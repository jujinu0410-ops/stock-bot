from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


HORIZONS = (10, 30, 60)
STOP_PCT = -2.5
STOP_ATR = -0.8
B_PCT = 1.5
B_ATR = 0.3
A_PCT = 3.0
A_ATR = 0.6
S_PCT = 5.0
S_ATR = 1.0


@dataclass(frozen=True)
class AlertOutcomeBar:
    minutes_after_alert: int
    high: float
    low: float
    close: float


def _pct(price: float, entry: float) -> float:
    return ((price - entry) / entry) * 100.0


def _atr_move(price: float, entry: float, atr14: float) -> float | None:
    if atr14 <= 0:
        return None
    return (price - entry) / atr14


def _normalize_bars(rows: Iterable[AlertOutcomeBar | Mapping[str, Any]]) -> list[AlertOutcomeBar]:
    bars: list[AlertOutcomeBar] = []
    for row in rows:
        if isinstance(row, AlertOutcomeBar):
            bar = row
        else:
            bar = AlertOutcomeBar(
                minutes_after_alert=int(row["minutes_after_alert"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
        if bar.minutes_after_alert < 0:
            continue
        if min(bar.high, bar.low, bar.close) <= 0:
            continue
        bars.append(bar)
    bars.sort(key=lambda x: x.minutes_after_alert)
    return bars


def _hit_up(high: float, entry: float, atr14: float, pct_threshold: float, atr_threshold: float) -> bool:
    pct_hit = _pct(high, entry) >= pct_threshold
    atr_hit = atr14 > 0 and _atr_move(high, entry, atr14) >= atr_threshold
    return pct_hit or atr_hit


def _hit_stop(low: float, entry: float, atr14: float) -> bool:
    pct_hit = _pct(low, entry) <= STOP_PCT
    atr_hit = atr14 > 0 and _atr_move(low, entry, atr14) <= STOP_ATR
    return pct_hit or atr_hit


def evaluate_youtube_alert_outcome(
    *,
    alert_price: float,
    atr14: float,
    bars: Iterable[AlertOutcomeBar | Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate whether a YouTube alert created a usable intraday trading opportunity.

    This is a SHADOW/post-trade evaluator only. It must not alter live alert decisions.

    Grade policy:
    - S: +5% or +1.0 ATR continuation within 60m, before any stop-risk breach.
    - A: +3% or +0.6 ATR continuation within 60m, before any stop-risk breach.
    - B: +1.5% or +0.3 ATR continuation, or a larger move that came only after stop-risk.
    - C: no meaningful continuation within 60m.
    """
    if alert_price <= 0:
        raise ValueError("alert_price must be positive")
    if atr14 < 0:
        raise ValueError("atr14 must be non-negative")

    normalized = _normalize_bars(bars)
    if not normalized:
        return {
            "status": "PENDING",
            "grade": None,
            "label": "평가 대기",
            "alert_price": alert_price,
            "atr14": atr14,
            "horizons": {},
        }

    horizon_results: dict[str, Any] = {}
    max_elapsed = max(b.minutes_after_alert for b in normalized)
    for horizon in HORIZONS:
        if max_elapsed < horizon:
            horizon_results[f"{horizon}m"] = None
            continue
        subset = [b for b in normalized if b.minutes_after_alert <= horizon]
        if not subset:
            horizon_results[f"{horizon}m"] = None
            continue
        max_high = max(b.high for b in subset)
        min_low = min(b.low for b in subset)
        last_close = subset[-1].close
        horizon_results[f"{horizon}m"] = {
            "mfe_pct": round(_pct(max_high, alert_price), 2),
            "mae_pct": round(_pct(min_low, alert_price), 2),
            "close_return_pct": round(_pct(last_close, alert_price), 2),
            "mfe_atr": round(_atr_move(max_high, alert_price, atr14), 2) if atr14 > 0 else None,
            "mae_atr": round(_atr_move(min_low, alert_price, atr14), 2) if atr14 > 0 else None,
            "max_high": max_high,
            "min_low": min_low,
            "last_close": last_close,
            "bars": len(subset),
        }

    bars60 = [b for b in normalized if b.minutes_after_alert <= 60]
    if max_elapsed < 60 or not bars60:
        return {
            "status": f"IN_PROGRESS_{max_elapsed}M",
            "grade": None,
            "label": "60분 평가 대기",
            "alert_price": alert_price,
            "atr14": atr14,
            "horizons": horizon_results,
        }

    first_stop = None
    first_b = None
    first_a = None
    first_s = None
    for bar in bars60:
        if first_stop is None and _hit_stop(bar.low, alert_price, atr14):
            first_stop = bar.minutes_after_alert
        if first_b is None and _hit_up(bar.high, alert_price, atr14, B_PCT, B_ATR):
            first_b = bar.minutes_after_alert
        if first_a is None and _hit_up(bar.high, alert_price, atr14, A_PCT, A_ATR):
            first_a = bar.minutes_after_alert
        if first_s is None and _hit_up(bar.high, alert_price, atr14, S_PCT, S_ATR):
            first_s = bar.minutes_after_alert

    stop_before_a = first_stop is not None and (first_a is None or first_stop <= first_a)
    if first_s is not None and not stop_before_a:
        grade, label = "S", "매우 좋은 알림"
    elif first_a is not None and not stop_before_a:
        grade, label = "A", "좋은 알림"
    elif first_b is not None or first_a is not None or first_s is not None:
        grade, label = "B", "기회는 있었으나 진입 품질 아쉬움"
    else:
        grade, label = "C", "나쁜 알림"

    return {
        "status": "EVALUATED_60M",
        "grade": grade,
        "label": label,
        "alert_price": alert_price,
        "atr14": atr14,
        "horizons": horizon_results,
        "first_hit_min": {
            "plus_1_5pct_or_0_3atr": first_b,
            "plus_3pct_or_0_6atr": first_a,
            "plus_5pct_or_1_0atr": first_s,
            "stop_risk_minus_2_5pct_or_0_8atr": first_stop,
        },
        "stop_before_tradeable_target": stop_before_a,
        "policy": {
            "S": "+5% or +1.0ATR within 60m before stop-risk",
            "A": "+3% or +0.6ATR within 60m before stop-risk",
            "B": "+1.5% or +0.3ATR, or larger move only after stop-risk",
            "C": "no meaningful continuation within 60m",
        },
    }
