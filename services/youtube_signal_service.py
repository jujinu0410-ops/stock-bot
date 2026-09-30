# -*- coding: utf-8 -*-
"""HTTP service for Google-Sheet-triggered YouTube candidate confirmation.

Flow:
  Apps Script BUY_CANDIDATE -> this endpoint -> Kiwoom ka10059 -> Gmail.

The service is deliberately narrow:
- it never scans the whole candidate universe,
- it never writes Google Sheets,
- it never mutates the portfolio,
- it never calls an order API.

Cloud Run must egress through a static public IP registered with Kiwoom.
"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
import os
import re
import time
from typing import Any, Dict

from flask import Flask, jsonify, request
import requests

from config.settings import GMAIL_APP_PASSWORD, GMAIL_USER, KIWOOM_APP_KEY, KIWOOM_APP_SECRET, RECIPIENT_EMAIL
from src.analysis.youtube_candidate_review import describe_flow
from src.analysis.youtube_candidate_signal import (
    FINAL_BUY_ALERT,
    FINAL_BUY_ALERT_STRONG,
    FINAL_WATCH_ONLY,
    FLOW_UNAVAILABLE,
    TECH_BUY_CANDIDATE,
    KiwoomInvestorFlowReader,
    TechnicalAssessment,
    combine_final_signal,
    evaluate_flow,
)
from src.notifications.gmail_notifier import GmailNotifier

app = Flask(__name__)

KIWOOM_BASE_URL = "https://api.kiwoom.com"
ALLOWED_FINAL_SIGNALS = {FINAL_WATCH_ONLY, FINAL_BUY_ALERT, FINAL_BUY_ALERT_STRONG}
SIGNAL_LABELS = {
    FINAL_WATCH_ONLY: "관찰",
    FINAL_BUY_ALERT: "매수검토",
    FINAL_BUY_ALERT_STRONG: "강한매수검토",
}


class SignalServiceError(RuntimeError):
    pass


class RetryingKiwoomSession:
    """requests.Session wrapper with small 429 backoff for Kiwoom calls."""

    def __init__(self, max_attempts: int = 5):
        self._session = requests.Session()
        self.max_attempts = max(1, int(max_attempts))
        self.retry_count = 0

    @staticmethod
    def _retry_after(response: requests.Response, attempt: int) -> float:
        raw = str(response.headers.get("Retry-After") or "").strip()
        if raw:
            try:
                return max(0.2, min(float(raw), 15.0))
            except ValueError:
                pass
        return float(min(2 ** max(0, attempt - 1), 8))

    def post(self, *args: Any, **kwargs: Any) -> requests.Response:
        last: requests.Response | None = None
        for attempt in range(1, self.max_attempts + 1):
            last = self._session.post(*args, **kwargs)
            if last.status_code != 429:
                return last
            if attempt >= self.max_attempts:
                return last
            self.retry_count += 1
            time.sleep(self._retry_after(last, attempt))
        assert last is not None
        return last


def _secret_ok() -> bool:
    expected = str(os.getenv("YOUTUBE_SIGNAL_API_TOKEN") or "").strip()
    supplied = str(request.headers.get("X-StockBot-Token") or "").strip()
    return bool(expected) and supplied == expected


def _clean_ticker(value: Any) -> str:
    ticker = str(value or "").replace("A", "").strip()
    if not re.fullmatch(r"\d{6}", ticker):
        raise SignalServiceError("INVALID_TICKER")
    return ticker


def _as_float(payload: Dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_kiwoom_token(session: RetryingKiwoomSession) -> str:
    app_key = str(KIWOOM_APP_KEY or os.getenv("KIWOOM_APP_KEY") or "").strip()
    app_secret = str(KIWOOM_APP_SECRET or os.getenv("KIWOOM_APP_SECRET") or "").strip()
    if not app_key or app_key == "YOUR_KIWOOM_APP_KEY_HERE" or not app_secret or app_secret == "YOUR_KIWOOM_APP_SECRET_HERE":
        raise SignalServiceError("KIWOOM_CREDENTIALS_MISSING")

    res = session.post(
        f"{KIWOOM_BASE_URL}/oauth2/token",
        headers={"content-type": "application/json"},
        json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret},
        timeout=10,
    )
    if res.status_code != 200:
        raise SignalServiceError(f"KIWOOM_TOKEN_HTTP_{res.status_code}")

    data = res.json()
    token = data.get("token") or data.get("access_token")
    code = str(data.get("return_code", "0"))
    if not token or code not in ("0", "None"):
        msg = str(data.get("return_msg") or "")
        if "8050" in msg or "IP" in msg.upper():
            raise SignalServiceError(f"KIWOOM_IP_BLOCKED: {msg}")
        raise SignalServiceError(f"KIWOOM_TOKEN_ERROR: {code} {msg}")
    return str(token).strip()


def _email_html(payload: Dict[str, Any], final_signal: str, flow: Any, flow_text: str) -> str:
    name = escape(str(payload.get("name") or payload.get("ticker") or ""))
    ticker = escape(str(payload.get("ticker") or ""))
    current = _as_float(payload, "current_price")
    trigger05 = _as_float(payload, "buy_trigger_05")
    trigger06 = _as_float(payload, "confirm_trigger_06")
    label = SIGNAL_LABELS.get(final_signal, final_signal)

    def n(value: float | None) -> str:
        return "-" if value is None else f"{value:,.0f}"

    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"></head>
<body style=\"font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#f8fafc;margin:0;padding:18px;color:#0f172a\">
<div style=\"max-width:640px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:18px\">
  <div style=\"font-size:18px;font-weight:700;margin-bottom:12px\">YouTube 언급종목 {escape(label)} 알림</div>
  <div style=\"font-size:15px;font-weight:700;margin-bottom:10px\">{name} {ticker}</div>
  <div style=\"font-size:13px;line-height:1.75;color:#334155\">
    기술신호: <b>BUY_CANDIDATE</b><br>
    현재가: <b>{n(current)}</b> / 0.5ATR: {n(trigger05)} / 0.6ATR: {n(trigger06)}<br>
    수급판정: <b>{escape(str(flow.status))}</b><br>
    {escape(flow_text)}<br>
    외국인 5일: {flow.foreign_5d:+,.0f} / 기관 5일: {flow.institution_5d:+,.0f}<br>
    합산 5일: {flow.combined_5d:+,.0f} / 합산 20일: {flow.combined_20d:+,.0f}
  </div>
  <div style=\"margin-top:16px;padding-top:10px;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b\">Google Sheet 기술신호 → Kiwoom 정확수급 확인 · 자동주문 없음 · 최종 판단은 사용자가 직접 수행</div>
</div>
</body></html>"""


