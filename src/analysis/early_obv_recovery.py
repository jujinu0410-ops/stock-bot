"""Evidence-based early OBV veto using completed 45m history and live 1m volume.

Only veto an existing buy candidate when the previous trading day's final
four completed 45m candles establish persistent OBV/OBV9 deterioration AND
there is no meaningful intraday OBV/price/volume recovery. Never impose a
09:50 timing blackout or treat missing data as positive evidence.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

def evaluate_early_obv_recovery(
    history: list[dict[str, Any]],
    minute_bars: list[dict[str, Any]],
    *,
    today: date,
    fresh_price: float,
    atr14: float,
    require_prior_weakness: bool = True,
) -> dict[str, Any]:
    def result(decision: str, reason: str, **kw: Any) -> dict[str, Any]:
        return {"decision": decision, "reason": reason, **kw}

    if len(history) < 14 or atr14 <= 0 or fresh_price <= 0:
        return result("UNAVAILABLE", "OBV_HISTORY_OR_ATR_UNAVAILABLE")

    series = []
    running = 0.0
    for i, bar in enumerate(history):
        close = float(bar["close"])
        volume = float(bar["volume"])
        if i:
            prevclose = float(history[i - 1]["close"])
            running += volume if close > prevclose else -volume if close < prevclose else 0
        series.append(running)
    enriched = []
    for i, (bar, value) in enumerate(zip(history, series)):
        enriched.append({
            **bar, "obv": value,
            "obv9": sum(series[i - 8:i + 1]) / 9 if i >= 8 else None,
        })

    prior_dates = sorted({str(x["date"]) for x in enriched if str(x["date"]) < today.isoformat()})
    if not prior_dates:
        return result("UNAVAILABLE", "PREVIOUS_TRADING_SESSION_NOT_FOUND")
    prior_date = prior_dates[-1]
    previous = [x for x in enriched if str(x["date"]) == prior_date]
    if len(previous) < 6:
        return result("UNAVAILABLE", "PREVIOUS_SESSION_45M_BARS_INSUFFICIENT")
    last_four = previous[-4:]
    if any(x["obv9"] is None for x in last_four):
        return result("UNAVAILABLE", "PREVIOUS_OBV9_NOT_READY")

    no_gold = all(x["obv"] <= x["obv9"] for x in last_four)
    obv_falling = all(a["obv"] > b["obv"] for a, b in zip(last_four, last_four[1:]))
    obv9_falling = all(a["obv9"] > b["obv9"] for a, b in zip(last_four, last_four[1:]))
    persistent_weakness = no_gold and obv_falling and obv9_falling
    evidence = {
        "previous_session": prior_date,
        "prior_no_gold_4": no_gold,
        "prior_obv_down_4": obv_falling,
        "prior_obv9_down_4": obv9_falling,
    }
    if not persistent_weakness and require_prior_weakness:
        return result("PASS", "PRIOR_45M_OBV_NOT_PERSISTENTLY_WEAK", **evidence)

    current_completed = [x for x in enriched if str(x["date"]) == today.isoformat()]
    if current_completed:
        latest = current_completed[-1]
        if latest["obv9"] is not None and latest["obv"] > latest["obv9"] and latest["obv"] > previous[-1]["obv"]:
            return result("PASS", "CURRENT_COMPLETED_45M_OBV_GOLD", **evidence)

    # An unclosed 45m candle is not considered 'complete'; its live minutes
    # can nevertheless prove meaningful recovery before 09:50.
    ordered = sorted(minute_bars, key=lambda x: str(x.get("time") or ""))
    def get_minute_time(bar: dict[str, Any]) -> datetime | None:
        raw = str(bar.get("time") or "")
        try:
            if len(raw) >= 14:
                return datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
            if len(raw) == 6:
                return datetime.combine(today, datetime.strptime(raw, "%H%M%S").time())
        except ValueError:
            return None
        return None

    latest_completed_time = (
        datetime.strptime(str(current_completed[-1]["time"]), "%H:%M").time()
        if current_completed else None
    )
    live = []
    for bar in ordered:
        stamp = get_minute_time(bar)
        if not stamp or stamp.date() != today:
            continue
        if not ((9, 0) <= (stamp.hour, stamp.minute) <= (15, 30)):
            continue
        # Do not double-count minutes already represented by a completed 45m
        # candle. Today's unfinished candle alone supplies provisional volume.
        if latest_completed_time and stamp.time() < latest_completed_time:
            continue
        live.append(bar)
    if len(live) < 6:
        return result("VETO", "PRIOR_45M_OBV_DOWN_AND_LIVE_MINUTES_INSUFFICIENT", **evidence)

    # All available today's minutes (max 60) estimate the unfinished 45m bar.
    # Strictly require price higher than the last completed bar for a bullish
    # contribution; weak gap-down bounces cannot manufacture a gold signal.
    partial_volume = sum(max(0.0, float(x["volume"])) for x in live)
    latest_bar = current_completed[-1] if current_completed else previous[-1]
    partial_obv = latest_bar["obv"] + (
        partial_volume if fresh_price > float(latest_bar["close"])
        else -partial_volume if fresh_price < float(latest_bar["close"])
        else 0
    )
    prev_eight = [x["obv"] for x in enriched[-8:]]
    provisional_obv9 = (sum(prev_eight) + partial_obv) / 9
    recent = live[-10:]
    close_seq = [float(x["close"]) for x in recent]
    rise_from_low_atr = (fresh_price - min(close_seq)) / atr14
    previous_avg_45m_volume = sum(float(x["volume"]) for x in previous) / len(previous)
    expected_window_vol = previous_avg_45m_volume * len(recent) / 45
    volume_ratio = (
        sum(max(0.0, float(x["volume"])) for x in recent) / expected_window_vol
        if expected_window_vol > 0 else 0.0
    )
    up_vol, down_vol = 0.0, 0.0
    for a, b in zip(recent, recent[1:]):
        v = max(0.0, float(b["volume"]))
        if float(b["close"]) > float(a["close"]):
            up_vol += v
        elif float(b["close"]) < float(a["close"]):
            down_vol += v
    weighted_vwap = sum(
        float(x["close"]) * max(0.0, float(x["volume"])) for x in recent
    ) / max(1.0, sum(max(0.0, float(x["volume"])) for x in recent))
    recent_up_steps = sum(a < b for a, b in zip(close_seq[-5:], close_seq[-4:]))
    price_momentum = fresh_price >= weighted_vwap and recent_up_steps >= 3
    live_gold = (
        partial_obv > provisional_obv9
        and partial_obv > latest_bar["obv"]
        and volume_ratio >= 1.1
        and price_momentum
    )
    meaningful_recovery = (
        rise_from_low_atr >= 0.40
        and volume_ratio >= 1.50
        and up_vol >= 1.60 * max(down_vol, 1.0)
        and price_momentum
    )
    details = {
        **evidence,
        "provisional_obv_gold": live_gold,
        "rebound_atr_10m": round(rise_from_low_atr, 3),
        "volume_ratio_vs_prior_45m": round(volume_ratio, 3),
        "up_down_volume_ratio": round(up_vol / max(down_vol, 1.0), 3),
        "recent_up_steps_4": recent_up_steps,
        "minute_count": len(live),
    }
    if live_gold:
        return result("PASS", "LIVE_PARTIAL_OBV_GOLD_WITH_VOLUME", **details)
    if meaningful_recovery:
        return result("PASS", "MEANINGFUL_PRICE_AND_VOLUME_OBV_RECOVERY", **details)
    return result("VETO", "PERSISTENT_45M_OBV_DOWN_WITHOUT_MEANINGFUL_RECOVERY", **details)
