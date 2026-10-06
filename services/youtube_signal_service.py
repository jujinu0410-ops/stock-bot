# -*- coding: utf-8 -*-
"""HTTP service shared by YouTube candidate and held-position alerts.

Flows:
  YouTube BUY_CANDIDATE -> /confirm -> Kiwoom ka10059 -> DART/news context -> Gmail
  Held-position state change -> /held-context -> Kiwoom ka10059 -> DART/news context -> JSON

Safety:
- no Google Sheets writes
- no portfolio mutation
- no order API
- Cloud Run must egress through the existing static public IP registered with Kiwoom.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape
import os
import re
import time
from typing import Any, Dict, List
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

from flask import Flask, jsonify, request
import requests

from config.settings import (
    GMAIL_APP_PASSWORD,
    GMAIL_USER,
    KIWOOM_APP_KEY,
    KIWOOM_APP_SECRET,
    RECIPIENT_EMAIL,
)
from src.analysis.bollinger_atr_strategy import get_strictly_sliced_45m_df
from src.analysis.jev_alert_gate import evaluate_jev_alert_gate
from src.runtime.krx_calendar import KRXCalendar
from src.analysis.market_catalyst_context import assess_catalyst, plain_text
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
from src.api.dart_api import DartAPIClient
from src.notifications.gmail_notifier import GmailNotifier

app = Flask(__name__)

KIWOOM_BASE_URL = "https://api.kiwoom.com"
ALLOWED_FINAL_SIGNALS = {FINAL_WATCH_ONLY, FINAL_BUY_ALERT, FINAL_BUY_ALERT_STRONG}
# User-facing language is shared with the held-position monitor:
# ▲ confirmed upward/buy condition, △ early upward/buy condition, · neutral watch.
# Internal final-signal codes remain unchanged for compatibility.
SIGNAL_LABELS = {
    FINAL_WATCH_ONLY: "· 관찰",
    FINAL_BUY_ALERT: "△ 매수조짐",
    FINAL_BUY_ALERT_STRONG: "▲ 매수확인",
}
SIGNAL_COLORS = {
    FINAL_WATCH_ONLY: "#64748B",
    FINAL_BUY_ALERT: "#DC2626",
    FINAL_BUY_ALERT_STRONG: "#DC2626",
}
_DART = DartAPIClient()


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


def _parse_as_of(payload: Dict[str, Any]) -> date:
    as_of_text = str(payload.get("as_of_date") or date.today().isoformat()).strip()
    try:
        return date.fromisoformat(as_of_text[:10])
    except ValueError as exc:
        raise SignalServiceError("INVALID_AS_OF_DATE") from exc



def _fetch_daily_obv_gate(ticker: str) -> Dict[str, Any]:
    """Daily OBV vs OBV9 timing state from the same Naver daily chart source."""
    url = (
        "https://fchart.stock.naver.com/sise.nhn"
        f"?symbol={ticker}&timeframe=day&count=40&requestType=0"
    )
    try:
        res = requests.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code != 200:
            return {"available": False, "reason": f"HTTP_{res.status_code}"}
        text = res.content.decode("cp949", errors="replace")
        if text.lstrip().startswith("<?xml") and "?>" in text:
            text = text[text.find("?>") + 2:]
        root = ET.fromstring(text)
        bars = []
        for item in root.iter("item"):
            parts = str(item.attrib.get("data") or "").split("|")
            if len(parts) < 6:
                continue
            try:
                bars.append({"close": float(parts[4]), "volume": float(parts[5])})
            except (TypeError, ValueError):
                continue
        if len(bars) < 10:
            return {"available": False, "reason": f"INSUFFICIENT_BARS_{len(bars)}"}
        obv = []
        running = 0.0
        for i, bar in enumerate(bars):
            if i > 0:
                if bar["close"] > bars[i - 1]["close"]:
                    running += bar["volume"]
                elif bar["close"] < bars[i - 1]["close"]:
                    running -= bar["volume"]
            obv.append(running)
        obv9 = sum(obv[-9:]) / 9.0
        current = obv[-1]
        prev = obv[-2]
        return {
            "available": True,
            "obv": current,
            "obv9": obv9,
            "gold": current > obv9,
            "trend_down": current < prev,
        }
    except Exception as exc:
        app.logger.warning("daily OBV gate failed ticker=%s error=%s", ticker, exc)
        return {"available": False, "reason": type(exc).__name__}


def _fetch_45m_vwap_gate(ticker: str) -> Dict[str, Any]:
    """VWAP9/26 on the latest completed KRX 45m bar only."""
    try:
        now = datetime.now().astimezone()
        slot = KRXCalendar.get_completed_45m_bar(now)
        if not slot:
            return {"available": False, "reason": "NO_COMPLETED_45M_BAR"}
        cutoff = datetime.combine(
            now.date(),
            datetime.strptime(slot[2], "%H:%M").time(),
            tzinfo=now.tzinfo,
        )
        df_45m, source, quality = get_strictly_sliced_45m_df(ticker, cutoff)
        if df_45m is None or quality != "VALID" or len(df_45m) < 26:
            return {"available": False, "reason": quality, "source": source}
        typical_price = (df_45m["High"] + df_45m["Low"] + df_45m["Close"]) / 3.0
        tp_vol = typical_price * df_45m["Volume"]
        vol9 = df_45m["Volume"].rolling(9).sum()
        vol26 = df_45m["Volume"].rolling(26).sum()
        vwap9 = float((tp_vol.rolling(9).sum() / (vol9 + 1e-9)).iloc[-1])
        vwap26 = float((tp_vol.rolling(26).sum() / (vol26 + 1e-9)).iloc[-1])
        if not (vwap9 == vwap9 and vwap26 == vwap26):
            return {"available": False, "reason": "VWAP_NAN", "source": source}
        return {
            "available": True,
            "vwap9": vwap9,
            "vwap26": vwap26,
            "state": "VWAP_GOLD" if vwap9 > vwap26 else "VWAP_DEAD",
            "gold": vwap9 > vwap26,
            "completed_bar": slot[2],
            "source": source,
        }
    except Exception as exc:
        app.logger.warning("45m VWAP gate failed ticker=%s error=%s", ticker, exc)
        return {"available": False, "reason": type(exc).__name__}


def _evaluate_entry_timing_veto(ticker: str) -> Dict[str, Any]:
    daily = _fetch_daily_obv_gate(ticker)
    intraday = _fetch_45m_vwap_gate(ticker)
    veto = bool(
        daily.get("available")
        and intraday.get("available")
        and not daily.get("gold")
        and not intraday.get("gold")
    )
    return {
        "decision": "VETO" if veto else "PASS",
        "reason": (
            "DAILY_OBV_NOT_GOLD_AND_45M_VWAP_NOT_GOLD"
            if veto
            else "TIMING_GATE_PASS_OR_DATA_GAP"
        ),
        "daily_obv": daily,
        "vwap_45m": intraday,
    }

def _get_kiwoom_token(session: RetryingKiwoomSession) -> str:
    app_key = str(KIWOOM_APP_KEY or os.getenv("KIWOOM_APP_KEY") or "").strip()
    app_secret = str(KIWOOM_APP_SECRET or os.getenv("KIWOOM_APP_SECRET") or "").strip()
    if (
        not app_key
        or app_key == "YOUR_KIWOOM_APP_KEY_HERE"
        or not app_secret
        or app_secret == "YOUR_KIWOOM_APP_SECRET_HERE"
    ):
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


def _flow_context(ticker: str, as_of: date) -> tuple[Dict[str, Any], int]:
    session = RetryingKiwoomSession(max_attempts=5)
    token = _get_kiwoom_token(session)
    rows = KiwoomInvestorFlowReader(token, session=session).fetch_stock_flow(
        ticker, as_of, max_pages=5
    )
    flow = evaluate_flow(rows, source_verified=True)
    if flow.status == FLOW_UNAVAILABLE:
        raise SignalServiceError(flow.reason)

    flow_text = describe_flow(
        {
            "status": flow.status,
            "foreign_5d": flow.foreign_5d,
            "institution_5d": flow.institution_5d,
            "combined_5d": flow.combined_5d,
            "combined_20d": flow.combined_20d,
        }
    )
    return {
        "status": flow.status,
        "summary": flow_text,
        "foreign_5d": flow.foreign_5d,
        "institution_5d": flow.institution_5d,
        "combined_5d": flow.combined_5d,
        "combined_20d": flow.combined_20d,
        "flow_strength_5d": flow.flow_strength_5d,
        "_flow_obj": flow,
    }, session.retry_count


def _disclosure_context(ticker: str, name: str, as_of: date) -> List[Dict[str, Any]]:
    try:
        rows = _DART.get_recent_disclosures_briefing(
            [{"stock_code": ticker, "stock_name": name}],
            target_date=as_of.strftime("%Y%m%d"),
        )
    except Exception as exc:
        app.logger.warning("DART context failed %s %s: %s", ticker, name, exc)
        return []

    out: List[Dict[str, Any]] = []
    for row in rows[:5]:
        out.append(
            {
                "report_nm": str(row.get("report_nm") or ""),
                "rcept_no": str(row.get("rcept_no") or ""),
                "rcept_dt": str(row.get("rcept_dt") or ""),
                "link": str(row.get("link") or ""),
                "summary": plain_text(row.get("summary")),
                "impact": plain_text(row.get("impact")),
            }
        )
    return out


def _news_context(name: str, ticker: str, now: datetime, limit: int = 5) -> List[Dict[str, Any]]:
    """Best-effort Google News RSS lookup. Failure must never block an alert."""
    if not name:
        return []
    query = quote_plus(f'"{name}"')
    url = "https://news.google.com/rss/search?" + f"q={query}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        res = requests.get(
            url,
            timeout=5,
            headers={"User-Agent": "StockBot/1.0 held-context"},
        )
        if res.status_code != 200:
            return []
        root = ET.fromstring(res.content)
    except Exception as exc:
        app.logger.warning("news RSS failed %s %s: %s", ticker, name, exc)
        return []

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=36)
    items: List[Dict[str, Any]] = []

    for item in root.findall(".//item"):
        title = plain_text(item.findtext("title"))
        link = str(item.findtext("link") or "").strip()
        source_el = item.find("source")
        source = plain_text(source_el.text if source_el is not None else "")
        pub_raw = str(item.findtext("pubDate") or "").strip()
        published: datetime | None = None
        if pub_raw:
            try:
                published = parsedate_to_datetime(pub_raw)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except Exception:
                published = None

        if published and published.astimezone(timezone.utc) < cutoff:
            continue

        items.append(
            {
                "title": title,
                "link": link,
                "source": source,
                "published_at": published.isoformat(timespec="seconds") if published else "",
            }
        )
        if len(items) >= limit:
            break
    return items


def _collect_market_context(
    ticker: str,
    name: str,
    as_of: date,
    now: datetime | None = None,
    include_flow: bool = True,
) -> Dict[str, Any]:
    now = now or datetime.now().astimezone()
    errors: List[str] = []

    flow: Dict[str, Any] = {
        "status": "UNAVAILABLE",
        "summary": "수급 확인 불가",
        "foreign_5d": None,
        "institution_5d": None,
        "combined_5d": None,
        "combined_20d": None,
        "flow_strength_5d": None,
    }
    retry_count = 0
    if include_flow:
        try:
            flow, retry_count = _flow_context(ticker, as_of)
        except Exception as exc:
            errors.append(f"KIWOOM:{type(exc).__name__}:{exc}")

    disclosures = _disclosure_context(ticker, name, as_of)
    news = _news_context(name, ticker, now)
    catalyst = assess_catalyst(disclosures, news, now=now)

    flow.pop("_flow_obj", None)
    return {
        "flow": flow,
        "disclosures": disclosures,
        "news": news,
        "catalyst": catalyst,
        "kiwoom_429_retry_count": retry_count,
        "errors": errors,
    }


def _context_html(context: Dict[str, Any]) -> str:
    catalyst = context.get("catalyst") or {}
    flow = context.get("flow") or {}
    disclosures = context.get("disclosures") or []
    news = context.get("news") or []

    def num(v: Any) -> str:
        try:
            return f"{float(v):+,.0f}"
        except (TypeError, ValueError):
            return "-"

    rows = [
        '<div style="margin-top:14px;padding-top:12px;border-top:1px solid #e2e8f0">',
        '<div style="font-size:14px;font-weight:700;margin-bottom:6px">움직임 원인 확인</div>',
        f'<div style="font-size:13px;line-height:1.7">촉매판정: <b>{escape(str(catalyst.get("label") or "확인된 촉매 없음"))}</b><br>',
        f'{escape(str(catalyst.get("reason") or ""))}<br>',
        f'수급: <b>{escape(str(flow.get("status") or "UNAVAILABLE"))}</b> · '
        f'외국인5일 {num(flow.get("foreign_5d"))} / 기관5일 {num(flow.get("institution_5d"))}</div>',
    ]
    if disclosures:
        rows.append('<div style="margin-top:8px;font-size:12px"><b>DART</b><br>')
        for d in disclosures[:3]:
            rows.append(
                "• "
                + escape(str(d.get("report_nm") or ""))
                + " — "
                + escape(str(d.get("impact") or d.get("summary") or ""))
                + "<br>"
            )
        rows.append("</div>")
    else:
        rows.append('<div style="margin-top:8px;font-size:12px"><b>DART</b>: 당일 주요 신규 공시 없음</div>')

    if news:
        rows.append('<div style="margin-top:8px;font-size:12px"><b>최근 뉴스</b><br>')
        for n in news[:3]:
            rows.append(
                "• "
                + escape(str(n.get("title") or ""))
                + (" · " + escape(str(n.get("source") or "")) if n.get("source") else "")
                + "<br>"
            )
        rows.append("</div>")
    else:
        rows.append('<div style="margin-top:8px;font-size:12px"><b>최근 뉴스</b>: 뚜렷한 관련 기사 미확인</div>')

    rows.append(
        '<div style="margin-top:6px;font-size:11px;color:#64748b">'
        "※ 뉴스·공시는 동시성/관련성을 보여주는 보조근거이며 주가 움직임의 인과관계를 단정하지 않습니다."
        "</div></div>"
    )
    return "".join(rows)


def _email_html(
    payload: Dict[str, Any],
    final_signal: str,
    flow: Any,
    flow_text: str,
    context: Dict[str, Any] | None = None,
) -> str:
    name = escape(str(payload.get("name") or payload.get("ticker") or ""))
    ticker = escape(str(payload.get("ticker") or ""))
    current = _as_float(payload, "current_price")
    trigger05 = _as_float(payload, "buy_trigger_05")
    trigger06 = _as_float(payload, "confirm_trigger_06")
    label = SIGNAL_LABELS.get(final_signal, final_signal)
    label_color = SIGNAL_COLORS.get(final_signal, "#334155")

    def n(value: float | None) -> str:
        return "-" if value is None else f"{value:,.0f}"

    context_block = _context_html(context or {}) if context else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#f8fafc;margin:0;padding:18px;color:#0f172a">
<div style="max-width:640px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:18px">
  <div style="font-size:18px;font-weight:700;margin-bottom:12px">YouTube 언급종목 <span style="color:{label_color};font-weight:800">{escape(label)}</span> 알림</div>
  <div style="font-size:15px;font-weight:700;margin-bottom:10px">{name} {ticker}</div>
  <div style="font-size:13px;line-height:1.75;color:#334155">
    기술신호: <b>BUY_CANDIDATE</b><br>
    현재가: <b>{n(current)}</b> / 0.5ATR: {n(trigger05)} / 0.6ATR: {n(trigger06)}<br>
    수급판정: <b>{escape(str(flow.status))}</b><br>
    {escape(flow_text)}<br>
    외국인 5일: {flow.foreign_5d:+,.0f} / 기관 5일: {flow.institution_5d:+,.0f}<br>
    합산 5일: {flow.combined_5d:+,.0f} / 합산 20일: {flow.combined_20d:+,.0f}
  </div>
  {context_block}
  <div style="margin-top:16px;padding-top:10px;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b">Google Sheet 기술신호 → Kiwoom 정확수급 + DART/뉴스 확인 · 자동주문 없음 · 최종 판단은 사용자가 직접 수행</div>
</div>
</body></html>"""


