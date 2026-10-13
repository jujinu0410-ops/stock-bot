"""One source-neutral classification and market/sector confidence modifier.

Three watchlists are discovery sources, not trading strategies. This module
never sends email, writes a sheet, places an order, or manufactures a technical
confirmation. Downside and hard-risk signals are never downgraded by context.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping

STAGES = ("WATCH", "BUY_SETUP", "BUY_CONFIRMED", "DOWN_EARLY", "DOWN_CONFIRMED", "RISK")
SOURCE_TYPES = ("HELD", "ETF", "YOUTUBE")
BUY_STAGES = {"BUY_SETUP", "BUY_CONFIRMED"}
DOWNSIDE_STAGES = {"DOWN_EARLY", "DOWN_CONFIRMED", "RISK"}
STAGE_ALIAS = {
    "BUY_CANDIDATE": "BUY_SETUP",
    "BUY_ALERT": "BUY_SETUP",
    "BUY_ALERT_STRONG": "BUY_CONFIRMED",
    "WATCH_ONLY": "WATCH",
    "▲": "BUY_CONFIRMED", "△": "BUY_SETUP",
    "▼": "DOWN_CONFIRMED", "▽": "DOWN_EARLY",
    "SIGNAL_CONF_UP": "BUY_CONFIRMED",
    "SIGNAL_EARLY_UP": "BUY_SETUP", "BASE_PAIR_UP": "BUY_SETUP",
    "ATR_UP_1_0": "BUY_SETUP", "STOP_RECOVER_CONFIRMED": "BUY_SETUP",
    "SIGNAL_CONF_DOWN": "DOWN_CONFIRMED",
    "SIGNAL_EARLY_DOWN": "DOWN_EARLY", "BASE_PAIR_DOWN": "DOWN_EARLY",
    "ATR_DOWN_1_0": "DOWN_EARLY",
    "SELL_PREP_WARNING": "RISK", "OBV15_WARNING": "RISK",
    "STOP_BREACH": "RISK", "STOP_BREACH_INITIAL": "RISK",
}
SIGNAL_LABEL = {
    "WATCH": "관찰", "BUY_SETUP": "매수조짐", "BUY_CONFIRMED": "매수확인",
    "DOWN_EARLY": "하락조짐", "DOWN_CONFIRMED": "하락확인",
    "RISK": "위험경고",
}
STAGE_ORDER = {s: n for n, s in enumerate(STAGES)}

def clean_code(value: Any) -> str:
    digits = "".join(c for c in str(value or "") if c.isdigit())
    return digits.zfill(6)[-6:] if digits else ""

def normalize_stage(value: Any) -> str:
    raw = str(value or "").strip()
    if raw in STAGES:
        return raw
    upper = raw.upper()
    if upper in STAGE_ALIAS:
        return STAGE_ALIAS[upper]
    if raw in STAGE_ALIAS:
        return STAGE_ALIAS[raw]
    if raw.startswith("ETF_SIGNAL_") and len(raw) > len("ETF_SIGNAL_"):
        return STAGE_ALIAS.get(raw[len("ETF_SIGNAL_"):][:1], "WATCH")
    if raw.startswith(("▲", "△", "▼", "▽")):
        return STAGE_ALIAS[raw[0]]
    return "WATCH"

def normalize_sources(sources: Any) -> list[str]:
    values = sources if isinstance(sources, (tuple, list, set)) else [sources]
    out = []
    for value in values:
        tag = str(value or "").strip().upper()
        if tag in SOURCE_TYPES and tag not in out:
            out.append(tag)
    return sorted(out)

def _pct(raw: Any) -> float | None:
    try:
        n = float(raw)
        return n if abs(n) <= 1.0 else None
    except (TypeError, ValueError):
        return None

def _direction(raw: Any) -> int:
    pct = _pct(raw)
    # Use the verified market direction itself. Do not invent an unrequested
    # percentage threshold; exactly zero or unavailable is neutral.
    return 1 if pct is not None and pct > 0 else -1 if pct is not None and pct < 0 else 0

def evaluate_signal(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Common decision protocol, independent of which sheet supplied the ticker.

    market_pct and sector_etf_pct must be externally verified Kiwoom changes.
    If neither evidence source is supplied, preserve the exact technical stage.
    Market context adjusts *priority*, not technical confirmation or sell safety.
    """
    sources = normalize_sources(payload.get("sources", payload.get("source")))
    stage = normalize_stage(payload.get("stage", payload.get("signal")))
    market = str(payload.get("market") or "").upper().replace(" ", "")
    if market in ("KOSPI200", "KOSPI", "코스피"):
        market = "KOSPI"
    elif market in ("KOSDAQ150", "KOSDAQ", "코스닥"):
        market = "KOSDAQ"
    else:
        market = "UNKNOWN"
    etf_inverse = bool(payload.get("inverse", False))
    market_pct = _pct(payload.get("market_pct"))
    sector_pct = _pct(payload.get("sector_etf_pct"))
    # Inverse ETFs profit from an underlying market fall; only their context
    # direction inverts. Their own technical and live-price checks stay intact.
    market_dir = _direction(market_pct) * (-1 if etf_inverse else 1)
    sector_dir = _direction(sector_pct)
    adjust = 0
    reasons = []
    if stage in BUY_STAGES and market != "UNKNOWN":
        if market_dir > 0 and sector_dir >= 0:
            adjust = 1
            reasons.append("MARKET_ALIGNED_AND_SECTOR_SUPPORTIVE" if sector_dir else "MARKET_UP_SECTOR_UNCONFIRMED")
        elif market_dir < 0 and sector_dir <= 0:
            adjust = -1
            reasons.append("MARKET_AND_SECTOR_OPPOSED" if sector_dir else "MARKET_DOWN_SECTOR_UNCONFIRMED")
        elif market_dir < 0 and sector_dir > 0:
            reasons.append("SECTOR_RESILIENT_MARKET_WEAK")
        elif market_dir > 0 and sector_dir < 0:
            reasons.append("SECTOR_WEAK_MARKET_STRONG")
        else:
            reasons.append("MARKET_NEUTRAL")
    elif stage in DOWNSIDE_STAGES:
        reasons.append("DOWNSIDE_NEVER_SUPPRESSED_BY_MARKET_CONTEXT")
    else:
        reasons.append("NO_BUY_STAGE_OR_MARKET_CONTEXT")
    verified = payload.get("technical_verified", True) is True
    live = payload.get("live_verified", True) is True
    send_allowed = stage != "WATCH" and verified and live
    if not verified: reasons.append("TECHNICAL_NOT_VERIFIED")
    if not live: reasons.append("LIVE_PRICE_NOT_VERIFIED")
    # Preserve stage and only annotate priority; promotion to BUY_CONFIRMED
    # requires technical confirmation separately, regardless of market direction.
    return {
        "ok": True, "ticker": clean_code(payload.get("ticker")),
        "sources": sources, "market": market, "stage": stage,
        "signal_label": SIGNAL_LABEL[stage], "confidence_adjustment": adjust,
        "confidence": "AGGRESSIVE" if adjust > 0 else "CONSERVATIVE" if adjust < 0 else "NORMAL",
        "send_allowed": send_allowed,
        "market_proxy_change_pct": market_pct, "sector_etf_change_pct": sector_pct,
        "inverse": etf_inverse, "reasons": reasons,
        "technical_confirmation_unmodified": True, "order_api": False,
    }
