# -*- coding: utf-8 -*-
"""LIVE Gmail dispatcher for YouTube Candidate Watch.

This module intentionally sends email only. It never writes Google Sheets, mutates
portfolio state, or places orders.  It reuses the exact-flow JSON and review layer,
then dispatches user-facing mail for WATCH_ONLY / BUY_ALERT / BUY_ALERT_STRONG.

Duplicate policy is deliberately responsive rather than conservative:
- first occurrence: send,
- same ticker + same signal + same as-of date: suppress,
- signal change on the same date: send again,
- same signal on a new date: send again.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
import sys
from typing import Any, Dict, Mapping

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import GMAIL_USER, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL
from scripts.youtube_candidate_review_preview import latest_json, run as build_review
from src.notifications.gmail_notifier import GmailNotifier

DEFAULT_SOURCE_DB = Path(r"C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system\data\stock_system.db")
DEFAULT_STATE = BASE_DIR / "logs" / "youtube_candidate_live_mail_state.json"

LIVE_MAIL_SIGNALS = {"WATCH_ONLY", "BUY_ALERT", "BUY_ALERT_STRONG"}
LABELS = {
    "WATCH_ONLY": "관찰",
    "BUY_ALERT": "매수검토",
    "BUY_ALERT_STRONG": "강한매수검토",
}


def load_state(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = payload.get("tickers") if isinstance(payload, dict) else None
    return dict(rows) if isinstance(rows, dict) else {}


def save_state(path: Path, rows: Mapping[str, Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "tickers": dict(rows),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def decide_live_send(signal: str, ticker: str, as_of_date: str, state: Mapping[str, Mapping[str, str]]) -> tuple[bool, str]:
    if signal not in LIVE_MAIL_SIGNALS:
        return False, f"{signal} is not in LIVE mail states"
    previous = state.get(ticker) or {}
    if previous.get("signal") == signal and previous.get("as_of_date") == as_of_date:
        return False, "same ticker/signal already sent for this as-of date"
    if previous.get("as_of_date") == as_of_date and previous.get("signal") and previous.get("signal") != signal:
        return True, f"signal changed {previous.get('signal')} -> {signal} on same date"
    if previous.get("as_of_date") and previous.get("as_of_date") != as_of_date:
        return True, "new as-of date; daily live update"
    return True, "first live mail for ticker/state"


def _html(review: Mapping[str, Any], preview_text: str) -> str:
    signal = str(review.get("signal") or "")
    label = LABELS.get(signal, signal)
    title = f"YouTube 언급종목 {label} 알림"
    body = escape(preview_text).replace("\n", "<br>")
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"></head>
<body style=\"font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#f8fafc;margin:0;padding:18px;color:#0f172a\">
  <div style=\"max-width:640px;margin:0 auto;background:white;border:1px solid #e2e8f0;border-radius:10px;padding:18px\">
    <div style=\"font-size:17px;font-weight:700;margin-bottom:12px\">{escape(title)}</div>
    <div style=\"font-size:13px;line-height:1.75;color:#334155\">{body}</div>
    <div style=\"margin-top:16px;padding-top:10px;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b\">자동주문 없음 · 최종 매수 판단은 사용자가 직접 수행</div>
  </div>
</body></html>"""


def run(input_json: Path, db_path: Path, state_path: Path, *, with_market: bool, send: bool, force: bool = False) -> Dict[str, Any]:
    exact = json.loads(input_json.read_text(encoding="utf-8"))
    as_of_date = str(exact.get("as_of_date") or "UNKNOWN")
    review_result = build_review(input_json, db_path, with_market)
    state = load_state(state_path)

    notifier = GmailNotifier(sender_email=GMAIL_USER, app_password=GMAIL_APP_PASSWORD)
    if RECIPIENT_EMAIL:
        notifier.recipient_email = RECIPIENT_EMAIL

    dispatches = []
    send_failures = 0
    sent_count = 0

    for review in review_result.get("reviews") or []:
        ticker = str(review.get("ticker") or "")
        name = str(review.get("name") or ticker)
        signal = str(review.get("signal") or "NO_ALERT")
        allowed, reason = decide_live_send(signal, ticker, as_of_date, state)
        if force and signal in LIVE_MAIL_SIGNALS:
            allowed, reason = True, "force live send requested"

        preview = str(review.get("preview") or "")
        market_error = review.get("market_error")
        if market_error:
            preview += f"\n시장수급 참고: UNAVAILABLE ({market_error})"

        item = {
            "ticker": ticker,
            "name": name,
            "signal": signal,
            "as_of_date": as_of_date,
            "eligible": signal in LIVE_MAIL_SIGNALS,
            "send_allowed": allowed,
            "decision_reason": reason,
            "sent": False,
            "send_error": None,
        }

        if allowed:
            label = LABELS.get(signal, signal)
            subject = f"[YouTube 매수감시][{label}] {name} {ticker} · {as_of_date}"
            if send:
                ok = notifier.send_email(subject, _html(review, preview), attachments=None)
                item["sent"] = bool(ok)
                if ok:
                    sent_count += 1
                    state[ticker] = {
                        "signal": signal,
                        "as_of_date": as_of_date,
                        "sent_at": datetime.now().isoformat(timespec="seconds"),
                        "subject": subject,
                    }
                    save_state(state_path, state)
                else:
                    send_failures += 1
                    item["send_error"] = "GMAIL_SEND_FAILED"
            else:
                item["send_error"] = "SEND_FLAG_NOT_ENABLED"
        dispatches.append(item)

    return {
        "run_mode": "YOUTUBE_CANDIDATE_LIVE_EMAIL",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_json": str(input_json),
        "as_of_date": as_of_date,
        "recipient": RECIPIENT_EMAIL or GMAIL_USER,
        "send_enabled": send,
        "sent_count": sent_count,
        "send_failure_count": send_failures,
        "dispatches": dispatches,
        "market_regime_token_status": review_result.get("kiwoom_token_status"),
        "market_regime_429_retry_count": review_result.get("kiwoom_http_429_retry_count"),
        "safety": {
            "email_send": send,
            "sheet_write": False,
            "portfolio_mutation": False,
            "order_api": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json", type=Path, default=None)
    parser.add_argument("--log-dir", type=Path, default=BASE_DIR / "logs")
    parser.add_argument("--db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--with-market", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    input_json = args.input_json or latest_json(args.log_dir)
    result = run(input_json, args.db, args.state, with_market=args.with_market, send=args.send, force=args.force)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")

    if result["send_enabled"] and result["send_failure_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