def _confirm(payload: Dict[str, Any]) -> Dict[str, Any]:
    ticker = _clean_ticker(payload.get("ticker"))
    name = str(payload.get("name") or ticker).strip()
    tech_status = str(payload.get("tech_status") or "").strip().upper()
    if tech_status != TECH_BUY_CANDIDATE:
        raise SignalServiceError(f"TECH_STATUS_NOT_ELIGIBLE:{tech_status or 'EMPTY'}")

    as_of = _parse_as_of(payload)

    session = RetryingKiwoomSession(max_attempts=5)
    token = _get_kiwoom_token(session)
    rows = KiwoomInvestorFlowReader(token, session=session).fetch_stock_flow(
        ticker, as_of, max_pages=5
    )
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
    timing_gate = None
    if final.final_signal in {FINAL_BUY_ALERT, FINAL_BUY_ALERT_STRONG}:
        timing_gate = _evaluate_entry_timing_veto(ticker)
        if timing_gate.get("decision") == "VETO":
            app.logger.info(
                "entry timing veto ticker=%s final=%s reason=%s",
                ticker, final.final_signal, timing_gate.get("reason"),
            )
            flow_text = describe_flow(
                {
                    "status": flow.status,
                    "foreign_5d": flow.foreign_5d,
                    "institution_5d": flow.institution_5d,
                    "combined_5d": flow.combined_5d,
                    "combined_20d": flow.combined_20d,
                }
            )
            return {
                "ok": True,
                "ticker": ticker,
                "name": name,
                "trigger_key": str(payload.get("trigger_key") or ""),
                "final_signal": FINAL_WATCH_ONLY,
                "original_final_signal": final.final_signal,
                "flow_status": flow.status,
                "flow_summary": flow_text,
                "timing_gate": timing_gate,
                "mail_sent": False,
                "suppressed_by_timing_gate": True,
                "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
            }
    flow_text = describe_flow(
        {
            "status": flow.status,
            "foreign_5d": flow.foreign_5d,
            "institution_5d": flow.institution_5d,
            "combined_5d": flow.combined_5d,
            "combined_20d": flow.combined_20d,
        }
    )

    if final.final_signal not in ALLOWED_FINAL_SIGNALS:
        raise SignalServiceError(f"FINAL_SIGNAL_NOT_MAIL_WORTHY:{final.final_signal}")

    context = _collect_market_context(
        ticker=ticker,
        name=name,
        as_of=as_of,
        include_flow=False,
    )
    context["flow"] = {
        "status": flow.status,
        "summary": flow_text,
        "foreign_5d": flow.foreign_5d,
        "institution_5d": flow.institution_5d,
        "combined_5d": flow.combined_5d,
        "combined_20d": flow.combined_20d,
        "flow_strength_5d": flow.flow_strength_5d,
    }
    context["kiwoom_429_retry_count"] = session.retry_count

    # Apps Script may already have run the cheap direct Jev PRE-GATE.
    # A decisive PROCEED does not pay for a second Jev call.  Uncertain or
    # missing PRE-GATE results keep the existing context-rich final Jev review.
    pre_gate = payload.get("jev_pre_gate") if isinstance(payload.get("jev_pre_gate"), dict) else {}
    pre_gate_ok = str(pre_gate.get("status") or "").upper() == "OK"
    pre_gate_decision = str(pre_gate.get("decision") or "").upper()
    needs_final_review = bool(pre_gate.get("needs_final_review", True))

    if pre_gate_ok and pre_gate_decision == "HOLD":
        # Defensive consistency: Apps Script should have stopped before /confirm,
        # but if a HOLD payload reaches here, do not run expensive final work/mail.
        return {
            "ok": True,
            "ticker": ticker,
            "name": name,
            "trigger_key": str(payload.get("trigger_key") or ""),
            "final_signal": FINAL_WATCH_ONLY,
            "flow_status": flow.status,
            "flow_summary": flow_text,
            "jev_pre_gate": pre_gate,
            "mail_sent": False,
            "suppressed_by_jev_pre_gate": True,
            "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
        }

    if pre_gate_ok and pre_gate_decision == "PROCEED" and not needs_final_review:
        jev_gate = {
            "ok": True,
            "status": "OK",
            "mode": "PRE_GATE",
            "decision": "SEND",
            "reason": "PRE_GATE_DECISIVE_PROCEED",
            "source": "YOUTUBE",
            "ticker": ticker,
            "event_keys": ["YOUTUBE_BUY_CANDIDATE", final.final_signal],
            "model": pre_gate.get("model") or "jev-latest",
            "answers": pre_gate.get("answers") or {},
            "usage": {},
        }
    else:
        jev_gate = _jev_gate(
            {
                **payload,
                "source": "YOUTUBE",
                "ticker": ticker,
                "name": name,
                "event_keys": ["YOUTUBE_BUY_CANDIDATE", final.final_signal],
                "event_level": "STRONG" if final.final_signal == FINAL_BUY_ALERT_STRONG else "WATCH",
                "current_price": payload.get("current_price"),
                "buy_trigger_05": payload.get("buy_trigger_05"),
                "confirm_trigger_06": payload.get("confirm_trigger_06"),
                "context": context,
            }
        )
    if jev_gate.get("decision") == "HOLD":
        app.logger.info(
            "Jev held YouTube candidate ticker=%s final=%s reason=%s",
            ticker, final.final_signal, jev_gate.get("reason"),
        )
        return {
            "ok": True,
            "ticker": ticker,
            "name": name,
            "trigger_key": str(payload.get("trigger_key") or ""),
            "final_signal": final.final_signal,
            "market_context": context,
            "jev_gate": jev_gate,
            "mail_sent": False,
            "suppressed_by_jev": True,
            "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
        }

    notifier = GmailNotifier(sender_email=GMAIL_USER, app_password=GMAIL_APP_PASSWORD)
    if RECIPIENT_EMAIL:
        notifier.recipient_email = RECIPIENT_EMAIL
    subject = (
        f"[YouTube 매수감시][{SIGNAL_LABELS[final.final_signal]}] "
        f"{name} {ticker} · {as_of.isoformat()}"
    )
    sent = notifier.send_email(
        subject,
        _email_html(payload, final.final_signal, flow, flow_text, context=context),
        attachments=None,
    )
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
        "market_context": context,
        "jev_pre_gate": pre_gate,
        "jev_gate": jev_gate,
        "kiwoom_429_retry_count": session.retry_count,
        "mail_sent": True,
        "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
    }


