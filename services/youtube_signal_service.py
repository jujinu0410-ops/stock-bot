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
from zoneinfo import ZoneInfo
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
from src.analysis.youtube_alert_outcome import evaluate_youtube_alert_outcome
from src.analysis.daily_reversal_patterns import detect_daily_reversal_patterns
from src.analysis.youtube_candidate_signal import (
    FINAL_BUY_ALERT,
    FINAL_BUY_ALERT_STRONG,
    FINAL_WATCH_ONLY,
    FLOW_UNAVAILABLE,
    TECH_BUY_CANDIDATE,
    KiwoomInvestorFlowReader,
    FinalSignalAssessment,
    TechnicalAssessment,
    combine_final_signal,
    evaluate_flow,
)
from src.api.dart_api import DartAPIClient
from src.notifications.gmail_notifier import GmailNotifier

app = Flask(__name__)

KIWOOM_BASE_URL = "https://api.kiwoom.com"
ALLOWED_FINAL_SIGNALS = {FINAL_BUY_ALERT, FINAL_BUY_ALERT_STRONG}
FRESHNESS_DAMAGE_ATR = 0.40
FRESHNESS_SHARP_DROP_ATR = 0.20
STRONG_MAX_INTRADAY_DROP_PCT = -0.05
EARLY_ALERT_MAX_WEAK_DAY_PCT = -0.01
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


def _fetch_daily_reversal_pattern(ticker: str) -> Dict[str, Any]:
    """Completed-daily 3-candle reversal pattern context from Naver fchart."""
    url = (
        "https://fchart.stock.naver.com/sise.nhn"
        f"?symbol={ticker}&timeframe=day&count=90&requestType=0"
    )
    try:
        res = requests.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code != 200:
            return {"available": False, "reason": f"HTTP_{res.status_code}"}
        text = res.content.decode("cp949", errors="replace")
        if text.lstrip().startswith("<?xml") and "?>" in text:
            text = text[text.find("?>") + 2:]
        root = ET.fromstring(text)
        bars: List[Dict[str, Any]] = []
        now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
        for item in root.iter("item"):
            parts = str(item.attrib.get("data") or "").split("|")
            if len(parts) < 6:
                continue
            try:
                ymd = str(parts[0])
                bar_date = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
                # The video rules require a confirmed day-3 close.  Never use today's
                # still-forming daily candle before a conservative 16:00 KST cutoff.
                if bar_date == now_kst.date() and now_kst.hour < 16:
                    continue
                bars.append({
                    "date": bar_date.isoformat(),
                    "open": float(parts[1]),
                    "high": float(parts[2]),
                    "low": float(parts[3]),
                    "close": float(parts[4]),
                    "volume": float(parts[5]),
                })
            except (TypeError, ValueError):
                continue
        out = detect_daily_reversal_patterns(bars)
        out["source"] = "NAVER_FCHART_COMPLETED_DAILY"
        out["ticker"] = ticker
        return out
    except Exception as exc:
        app.logger.warning("daily reversal pattern failed ticker=%s error=%s", ticker, exc)
        return {"available": False, "reason": type(exc).__name__, "ticker": ticker}


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