def _confirm(payload: Dict[str, Any]) -> Dict[str, Any]:
    ticker = _clean_ticker(payload.get("ticker"))
    name = str(payload.get("name") or ticker).strip()
    tech_status = str(payload.get("tech_status") or "").strip().upper()
    if tech_status != TECH_BUY_CANDIDATE:
        raise SignalServiceError(f"TECH_STATUS_NOT_ELIGIBLE:{tech_status or 'EMPTY'}")

    as_of_text = str(payload.get("as_of_date") or date.today().isoformat()).strip()
    try:
        as_of = date.fromisoformat(as_of_text[:10])
    except ValueError as exc:
        raise SignalServiceError("INVALID_AS_OF_DATE") from exc

    session = RetryingKiwoomSession(max_attempts=5)
    token = _get_kiwoom_token(session)
    rows = KiwoomInvestorFlowReader(token, session=session).fetch_stock_flow(ticker, as_of, max_pages=5)
    flow = evaluate_flow(rows, source_verified=True)
    if flow.status == FLOW_UNAVAILABLE:
        raise SignalServiceError(flow.reason)

    tech = TechnicalAssessment(
        status=TECH_BUY_CANDIDATE,
        rebound_atr=None,
        early_trigger_04=None,
        buy_trigger_05=_as_float(payload, "buy_trigger_05"),
        confirm_trigger_06=_as_float(payload, "confirm_trigger_06"),
        confirmed_06=False,
        overheat=False,
        reason="BUY_CANDIDATE supplied by Google Sheet monitor",
    )
    final = combine_final_signal(tech, flow)
    flow_text = describe_flow({
        "status": flow.status,
        "foreign_5d": flow.foreign_5d,
        "institution_5d": flow.institution_5d,
        "combined_5d": flow.combined_5d,
        "combined_20d": flow.combined_20d,
    })

    if final.final_signal not in ALLOWED_FINAL_SIGNALS:
        raise SignalServiceError(f"FINAL_SIGNAL_NOT_MAIL_WORTHY:{final.final_signal}")

    notifier = GmailNotifier(sender_email=GMAIL_USER, app_password=GMAIL_APP_PASSWORD)
    if RECIPIENT_EMAIL:
        notifier.recipient_email = RECIPIENT_EMAIL
    subject = f"[YouTube 매수감시][{SIGNAL_LABELS[final.final_signal]}] {name} {ticker} · {as_of.isoformat()}"
    sent = notifier.send_email(subject, _email_html(payload, final.final_signal, flow, flow_text), attachments=None)
    if not sent:
        raise SignalServiceError("GMAIL_SEND_FAILED")

    return {
        "ok": True,
        "ticker": ticker,
        "name": name,
        "trigger_key": str(payload.get("trigger_key") or ""),
        "final_signal": final.final_signal,
        "flow_status": flow.status,
        "flow_summary": flow_text,
        "foreign_5d": flow.foreign_5d,
        "institution_5d": flow.institution_5d,
        "combined_5d": flow.combined_5d,
        "combined_20d": flow.combined_20d,
        "flow_strength_5d": flow.flow_strength_5d,
        "kiwoom_429_retry_count": session.retry_count,
        "mail_sent": True,
        "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
    }


@app.get("/health")
def health() -> Any:
    return jsonify({"ok": True, "service": "youtube-signal-service"})


@app.post("/confirm")
def confirm() -> Any:
    if not _secret_ok():
        return jsonify({"ok": False, "error": "UNAUTHORIZED"}), 401
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "INVALID_JSON"}), 400
    try:
        return jsonify(_confirm(payload)), 200
    except SignalServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    except Exception as exc:  # keep response terse; Cloud Logging has the traceback
        app.logger.exception("youtube signal confirmation failed")
        return jsonify({"ok": False, "error": f"UNEXPECTED:{type(exc).__name__}"}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