def _held_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    ticker = _clean_ticker(payload.get("ticker"))
    name = str(payload.get("name") or ticker).strip()
    as_of = _parse_as_of(payload)
    context = _collect_market_context(ticker, name, as_of, include_flow=True)
    return {
        "ok": True,
        "ticker": ticker,
        "name": name,
        "event_key": str(payload.get("event_key") or ""),
        "event_level": str(payload.get("event_level") or ""),
        "regime": str(payload.get("regime") or ""),
        "market_context": context,
        "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
    }


def _jev_gate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Shared, fail-open delivery gate for YouTube, held, and ETF alerts."""
    ticker = _clean_ticker(payload.get("ticker"))
    enriched = dict(payload)
    enriched["ticker"] = ticker
    enriched["name"] = str(payload.get("name") or ticker).strip()
    return evaluate_jev_alert_gate(enriched)


@app.get("/health")
def health() -> Any:
    return jsonify(
        {
            "ok": True,
            "service": "youtube-signal-service",
            "endpoints": ["/confirm", "/held-context", "/jev-gate"],
        }
    )


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
    except Exception as exc:
        app.logger.exception("youtube signal confirmation failed")
        return jsonify({"ok": False, "error": f"UNEXPECTED:{type(exc).__name__}"}), 500


@app.post("/held-context")
def held_context() -> Any:
    if not _secret_ok():
        return jsonify({"ok": False, "error": "UNAUTHORIZED"}), 401
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "INVALID_JSON"}), 400
    try:
        return jsonify(_held_context(payload)), 200
    except SignalServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("held context failed")
        return jsonify({"ok": False, "error": f"UNEXPECTED:{type(exc).__name__}"}), 500


@app.post("/jev-gate")
def jev_gate() -> Any:
    if not _secret_ok():
        return jsonify({"ok": False, "error": "UNAUTHORIZED"}), 401
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "INVALID_JSON"}), 400
    try:
        return jsonify(_jev_gate(payload)), 200
    except SignalServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("jev alert gate failed")
        return jsonify({"ok": False, "error": f"UNEXPECTED:{type(exc).__name__}"}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