def _analyze_45m_exit_risk_df(df_45m: Any, source: str = "") -> Dict[str, Any]:
    """Confirm persistent 45m weakness for a held-position sell-prep warning."""
    try:
        if df_45m is None or len(df_45m) < 35:
            return {"available": False, "reason": "INSUFFICIENT_45M_BARS", "source": source}

        work = df_45m.copy()
        close = work["Close"].astype(float)
        volume = work["Volume"].astype(float).fillna(0.0)

        delta = close.diff()
        signed_volume = volume * 0.0
        signed_volume = signed_volume.where(~(delta > 0), volume)
        signed_volume = signed_volume.where(~(delta < 0), -volume)
        obv = signed_volume.fillna(0.0).cumsum()
        obv9 = obv.rolling(9).mean()

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macd_signal = macd.ewm(span=9, adjust=False).mean()

        calc = work[["Close"]].copy()
        calc["obv"] = obv
        calc["obv9"] = obv9
        calc["macd"] = macd
        calc["macd_signal"] = macd_signal
        calc = calc.dropna(subset=["obv9", "macd", "macd_signal"])
        if len(calc) < 3:
            return {"available": False, "reason": "INSUFFICIENT_CALCULATED_45M_BARS", "source": source}

        session_last = calc.groupby(calc.index.date).tail(1)
        if len(session_last) < 2:
            return {"available": False, "reason": "NEED_TWO_TRADING_SESSIONS", "source": source}

        prev_session = session_last.iloc[-2]
        curr_session = session_last.iloc[-1]
        prev_date = session_last.index[-2].date().isoformat()
        curr_date = session_last.index[-1].date().isoformat()

        no_gold_two_sessions = bool(
            float(prev_session["obv"]) <= float(prev_session["obv9"])
            and float(curr_session["obv"]) <= float(curr_session["obv9"])
        )
        obv_session_falling = bool(
            float(curr_session["obv"]) < float(prev_session["obv"])
            and float(curr_session["obv9"]) < float(prev_session["obv9"])
        )

        recent = calc.tail(3)
        macd_values = [float(v) for v in recent["macd"].tolist()]
        macd_falling_3 = bool(macd_values[0] > macd_values[1] > macd_values[2])
        macd_below_signal = bool(float(recent["macd"].iloc[-1]) < float(recent["macd_signal"].iloc[-1]))

        warn = bool(
            no_gold_two_sessions
            and obv_session_falling
            and macd_falling_3
            and macd_below_signal
        )
        return {
            "available": True,
            "decision": "WARN" if warn else "PASS",
            "reason": (
                "PERSISTENT_45M_OBV_NO_GOLD_AND_MACD_FALLING"
                if warn else "45M_PERSISTENT_WEAKNESS_NOT_CONFIRMED"
            ),
            "source": source,
            "prev_session": prev_date,
            "curr_session": curr_date,
            "no_gold_two_sessions": no_gold_two_sessions,
            "obv_session_falling": obv_session_falling,
            "macd_falling_3": macd_falling_3,
            "macd_below_signal": macd_below_signal,
            "obv": float(curr_session["obv"]),
            "obv9": float(curr_session["obv9"]),
            "macd": float(recent["macd"].iloc[-1]),
            "macd_signal": float(recent["macd_signal"].iloc[-1]),
            "completed_bar": calc.index[-1].strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as exc:
        app.logger.warning("45m exit-risk analysis failed error=%s", exc)
        return {"available": False, "reason": type(exc).__name__, "source": source}


def _fetch_45m_exit_risk(ticker: str) -> Dict[str, Any]:
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
        if df_45m is None or quality != "VALID":
            return {"available": False, "reason": quality, "source": source}
        return _analyze_45m_exit_risk_df(df_45m, source)
    except Exception as exc:
        app.logger.warning("45m exit-risk fetch failed ticker=%s error=%s", ticker, exc)
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


def _apply_strong_confirmation_gate(
    final: FinalSignalAssessment,
    freshness_gate: Dict[str, Any],
    timing_gate: Dict[str, Any],
) -> tuple[FinalSignalAssessment, Dict[str, Any]]:
    """Keep ▲ BUY_ALERT_STRONG only when 45m trend confirms and no severe intraday drop remains."""
    if final.final_signal != FINAL_BUY_ALERT_STRONG:
        return final, {
            "decision": "NOT_APPLICABLE",
            "reason": "FINAL_SIGNAL_NOT_STRONG",
        }

    reasons: List[str] = []
    vwap_45m = timing_gate.get("vwap_45m") or {}
    if not vwap_45m.get("available"):
        reasons.append("45M_VWAP_UNAVAILABLE")
    elif not vwap_45m.get("gold"):
        reasons.append("45M_VWAP_NOT_GOLD")

    quote = freshness_gate.get("quote") or {}
    fresh_price = _parse_kiwoom_price(freshness_gate.get("fresh_price"))
    open_price = _parse_kiwoom_price(quote.get("open"))
    intraday_change_pct = None
    if fresh_price and open_price:
        intraday_change_pct = (fresh_price - open_price) / open_price
        if intraday_change_pct <= STRONG_MAX_INTRADAY_DROP_PCT:
            reasons.append("INTRADAY_DROP_GE_5PCT")

    if reasons:
        downgraded = FinalSignalAssessment(
            FINAL_BUY_ALERT,
            final.tech_status,
            final.flow_status,
            final.market_regime,
            final.reason + "; strong confirmation downgraded: " + ",".join(reasons),
        )
        return downgraded, {
            "decision": "DOWNGRADE_TO_BUY_ALERT",
            "reason": "|".join(reasons),
            "intraday_change_pct": intraday_change_pct,
            "vwap_45m": vwap_45m,
        }

    return final, {
        "decision": "PASS_STRONG",
        "reason": "CONFIRMED_06_AND_45M_UPTREND",
        "intraday_change_pct": intraday_change_pct,
        "vwap_45m": vwap_45m,
    }


def _apply_daily_pattern_signal_modifier(
    final: FinalSignalAssessment,
    daily_pattern: Dict[str, Any],
    tech: TechnicalAssessment,
    freshness_gate: Dict[str, Any],
    timing_gate: Dict[str, Any],
) -> tuple[FinalSignalAssessment, Dict[str, Any]]:
    """Let completed daily reversal patterns directly move an existing signal by one step.

    Hard guards are never bypassed. A bullish pattern may upgrade △ to ▲ only
    when +0.6 ATR is already confirmed, flow is NEUTRAL (not negative), and the
    completed 45m VWAP trend is available and bullish. A bearish pattern can
    downgrade ▲→△ or △→WATCH_ONLY.
    """
    if not daily_pattern.get("available"):
        return final, {"decision": "NO_EFFECT", "reason": "PATTERN_UNAVAILABLE"}

    bias = str(daily_pattern.get("bias") or "NONE").upper()
    labels = list(daily_pattern.get("labels") or [])
    if bias == "BEAR":
        if final.final_signal == FINAL_BUY_ALERT_STRONG:
            changed = FinalSignalAssessment(
                FINAL_BUY_ALERT,
                final.tech_status,
                final.flow_status,
                final.market_regime,
                final.reason + "; daily bearish reversal pattern downgraded strong buy alert",
            )
            return changed, {
                "decision": "DOWNGRADE",
                "reason": "DAILY_BEAR_PATTERN_STRONG_TO_EARLY",
                "bias": bias,
                "labels": labels,
            }
        if final.final_signal == FINAL_BUY_ALERT:
            changed = FinalSignalAssessment(
                FINAL_WATCH_ONLY,
                final.tech_status,
                final.flow_status,
                final.market_regime,
                final.reason + "; daily bearish reversal pattern downgraded buy alert to watch",
            )
            return changed, {
                "decision": "DOWNGRADE",
                "reason": "DAILY_BEAR_PATTERN_EARLY_TO_WATCH",
                "bias": bias,
                "labels": labels,
            }

    if (
        bias == "BULL"
        and final.final_signal == FINAL_BUY_ALERT
        and tech.confirmed_06
        and final.flow_status == "NEUTRAL"
    ):
        vwap_45m = timing_gate.get("vwap_45m") or {}
        quote = freshness_gate.get("quote") or {}
        fresh_price = _parse_kiwoom_price(freshness_gate.get("fresh_price"))
        open_price = _parse_kiwoom_price(quote.get("open"))
        intraday_change_pct = (
            (fresh_price - open_price) / open_price
            if fresh_price and open_price else None
        )
        strong_day_ok = (
            intraday_change_pct is None
            or intraday_change_pct > STRONG_MAX_INTRADAY_DROP_PCT
        )
        if (
            freshness_gate.get("decision") == "PASS"
            and vwap_45m.get("available")
            and vwap_45m.get("gold")
            and strong_day_ok
        ):
            changed = FinalSignalAssessment(
                FINAL_BUY_ALERT_STRONG,
                final.tech_status,
                final.flow_status,
                final.market_regime,
                final.reason + "; daily bullish reversal pattern + 0.6ATR + 45m uptrend upgraded signal",
            )
            return changed, {
                "decision": "UPGRADE",
                "reason": "DAILY_BULL_PATTERN_NEUTRAL_FLOW_TO_STRONG",
                "bias": bias,
                "labels": labels,
            }

    return final, {
        "decision": "NO_EFFECT",
        "reason": "PATTERN_CONDITIONS_NOT_MET",
        "bias": bias,
        "labels": labels,
    }


def _parse_kiwoom_price(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = abs(float(str(value).strip().replace(",", "").replace("+", "")))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_kiwoom_signed_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip().replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _evaluate_early_alert_safety_gate(
    final: FinalSignalAssessment,
    freshness_gate: Dict[str, Any],
    timing_gate: Dict[str, Any],
) -> Dict[str, Any]:
    """Suppress △ when 45m trend is unavailable and the live day is already weak."""
    if final.final_signal != FINAL_BUY_ALERT:
        return {"decision": "NOT_APPLICABLE", "reason": "FINAL_SIGNAL_NOT_EARLY"}

    vwap_45m = timing_gate.get("vwap_45m") or {}
    if vwap_45m.get("available"):
        return {"decision": "PASS", "reason": "45M_AVAILABLE"}

    change_pct = freshness_gate.get("change_pct")
    if change_pct is None:
        quote = freshness_gate.get("quote") or {}
        change_pct = quote.get("change_pct")

    if change_pct is not None and float(change_pct) <= EARLY_ALERT_MAX_WEAK_DAY_PCT:
        return {
            "decision": "VETO",
            "reason": "45M_UNAVAILABLE_AND_DAY_BELOW_MINUS_1PCT",
            "change_pct": float(change_pct),
        }

    return {
        "decision": "PASS",
        "reason": "45M_UNAVAILABLE_BUT_DAY_NOT_TOO_WEAK",
        "change_pct": change_pct,
    }


def _fetch_kiwoom_quote(
    session: RetryingKiwoomSession,
    token: str,
    ticker: str,
) -> Dict[str, Any]:
    response = session.post(
        f"{KIWOOM_BASE_URL}/api/dostk/stkinfo",
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10001",
        },
        json={"stk_cd": ticker},
        timeout=10,
    )
    if response.status_code != 200:
        return {"available": False, "reason": f"KA10001_HTTP_{response.status_code}"}
    payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("return_code", "0")) not in ("0", "None"):
        return {"available": False, "reason": "KA10001_BUSINESS_ERROR"}
    current = _parse_kiwoom_price(payload.get("cur_prc"))
    if current is None:
        return {"available": False, "reason": "KA10001_CURRENT_PRICE_MISSING"}
    change_pct_raw = _parse_kiwoom_signed_number(payload.get("flu_rt"))
    change_amount = _parse_kiwoom_signed_number(payload.get("pred_pre"))
    change_pct = change_pct_raw / 100.0 if change_pct_raw is not None else None
    if change_pct is None and change_amount is not None:
        prev_close = current - change_amount
        if prev_close > 0:
            change_pct = (current / prev_close) - 1.0
    return {
        "available": True,
        "current_price": current,
        "change_pct": change_pct,
        "open": _parse_kiwoom_price(payload.get("open_pric")),
        "high": _parse_kiwoom_price(payload.get("high_pric")),
        "low": _parse_kiwoom_price(payload.get("low_pric")),
        "source": "KIWOOM_KA10001",
    }


