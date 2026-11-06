"""Daily 3-candle reversal patterns for direct one-step signal modification.

Source-derived rules:
- 8 named patterns (4 bullish, 4 bearish)
- bullish patterns require a falling SMA3 pre-trend; bearish patterns require a rising SMA3 pre-trend
- 3-day candle body/open/close relationships follow the user's supplied video notes.

Engineering-only numeric thresholds for ambiguous words such as "long", "small",
"material gap", and "significantly lower/higher":
- long body >= max(1.25 * median body of prior 20 bars, 0.45 * ATR14)
- small body <= min(0.75 * median body of prior 20 bars, 0.45 * ATR14)
- material gap >= 0.15 * ATR14
- non-doji star body > 0.03 * ATR14
- "significantly" beyond prior close >= 0.15 * ATR14

These thresholds are implementation choices, not claims from the source video.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Dict, List, Sequence


LONG_BODY_MEDIAN_MULT = 1.25
LONG_BODY_ATR_MULT = 0.45
SMALL_BODY_MEDIAN_MULT = 0.75
SMALL_BODY_ATR_MULT = 0.45
GAP_ATR_MULT = 0.15
NON_DOJI_ATR_MULT = 0.03
CONFIRM_DISTANCE_ATR_MULT = 0.15

PATTERN_LABELS = {
    "BULL_THREE_RED_SOLDIERS": "적삼병",
    "BULL_HARAMI_CONFIRMED": "상승 잉태 확인형",
    "BULL_ENGULFING_CONFIRMED": "상승 장악 확인형",
    "MORNING_STAR": "샛별형",
    "BEAR_THREE_BLACK_CROWS": "흑삼병",
    "BEAR_HARAMI_CONFIRMED": "하락 잉태 확인형",
    "BEAR_ENGULFING_CONFIRMED": "하락 장악 확인형",
    "EVENING_STAR": "석별형",
}


def _f(bar: Dict[str, Any], key: str) -> float:
    return float(bar[key])


def _body(bar: Dict[str, Any]) -> float:
    return abs(_f(bar, "close") - _f(bar, "open"))


def _bull(bar: Dict[str, Any]) -> bool:
    return _f(bar, "close") > _f(bar, "open")


def _bear(bar: Dict[str, Any]) -> bool:
    return _f(bar, "close") < _f(bar, "open")


def _body_range(bar: Dict[str, Any]) -> tuple[float, float]:
    return min(_f(bar, "open"), _f(bar, "close")), max(_f(bar, "open"), _f(bar, "close"))


def _sma3(bars: Sequence[Dict[str, Any]], idx: int) -> float | None:
    if idx < 2:
        return None
    return sum(_f(bars[i], "close") for i in range(idx - 2, idx + 1)) / 3.0


def _atr14(bars: Sequence[Dict[str, Any]], idx: int) -> float | None:
    if idx < 14:
        return None
    trs: List[float] = []
    for i in range(idx - 13, idx + 1):
        prev_close = _f(bars[i - 1], "close")
        hi, lo = _f(bars[i], "high"), _f(bars[i], "low")
        trs.append(max(hi - lo, abs(hi - prev_close), abs(lo - prev_close)))
    return sum(trs) / len(trs)


def _pretrend_ok(bars: Sequence[Dict[str, Any]], day1: int, direction: str) -> bool:
    # Five consecutive SMA3 changes ending on the day before pattern day1.
    points = []
    for i in range(day1 - 6, day1):
        v = _sma3(bars, i)
        if v is None:
            return False
        points.append(v)
    if len(points) != 6:
        return False
    if direction == "DOWN":
        return all(points[i] < points[i - 1] for i in range(1, len(points)))
    return all(points[i] > points[i - 1] for i in range(1, len(points)))


def _thresholds(bars: Sequence[Dict[str, Any]], day1: int) -> Dict[str, float] | None:
    atr = _atr14(bars, day1)
    start = max(0, day1 - 20)
    prior = [_body(bars[i]) for i in range(start, day1)]
    prior = [v for v in prior if v > 0]
    if atr is None or atr <= 0 or len(prior) < 10:
        return None
    med = median(prior)
    return {
        "atr14": atr,
        "median_body20": med,
        "long_body_min": max(LONG_BODY_MEDIAN_MULT * med, LONG_BODY_ATR_MULT * atr),
        "small_body_max": min(SMALL_BODY_MEDIAN_MULT * med, SMALL_BODY_ATR_MULT * atr),
        "gap_min": GAP_ATR_MULT * atr,
        "non_doji_min": NON_DOJI_ATR_MULT * atr,
        "confirm_distance": CONFIRM_DISTANCE_ATR_MULT * atr,
    }


def _inside_body(inner: Dict[str, Any], outer: Dict[str, Any]) -> bool:
    ilo, ihi = _body_range(inner)
    olo, ohi = _body_range(outer)
    return ilo >= olo and ihi <= ohi


def _open_inside_body(cur: Dict[str, Any], prev: Dict[str, Any]) -> bool:
    lo, hi = _body_range(prev)
    return lo <= _f(cur, "open") <= hi


def detect_daily_reversal_patterns(bars: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Detect completed 3-day reversal patterns on the last three bars."""
    if len(bars) < 30:
        return {"available": False, "reason": f"INSUFFICIENT_BARS_{len(bars)}", "patterns": [], "bias": "NONE"}

    d1, d2, d3 = len(bars) - 3, len(bars) - 2, len(bars) - 1
    t = _thresholds(bars, d1)
    if not t:
        return {"available": False, "reason": "THRESHOLD_UNAVAILABLE", "patterns": [], "bias": "NONE"}

    b1, b2, b3 = bars[d1], bars[d2], bars[d3]
    atr = t["atr14"]
    long1, long2, long3 = (_body(b) >= t["long_body_min"] for b in (b1, b2, b3))
    small1, small2 = _body(b1) <= t["small_body_max"], _body(b2) <= t["small_body_max"]
    non_doji2 = _body(b2) > t["non_doji_min"]
    down_pre = _pretrend_ok(bars, d1, "DOWN")
    up_pre = _pretrend_ok(bars, d1, "UP")
    patterns: List[str] = []

    # 1) Three Red Soldiers
    if (
        down_pre and _bull(b1) and _bull(b2) and _bull(b3)
        and _open_inside_body(b2, b1) and _open_inside_body(b3, b2)
        and _f(b3, "close") == max(_f(b1, "close"), _f(b2, "close"), _f(b3, "close"))
    ):
        patterns.append("BULL_THREE_RED_SOLDIERS")

    # 2) Bullish Harami Confirmed
    if (
        down_pre and _bear(b1) and long1 and small2 and _inside_body(b2, b1)
        and not (_f(b1, "open") == _f(b2, "open") and _f(b1, "close") == _f(b2, "close"))
        and _bull(b3) and long3 and _f(b3, "close") > _f(b1, "open")
    ):
        patterns.append("BULL_HARAMI_CONFIRMED")

    # 3) Bullish Engulfing Confirmed
    if (
        down_pre and _bear(b1) and small1 and _bull(b2) and long2
        and _f(b2, "open") < _f(b1, "close")
        and _f(b2, "close") > _f(b1, "open")
        and _bull(b3) and long3 and _f(b3, "close") > _f(b2, "close")
    ):
        patterns.append("BULL_ENGULFING_CONFIRMED")

    # 4) Morning Star
    if (
        down_pre and _bear(b1) and long1 and small2 and non_doji2
        and (_f(b1, "close") - _f(b2, "open")) >= t["gap_min"]
        and _bull(b3) and (_f(b3, "open") - _f(b2, "close")) >= t["gap_min"]
        and _f(b3, "close") >= (_f(b1, "open") + _f(b1, "close")) / 2.0
    ):
        patterns.append("MORNING_STAR")

    # 5) Three Black Crows
    if (
        up_pre and _bear(b1) and _bear(b2) and _bear(b3)
        and _open_inside_body(b2, b1) and _open_inside_body(b3, b2)
        and _f(b3, "close") == min(_f(b1, "close"), _f(b2, "close"), _f(b3, "close"))
    ):
        patterns.append("BEAR_THREE_BLACK_CROWS")

    # 6) Bearish Harami Confirmed
    if (
        up_pre and _bull(b1) and long1 and small2 and _inside_body(b2, b1)
        and not (_f(b1, "open") == _f(b2, "open") and _f(b1, "close") == _f(b2, "close"))
        and _bear(b3) and long3 and _f(b3, "close") < _f(b1, "open")
    ):
        patterns.append("BEAR_HARAMI_CONFIRMED")

    # 7) Bearish Engulfing Confirmed
    if (
        up_pre and _bull(b1) and small1 and _bear(b2) and long2
        and _f(b2, "open") > _f(b1, "close")
        and _f(b2, "close") < _f(b1, "open")
        and _bear(b3)
        and (_f(b2, "close") - _f(b3, "close")) >= t["confirm_distance"]
    ):
        patterns.append("BEAR_ENGULFING_CONFIRMED")

    # 8) Evening Star
    if (
        up_pre and _bull(b1) and long1 and small2 and non_doji2
        and (_f(b2, "open") - _f(b1, "close")) >= t["gap_min"]
        and _bear(b3) and (_f(b2, "close") - _f(b3, "open")) >= t["gap_min"]
        and _f(b3, "close") <= (_f(b1, "open") + _f(b1, "close")) / 2.0
    ):
        patterns.append("EVENING_STAR")

    bull = [p for p in patterns if p.startswith("BULL_") or p == "MORNING_STAR"]
    bear = [p for p in patterns if p.startswith("BEAR_") or p == "EVENING_STAR"]
    bias = "BULL" if bull and not bear else "BEAR" if bear and not bull else "MIXED" if bull and bear else "NONE"

    return {
        "available": True,
        "patterns": patterns,
        "labels": [PATTERN_LABELS[p] for p in patterns],
        "bias": bias,
        "pretrend": "DOWN" if down_pre else "UP" if up_pre else "NONE",
        "last_date": str(b3.get("date") or ""),
        "thresholds": {k: round(v, 6) for k, v in t.items()},
        "mode": "DIRECT_MODIFIER_V1",
    }
