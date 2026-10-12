"""Completed 45m OBV 15-bar sell-preparation warning for held positions.

Only verified 15-bar no-gold + recent falling OBV/OBV9 triggers risk review.
Live price/volume OBV recovery supersedes stale completed-bar weakness.
Does not place trades and does not depend on MACD or BEAR_CONFIRMED.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from src.analysis.early_obv_recovery import evaluate_early_obv_recovery


def analyze_obv15_sell_warning(
    df: Any,
    minute_bars: list[dict[str, Any]],
    *,
    today: date,
    fresh_price: float,
    atr14: float,
) -> dict[str, Any]:
    if df is None or len(df) < 24:
        return {"decision": "UNAVAILABLE", "reason": "INSUFFICIENT_COMPLETED_45M_HISTORY"}
    if fresh_price <= 0 or atr14 <= 0:
        return {"decision": "UNAVAILABLE", "reason": "INVALID_LIVE_PRICE_OR_ATR"}

    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    delta = close.diff()
    signed = volume.where(delta > 0, (-volume).where(delta < 0, 0.0))
    obv = signed.fillna(0.0).cumsum()
    obv9 = obv.rolling(9).mean()
    if obv9.tail(15).isna().any():
        return {"decision": "UNAVAILABLE", "reason": "OBV9_HISTORY_INSUFFICIENT"}

    below = (obv <= obv9)
    streak = 0
    for value in reversed(below.tolist()):
        if not value:
            break
        streak += 1
    recent_obv_down = bool(obv.iloc[-3] > obv.iloc[-2] > obv.iloc[-1])
    recent_signal_down = bool(obv9.iloc[-3] > obv9.iloc[-2] > obv9.iloc[-1])
    details = {
        "consecutive_no_gold_bars": streak,
        "recent_obv_down": recent_obv_down,
        "recent_obv9_down": recent_signal_down,
        "last_completed_bar": df.index[-1].strftime("%Y-%m-%d %H:%M"),
        "last_completed_close": float(close.iloc[-1]),
        "obv": float(obv.iloc[-1]),
        "obv9": float(obv9.iloc[-1]),
        "current_price": fresh_price,
    }
    if streak < 15 or not recent_obv_down or not recent_signal_down:
        return {"decision": "PASS", "reason": "OBV15_WEAKNESS_NOT_CONFIRMED", **details}

    history = [
        {
            "date": ts.date().isoformat(),
            "time": ts.strftime("%H:%M"),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        for ts, row in df.iterrows()
    ]
    live = evaluate_early_obv_recovery(
        history, minute_bars, today=today, fresh_price=fresh_price, atr14=atr14,
        require_prior_weakness=False
    )
    details["live_recovery"] = live
    if live.get("reason") in {
        "LIVE_PARTIAL_OBV_GOLD_WITH_VOLUME",
        "MEANINGFUL_PRICE_AND_VOLUME_OBV_RECOVERY",
        "CURRENT_COMPLETED_45M_OBV_GOLD",
    }:
        return {"decision": "PASS", "reason": "LIVE_RECOVERY_CONFIRMED", **details}

    # Without today's verified minute bars, we cannot rule out live recovery.
    if live.get("reason") in {
        "PRIOR_45M_OBV_DOWN_AND_LIVE_MINUTES_INSUFFICIENT",
        "OBV_HISTORY_OR_ATR_UNAVAILABLE",
        "PREVIOUS_TRADING_SESSION_NOT_FOUND",
    } or live.get("decision") == "UNAVAILABLE":
        return {"decision": "UNAVAILABLE", "reason": "LIVE_RECOVERY_DATA_INSUFFICIENT", **details}

    return {
        "decision": "WARN",
        "reason": "15_COMPLETED_45M_BARS_NO_GOLD_OBV_OBV9_FALLING_NO_LIVE_RECOVERY",
        **details,
    }