def _fetch_kiwoom_minute_bars(
    session: RetryingKiwoomSession,
    token: str,
    ticker: str,
    limit: int = 10,
    base_date: date | None = None,
) -> Dict[str, Any]:
    response = session.post(
        f"{KIWOOM_BASE_URL}/api/dostk/chart",
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10080",
        },
        json={
            "stk_cd": ticker,
            "tic_scope": "1",
            "upd_stkpc_tp": "1",
            "base_dt": (base_date or date.today()).strftime("%Y%m%d"),
        },
        timeout=10,
    )
    if response.status_code != 200:
        return {"available": False, "reason": f"KA10080_HTTP_{response.status_code}", "bars": []}
    payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("return_code", "0")) not in ("0", "None"):
        return {"available": False, "reason": "KA10080_BUSINESS_ERROR", "bars": []}

    raw_rows = payload.get("stk_min_pole_chart_qry") or []
    bars: List[Dict[str, Any]] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        ts = str(row.get("cntr_tm") or "").strip()
        close = _parse_kiwoom_price(row.get("cur_prc"))
        if not ts or close is None:
            continue
        volume = _parse_kiwoom_price(row.get("trde_qty")) or 0.0
        bars.append(
            {
                "time": ts,
                "close": close,
                "open": _parse_kiwoom_price(row.get("open_pric")) or close,
                "high": _parse_kiwoom_price(row.get("high_pric")) or close,
                "low": _parse_kiwoom_price(row.get("low_pric")) or close,
                "volume": volume,
            }
        )
    dedup = {str(row["time"]): row for row in bars}
    ordered = [dedup[key] for key in sorted(dedup)]
    if len(ordered) < 4:
        return {
            "available": False,
            "reason": f"KA10080_INSUFFICIENT_BARS_{len(ordered)}",
            "bars": ordered,
        }
    keep = max(4, int(limit or 10))
    return {"available": True, "reason": "OK", "bars": ordered[-keep:], "source": "KIWOOM_KA10080"}


_KST = ZoneInfo("Asia/Seoul")


def _parse_alert_time(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise SignalServiceError("ALERT_TIME_MISSING")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SignalServiceError("ALERT_TIME_INVALID") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_KST)
    return parsed.astimezone(_KST)


def _minute_bar_datetime(raw_time: Any, alert_time: datetime) -> datetime | None:
    digits = re.sub(r"\D", "", str(raw_time or ""))
    try:
        if len(digits) == 14:
            return datetime.strptime(digits, "%Y%m%d%H%M%S").replace(tzinfo=_KST)
        if len(digits) == 12:
            return datetime.strptime(digits, "%Y%m%d%H%M").replace(tzinfo=_KST)
        if len(digits) == 6:
            t = datetime.strptime(digits, "%H%M%S").time()
            return datetime.combine(alert_time.date(), t, tzinfo=_KST)
        if len(digits) == 4:
            t = datetime.strptime(digits, "%H%M").time()
            return datetime.combine(alert_time.date(), t, tzinfo=_KST)
    except ValueError:
        return None
    return None


def _build_alert_outcome_bars(
    raw_bars: List[Dict[str, Any]],
    alert_time: datetime,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in raw_bars:
        bar_time = _minute_bar_datetime(row.get("time"), alert_time)
        if bar_time is None:
            continue
        seconds = (bar_time - alert_time).total_seconds()
        if seconds < 0:
            continue
        minutes_after = int(seconds // 60)
        if minutes_after > 90:
            continue
        try:
            out.append(
                {
                    "minutes_after_alert": minutes_after,
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["minutes_after_alert"])
    return out


def _evaluate_alert_outcome(payload: Dict[str, Any]) -> Dict[str, Any]:
    ticker = _clean_ticker(payload.get("ticker"))
    alert_time = _parse_alert_time(payload.get("alert_time"))
    alert_price = _as_float(payload, "alert_price")
    atr14 = _as_float(payload, "atr14")
    if alert_price is None or alert_price <= 0:
        raise SignalServiceError("ALERT_PRICE_INVALID")
    if atr14 is None or atr14 <= 0:
        raise SignalServiceError("ATR14_INVALID")

    session = RetryingKiwoomSession(max_attempts=5)
    token = _get_kiwoom_token(session)
    minute = _fetch_kiwoom_minute_bars(
        session,
        token,
        ticker,
        limit=180,
        base_date=alert_time.date(),
    )
    if not minute.get("available"):
        return {
            "ok": True,
            "ticker": ticker,
            "status": "DATA_UNAVAILABLE",
            "grade": None,
            "label": str(minute.get("reason") or "MINUTE_DATA_UNAVAILABLE"),
            "alert_time": alert_time.isoformat(timespec="seconds"),
            "kiwoom_429_retry_count": session.retry_count,
        }

    bars = _build_alert_outcome_bars(list(minute.get("bars") or []), alert_time)
    outcome = evaluate_youtube_alert_outcome(
        alert_price=float(alert_price),
        atr14=float(atr14),
        bars=bars,
    )
    return {
        "ok": True,
        "ticker": ticker,
        "alert_time": alert_time.isoformat(timespec="seconds"),
        "minute_source": minute.get("source"),
        "kiwoom_429_retry_count": session.retry_count,
        **outcome,
    }


def _evaluate_kiwoom_freshness_gate(
    ticker: str,
    *,
    signal_price: float | None,
    atr14: float | None,
    buy_trigger_05: float | None,
    session: RetryingKiwoomSession,
    token: str,
    overheat_upper: float | None = None,
) -> Dict[str, Any]:
    quote = _fetch_kiwoom_quote(session, token, ticker)
    minute = _fetch_kiwoom_minute_bars(session, token, ticker)

    if signal_price is None or signal_price <= 0 or atr14 is None or atr14 <= 0:
        return {
            "decision": "VETO",
            "reason": "FRESHNESS_INPUT_MISSING",
            "quote": quote,
            "minute": minute,
        }
    if not quote.get("available"):
        return {
            "decision": "VETO",
            "reason": str(quote.get("reason") or "FRESH_QUOTE_UNAVAILABLE"),
            "quote": quote,
            "minute": minute,
        }
    if not minute.get("available"):
        return {
            "decision": "VETO",
            "reason": str(minute.get("reason") or "MINUTE_BARS_UNAVAILABLE"),
            "quote": quote,
            "minute": minute,
        }

    fresh_price = float(quote["current_price"])
    damage_atr = (fresh_price - signal_price) / atr14

    bars = list(minute.get("bars") or [])
    recent = bars[-5:]
    closes = [float(row["close"]) for row in recent]
    p3 = float(bars[-4]["close"])
    delta3_atr = (fresh_price - p3) / atr14

    weighted = 0.0
    volume_sum = 0.0
    for row in recent:
        vol = max(0.0, float(row.get("volume") or 0.0))
        typical = (
            float(row.get("high") or row["close"])
            + float(row.get("low") or row["close"])
            + float(row["close"])
        ) / 3.0
        weighted += typical * vol
        volume_sum += vol
    vwap5 = weighted / volume_sum if volume_sum > 0 else sum(closes) / len(closes)

    lower_steps = sum(1 for a, b in zip(closes, closes[1:]) if b < a)
    below_vwap = fresh_price < vwap5
    sharp_fall = (
        delta3_atr <= -FRESHNESS_SHARP_DROP_ATR
        or (
            lower_steps >= 2
            and below_vwap
            and fresh_price < signal_price
        )
    )

    if overheat_upper is not None and overheat_upper > 0 and fresh_price >= overheat_upper:
        decision = "VETO"
        reason = "FRESH_PRICE_AT_OR_ABOVE_OVERHEAT"
    elif buy_trigger_05 is not None and buy_trigger_05 > 0 and fresh_price < buy_trigger_05:
        decision = "VETO"
        reason = "FRESH_PRICE_BELOW_BUY_TRIGGER"
    elif damage_atr <= -FRESHNESS_DAMAGE_ATR:
        decision = "VETO"
        reason = "FRESH_PRICE_DAMAGED_0_4ATR"
    elif sharp_fall:
        decision = "VETO"
        reason = "RECENT_1M_SHARP_FALL"
    else:
        decision = "PASS"
        reason = "FRESH_SIGNAL_ALIVE"

    if fresh_price >= p3 and fresh_price >= vwap5:
        direction = "RISING"
    elif fresh_price < p3 and fresh_price < vwap5:
        direction = "FALLING"
    else:
        direction = "FLAT"

    return {
        "decision": decision,
        "reason": reason,
        "fresh_price": fresh_price,
        "signal_price": signal_price,
        "damage_atr": damage_atr,
        "price_3m_ago": p3,
        "delta_3m_atr": delta3_atr,
        "vwap_5m": vwap5,
        "lower_steps_5m": lower_steps,
        "direction": direction,
        "overheat_upper": overheat_upper,
        "change_pct": quote.get("change_pct"),
        "quote": quote,
        "minute_source": minute.get("source"),
    }


def _evaluate_etf_freshness(payload: Dict[str, Any]) -> Dict[str, Any]:
    ticker = _clean_ticker(payload.get("ticker"))
    stage = str(payload.get("stage") or "").strip()
    if stage not in ("▲", "△"):
        raise SignalServiceError("ETF_STAGE_NOT_UPWARD")

    session = RetryingKiwoomSession(max_attempts=5)
    token = _get_kiwoom_token(session)
    quote = _fetch_kiwoom_quote(session, token, ticker)
    minute = _fetch_kiwoom_minute_bars(session, token, ticker)

    if not quote.get("available"):
        return {"ok": True, "decision": "VETO", "reason": str(quote.get("reason") or "FRESH_QUOTE_UNAVAILABLE"), "ticker": ticker}
    if not minute.get("available"):
        return {"ok": True, "decision": "VETO", "reason": str(minute.get("reason") or "MINUTE_BARS_UNAVAILABLE"), "ticker": ticker}

    fresh_price = float(quote["current_price"])
    change_pct = quote.get("change_pct")
    bars = list(minute.get("bars") or [])
    recent = bars[-5:]
    closes = [float(row["close"]) for row in recent]
    p3 = float(bars[-4]["close"])

    weighted = 0.0
    volume_sum = 0.0
    for row in recent:
        vol = max(0.0, float(row.get("volume") or 0.0))
        typical = (float(row.get("high") or row["close"]) + float(row.get("low") or row["close"]) + float(row["close"])) / 3.0
        weighted += typical * vol
        volume_sum += vol
    vwap5 = weighted / volume_sum if volume_sum > 0 else sum(closes) / len(closes)
    lower_steps = sum(1 for a, b in zip(closes, closes[1:]) if b < a)

    if fresh_price >= p3 and fresh_price >= vwap5:
        direction = "RISING"
    elif fresh_price < p3 and fresh_price < vwap5:
        direction = "FALLING"
    else:
        direction = "FLAT"

    if change_pct is None:
        decision, reason = "VETO", "LIVE_CHANGE_PCT_UNAVAILABLE"
    elif stage == "▲" and change_pct < 0:
        decision, reason = "VETO", "STRONG_UP_REQUIRES_NONNEGATIVE_DAY"
    elif stage == "△" and change_pct <= -0.01:
        decision, reason = "VETO", "EARLY_UP_DAY_DROP_BELOW_MINUS_1PCT"
    elif direction == "FALLING":
        decision, reason = "VETO", "RECENT_1M_FALLING"
    else:
        decision, reason = "PASS", "ETF_FRESH_SIGNAL_ALIVE"

    return {
        "ok": True,
        "decision": decision,
        "reason": reason,
        "ticker": ticker,
        "stage": stage,
        "fresh_price": fresh_price,
        "change_pct": change_pct,
        "price_3m_ago": p3,
        "vwap_5m": vwap5,
        "direction": direction,
        "lower_steps_5m": lower_steps,
        "quote_source": quote.get("source"),
        "minute_source": minute.get("source"),
        "kiwoom_429_retry_count": session.retry_count,
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
    daily_reversal_pattern: Dict[str, Any] | None = None,
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
    if daily_reversal_pattern is None:
        daily_reversal_pattern = _fetch_daily_reversal_pattern(ticker)

    flow.pop("_flow_obj", None)
    return {
        "flow": flow,
        "disclosures": disclosures,
        "news": news,
        "catalyst": catalyst,
        "daily_reversal_pattern": daily_reversal_pattern,
        "kiwoom_429_retry_count": retry_count,
        "errors": errors,
    }


def _context_html(context: Dict[str, Any]) -> str:
    catalyst = context.get("catalyst") or {}
    flow = context.get("flow") or {}
    disclosures = context.get("disclosures") or []
    news = context.get("news") or []
    daily_pattern = context.get("daily_reversal_pattern") or {}

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
    if daily_pattern.get("available"):
        labels = daily_pattern.get("labels") or []
        bias = str(daily_pattern.get("bias") or "NONE").upper()
        bias_ko = {
            "BULL": "상승반전",
            "BEAR": "하락반전",
            "MIXED": "혼재",
            "NONE": "해당 없음",
        }.get(bias, bias)
        rows.append(
            '<div style="margin-top:8px;font-size:12px"><b>일봉 반전패턴</b>: '
            + escape(bias_ko)
            + (' · ' + escape(', '.join(str(x) for x in labels)) if labels else '')
            + '<br><span style="color:#64748b">보조신호 — 단독 매매신호는 아니며 기존 신호를 1단계 조정할 수 있음</span></div>'
        )

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

    # Cheap Jev PRE-GATE is deliberately first. A clear HOLD must not consume
    # Kiwoom calls and must never produce a user-facing watch mail.
    pre_gate = payload.get("jev_pre_gate") if isinstance(payload.get("jev_pre_gate"), dict) else {}
    pre_gate_ok = str(pre_gate.get("status") or "").upper() == "OK"
    pre_gate_decision = str(pre_gate.get("decision") or "").upper()
    needs_final_review = bool(pre_gate.get("needs_final_review", True))
    if pre_gate_ok and pre_gate_decision == "HOLD":
        return {
            "ok": True,
            "ticker": ticker,
            "name": name,
            "trigger_key": str(payload.get("trigger_key") or ""),
            "final_signal": FINAL_WATCH_ONLY,
            "flow_status": "JEV_PRE_HOLD",
            "flow_summary": str(pre_gate.get("reason") or "JEV_PRE_HOLD"),
            "jev_pre_gate": pre_gate,
            "mail_sent": False,
            "suppressed_by_jev_pre_gate": True,
            "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
        }

    session = RetryingKiwoomSession(max_attempts=5)
    token = _get_kiwoom_token(session)

    signal_price = _as_float(payload, "current_price")
    atr14 = _as_float(payload, "atr14")
    buy_trigger_05 = _as_float(payload, "buy_trigger_05")
    overheat_upper = _as_float(payload, "overheat_upper")
    freshness_gate = _evaluate_kiwoom_freshness_gate(
        ticker,
        signal_price=signal_price,
        atr14=atr14,
        buy_trigger_05=buy_trigger_05,
        overheat_upper=overheat_upper,
        session=session,
        token=token,
    )
    if freshness_gate.get("decision") != "PASS":
        app.logger.info(
            "freshness veto ticker=%s reason=%s signal=%s fresh=%s",
            ticker,
            freshness_gate.get("reason"),
            signal_price,
            freshness_gate.get("fresh_price"),
        )
        return {
            "ok": True,
            "ticker": ticker,
            "name": name,
            "trigger_key": str(payload.get("trigger_key") or ""),
            "final_signal": FINAL_WATCH_ONLY,
            "flow_status": "FRESHNESS_VETO",
            "flow_summary": str(freshness_gate.get("reason") or "FRESHNESS_VETO"),
            "freshness_gate": freshness_gate,
            "jev_pre_gate": pre_gate,
            "mail_sent": False,
            "suppressed_by_freshness_gate": True,
            "kiwoom_429_retry_count": session.retry_count,
            "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
        }

    fresh_price = float(freshness_gate["fresh_price"])
    effective_payload = dict(payload)
    effective_payload["signal_price"] = signal_price
    effective_payload["current_price"] = fresh_price

    rows = KiwoomInvestorFlowReader(token, session=session).fetch_stock_flow(
        ticker, as_of, max_pages=5
    )
    flow = evaluate_flow(rows, source_verified=True)
    if flow.status == FLOW_UNAVAILABLE:
        raise SignalServiceError(flow.reason)

    confirm_trigger_06 = _as_float(payload, "confirm_trigger_06")
    confirmed_06 = bool(
        confirm_trigger_06 is not None
        and confirm_trigger_06 > 0
        and fresh_price >= confirm_trigger_06
    )
    tech = TechnicalAssessment(
        status=TECH_BUY_CANDIDATE,
        rebound_atr=None,
        early_trigger_04=None,
        buy_trigger_05=buy_trigger_05,
        confirm_trigger_06=confirm_trigger_06,
        confirmed_06=confirmed_06,
        overheat=bool(overheat_upper is not None and overheat_upper > 0 and fresh_price >= overheat_upper),
        reason="BUY_CANDIDATE supplied by Google Sheet monitor",
    )
    final = combine_final_signal(tech, flow)

    flow_text = describe_flow(
        {
            "status": flow.status,
            "foreign_5d": flow.foreign_5d,
            "institution_5d": flow.institution_5d,
            "combined_5d": flow.combined_5d,
            "combined_20d": flow.combined_20d,
        }
    )

    # WATCH_ONLY remains a sheet/backend state only. It is never a mail class.
    if final.final_signal not in ALLOWED_FINAL_SIGNALS:
        return {
            "ok": True,
            "ticker": ticker,
            "name": name,
            "trigger_key": str(payload.get("trigger_key") or ""),
            "final_signal": final.final_signal,
            "flow_status": flow.status,
            "flow_summary": flow_text,
            "freshness_gate": freshness_gate,
            "jev_pre_gate": pre_gate,
            "mail_sent": False,
            "suppressed_non_mail_signal": True,
            "kiwoom_429_retry_count": session.retry_count,
            "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
        }

    timing_gate = _evaluate_entry_timing_veto(ticker)
    if timing_gate.get("decision") == "VETO":
        app.logger.info(
            "entry timing veto ticker=%s final=%s reason=%s",
            ticker, final.final_signal, timing_gate.get("reason"),
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
            "freshness_gate": freshness_gate,
            "timing_gate": timing_gate,
            "mail_sent": False,
            "suppressed_by_timing_gate": True,
            "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
        }

    final, confirmation_gate = _apply_strong_confirmation_gate(
        final,
        freshness_gate,
        timing_gate,
    )
    if confirmation_gate.get("decision") == "DOWNGRADE_TO_BUY_ALERT":
        flow_text += " / ▲매수확인 보류: " + str(confirmation_gate.get("reason") or "")

    daily_pattern = _fetch_daily_reversal_pattern(ticker)
    final, pattern_signal_gate = _apply_daily_pattern_signal_modifier(
        final,
        daily_pattern,
        tech,
        freshness_gate,
        timing_gate,
    )
    if pattern_signal_gate.get("decision") == "DOWNGRADE":
        flow_text += " / 일봉 하락반전 패턴으로 신호 한 단계 하향"
    elif pattern_signal_gate.get("decision") == "UPGRADE":
        flow_text += " / 일봉 상승반전 패턴으로 신호 한 단계 상향"

    if final.final_signal not in ALLOWED_FINAL_SIGNALS:
        return {
            "ok": True,
            "ticker": ticker,
            "name": name,
            "trigger_key": str(payload.get("trigger_key") or ""),
            "final_signal": final.final_signal,
            "flow_status": flow.status,
            "flow_summary": flow_text,
            "freshness_gate": freshness_gate,
            "timing_gate": timing_gate,
            "confirmation_gate": confirmation_gate,
            "pattern_signal_gate": pattern_signal_gate,
            "daily_reversal_pattern": daily_pattern,
            "mail_sent": False,
            "suppressed_by_daily_pattern": True,
            "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
        }

    early_safety_gate = _evaluate_early_alert_safety_gate(
        final,
        freshness_gate,
        timing_gate,
    )
    if early_safety_gate.get("decision") == "VETO":
        app.logger.info(
            "early alert safety veto ticker=%s reason=%s change_pct=%s",
            ticker,
            early_safety_gate.get("reason"),
            early_safety_gate.get("change_pct"),
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
            "freshness_gate": freshness_gate,
            "timing_gate": timing_gate,
            "confirmation_gate": confirmation_gate,
            "early_safety_gate": early_safety_gate,
            "mail_sent": False,
            "suppressed_by_early_safety_gate": True,
            "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
        }

    context = _collect_market_context(
        ticker=ticker,
        name=name,
        as_of=as_of,
        include_flow=False,
        daily_reversal_pattern=daily_pattern,
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
    context["freshness_gate"] = freshness_gate
    context["confirmation_gate"] = confirmation_gate
    context["early_safety_gate"] = early_safety_gate
    context["pattern_signal_gate"] = pattern_signal_gate
    context["kiwoom_429_retry_count"] = session.retry_count

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
                **effective_payload,
                "source": "YOUTUBE",
                "ticker": ticker,
                "name": name,
                "event_keys": ["YOUTUBE_BUY_CANDIDATE", final.final_signal],
                "event_level": "STRONG" if final.final_signal == FINAL_BUY_ALERT_STRONG else "WATCH",
                "freshness_gate": freshness_gate,
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
            "freshness_gate": freshness_gate,
            "jev_gate": jev_gate,
            "mail_sent": False,
            "suppressed_by_jev": True,
            "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
        }

    if payload.get("dry_run") is True:
        return {
            "ok": True,
            "ticker": ticker,
            "name": name,
            "trigger_key": str(payload.get("trigger_key") or ""),
            "final_signal": final.final_signal,
            "flow_status": flow.status,
            "flow_summary": flow_text,
            "freshness_gate": freshness_gate,
            "timing_gate": timing_gate,
            "confirmation_gate": confirmation_gate,
            "pattern_signal_gate": pattern_signal_gate,
            "jev_gate": jev_gate,
            "mail_sent": False,
            "dry_run": True,
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
        _email_html(effective_payload, final.final_signal, flow, flow_text, context=context),
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
        "freshness_gate": freshness_gate,
        "timing_gate": timing_gate,
        "confirmation_gate": confirmation_gate,
        "pattern_signal_gate": pattern_signal_gate,
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

    regime = str(payload.get("regime") or "").strip().upper()
    obv_dir = str(payload.get("obv_dir") or "").strip().upper()
    obv9_dir = str(payload.get("obv9_dir") or "").strip().upper()
    if regime in ("BEAR_CONFIRMED", "EARLY_WEAKENING") or (
        obv_dir == "FALLING" and obv9_dir == "FALLING"
    ):
        context["technical_exit_risk"] = _fetch_45m_exit_risk(ticker)
    else:
        context["technical_exit_risk"] = {
            "available": False,
            "reason": "LOCAL_BEARISH_PREFILTER_NOT_MET",
        }

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


def _krx_session_status(dt_input: Any = None) -> Dict[str, Any]:
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    target = dt_input or now_kst.date()
    try:
        d = KRXCalendar.parse_to_date(target)
    except Exception:
        d = now_kst.date()
    is_trading = KRXCalendar.is_krx_trading_day(d)
    return {
        "date": d.isoformat(),
        "is_trading_day": bool(is_trading),
        "reason": "TRADING_DAY" if is_trading else "KRX_CLOSED",
        "timezone": "Asia/Seoul",
    }


@app.get("/health")
def health() -> Any:
    return jsonify(
        {
            "ok": True,
            "service": "youtube-signal-service",
            "endpoints": ["/confirm", "/held-context", "/jev-gate", "/alert-outcome", "/etf-freshness", "/krx-session"],
        }
    )


@app.post("/krx-session")
def krx_session() -> Any:
    if not _secret_ok():
        return jsonify({"ok": False, "error": "UNAUTHORIZED"}), 401
    payload = request.get_json(silent=True) or {}
    status = _krx_session_status(payload.get("date"))
    return jsonify({"ok": True, **status})


@app.post("/confirm")
def confirm() -> Any:
    if not _secret_ok():
        return jsonify({"ok": False, "error": "UNAUTHORIZED"}), 401
    session_status = _krx_session_status()
    if not session_status["is_trading_day"]:
        return jsonify({
            "ok": True,
            "final_signal": FINAL_WATCH_ONLY,
            "mail_sent": False,
            "suppressed_by_krx_closed": True,
            "reason": "KRX_MARKET_CLOSED",
            "krx_session": session_status,
            "safety": {"sheet_write": False, "portfolio_mutation": False, "order_api": False},
        })
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


@app.post("/etf-freshness")
def etf_freshness() -> Any:
    if not _secret_ok():
        return jsonify({"ok": False, "error": "UNAUTHORIZED"}), 401
    session_status = _krx_session_status()
    if not session_status["is_trading_day"]:
        return jsonify({
            "ok": True,
            "decision": "VETO",
            "reason": "KRX_MARKET_CLOSED",
            "krx_session": session_status,
        })
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "INVALID_JSON"}), 400
    try:
        return jsonify(_evaluate_etf_freshness(payload)), 200
    except SignalServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("ETF freshness evaluation failed")
        return jsonify({"ok": False, "error": f"UNEXPECTED:{type(exc).__name__}"}), 500


@app.post("/alert-outcome")
def alert_outcome() -> Any:
    if not _secret_ok():
        return jsonify({"ok": False, "error": "UNAUTHORIZED"}), 401
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "INVALID_JSON"}), 400
    try:
        return jsonify(_evaluate_alert_outcome(payload)), 200
    except SignalServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("youtube alert outcome evaluation failed")
        return jsonify({"ok": False, "error": f"UNEXPECTED:{type(exc).__name__}"}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
