"""Pure calculations for the offline Policy Shadow retrospective.

This module deliberately has no database, network, scheduler, or operational
portfolio side effects.  Callers must provide the historical inputs they want
to evaluate; missing history is represented as ``DATA_GAP`` rather than being
filled from current operational state.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Optional


CALCULABLE = "CALCULABLE"
APPROXIMATE = "APPROXIMATE"
DATA_GAP = "DATA_GAP"
APPROXIMATE_DAILY_RANGE = "APPROXIMATE_DAILY_RANGE"

LEGACY_POSITION = "LEGACY_POSITION"
NEW_433_CYCLE = "NEW_433_CYCLE"

LOSS_DEFENSE_ON = "ON"
LOSS_DEFENSE_OFF = "OFF"
LOSS_DEFENSE_NOT_DETERMINABLE = "NOT_DETERMINABLE"
PREEXISTING_HARD_STOP_BREACH = "PREEXISTING_HARD_STOP_BREACH"


def calculate_433_target_quantities(cycle_target_qty: Optional[int]) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Return fixed cumulative 4-3-3 quantities using V4's floor-to-share rule.

    V4 risk sizing already produces an integer share quantity with ``floor``.
    Policy Shadow applies that same deterministic integer treatment to the
    frozen cycle target: floor(40%), floor(70%), and the original 100% target.
    A positive target always retains at least one share at an interim stage.
    """

    if cycle_target_qty is None or int(cycle_target_qty) <= 0:
        return None, None, None
    target = int(cycle_target_qty)
    stage1 = max(1, math.floor(target * 0.40))
    stage2 = max(stage1, math.floor(target * 0.70))
    return stage1, stage2, target


def stage_size_status(actual_quantity: Optional[int], target_quantity: Optional[int], stage: int = 1) -> str:
    """Classify observed cumulative quantity without changing a real position."""

    if actual_quantity is None or target_quantity is None or int(target_quantity) <= 0:
        return "TARGET_QTY_DATA_GAP"
    actual, target = int(actual_quantity), int(target_quantity)
    prefix = f"STAGE{stage}_SIZE"
    if actual == target:
        return f"{prefix}_MATCH"
    return f"{prefix}_UNDERALLOCATED" if actual < target else f"{prefix}_OVERALLOCATED"


@dataclass(frozen=True)
class DailyRange:
    """A stored daily bar used only for a daily-range approximation."""

    trading_date: str
    high: float
    low: float


@dataclass(frozen=True)
class DailyDefenseThreshold:
    """A daily-range threshold with no inferred intraday event ordering."""

    defense_low: float
    defense_atr: float
    rebound_threshold: float
    rebound_occurrence: str = DATA_GAP
    defense_peak: Optional[float] = None
    trail_threshold: Optional[float] = None
    trail_occurrence: str = DATA_GAP
    grade: str = APPROXIMATE_DAILY_RANGE


@dataclass(frozen=True)
class IntradayPoint:
    """One chronologically ordered observed price for a live Shadow evaluation."""

    timestamp: str
    price: float


@dataclass(frozen=True)
class DefensePath:
    """An ordered intraday defense path; never synthesized from a daily bar."""

    defense_low: float
    defense_atr: float
    rebound_threshold: float
    rebound_timestamp: Optional[str]
    defense_peak: Optional[float]
    trail_threshold: Optional[float]
    trail_trigger_timestamp: Optional[str]


@dataclass(frozen=True)
class HardStop:
    candidate: float
    hard_stop: float
    status: str = CALCULABLE


def calculate_t_change(window_start_t: Optional[float], latest_t: Optional[float]) -> Optional[float]:
    """Return the requested net T change without inventing a missing score."""

    if window_start_t is None or latest_t is None:
        return None
    return float(latest_t) - float(window_start_t)


def t_gate_for_loss_band(loss_pct: Optional[float], t_change_3d: Optional[float]) -> str:
    """Evaluate only the T part of LOSS_DEFENSE for a known historical loss."""

    if loss_pct is None or t_change_3d is None:
        return DATA_GAP
    if loss_pct <= -30.0:
        return "PASS" if t_change_3d <= -5.0 else "FAIL"
    if -30.0 < loss_pct <= -15.0:
        return "PASS" if t_change_3d <= -8.0 else "FAIL"
    return "NOT_APPLICABLE"


def evaluate_loss_defense(
    *,
    loss_pct: Optional[float],
    t_change_3d: Optional[float],
    is_45m_bearish: Optional[bool],
) -> str:
    """Return a final result only if both historical gates are actually known."""

    t_gate = t_gate_for_loss_band(loss_pct, t_change_3d)
    if t_gate == DATA_GAP or is_45m_bearish is None:
        return LOSS_DEFENSE_NOT_DETERMINABLE
    if t_gate == "NOT_APPLICABLE":
        return LOSS_DEFENSE_OFF
    return LOSS_DEFENSE_ON if t_gate == "PASS" and is_45m_bearish else LOSS_DEFENSE_OFF


def calculate_daily_rebound_threshold(
    daily_ranges: Iterable[DailyRange],
    defense_atr: Optional[float],
) -> Optional[DailyDefenseThreshold]:
    """Calculate only a rebound threshold from daily lows.

    A daily high may precede its daily low.  Consequently, a daily bar never
    proves that rebound occurred, cannot establish a post-rebound peak, and
    cannot establish a 0.4 ATR trail occurrence.
    """

    rows = sorted(daily_ranges, key=lambda row: row.trading_date)
    if not rows or defense_atr is None or defense_atr <= 0:
        return None

    _, low_row = min(enumerate(rows), key=lambda item: item[1].low)
    return DailyDefenseThreshold(
        defense_low=low_row.low,
        defense_atr=defense_atr,
        rebound_threshold=low_row.low + 0.6 * defense_atr,
    )


def evaluate_intraday_defense_path(
    intraday_points: Iterable[IntradayPoint],
    defense_atr: Optional[float],
    previous_trail: Optional[float] = None,
) -> Optional[DefensePath]:
    """Evaluate low → rebound → peak → trail only from ordered observations."""

    points = list(intraday_points)
    if not points or defense_atr is None or defense_atr <= 0:
        return None

    defense_low = points[0].price
    rebound_threshold = defense_low + 0.6 * defense_atr
    rebound_timestamp: Optional[str] = None
    defense_peak: Optional[float] = None
    trail_threshold: Optional[float] = None
    trail_trigger_timestamp: Optional[str] = None

    for point in points:
        if rebound_timestamp is None:
            if point.price < defense_low:
                defense_low = point.price
                rebound_threshold = defense_low + 0.6 * defense_atr
            if point.price >= rebound_threshold:
                rebound_timestamp = point.timestamp
                defense_peak = point.price
                raw_trail = defense_peak - 0.4 * defense_atr
                trail_threshold = max(raw_trail, previous_trail) if previous_trail is not None else raw_trail
            continue

        defense_peak = max(defense_peak, point.price)
        raw_trail = defense_peak - 0.4 * defense_atr
        trail_threshold = max(raw_trail, trail_threshold or raw_trail, previous_trail or raw_trail)
        if trail_trigger_timestamp is None and point.price <= trail_threshold:
            trail_trigger_timestamp = point.timestamp

    return DefensePath(
        defense_low=defense_low,
        defense_atr=defense_atr,
        rebound_threshold=rebound_threshold,
        rebound_timestamp=rebound_timestamp,
        defense_peak=defense_peak,
        trail_threshold=trail_threshold,
        trail_trigger_timestamp=trail_trigger_timestamp,
    )


def calculate_hard_stop(
    weighted_avg_price: Optional[float], previous_hard_stop: Optional[float] = None
) -> Optional[HardStop]:
    """Calculate the monotonic -22% stop when a contemporaneous average exists."""

    if weighted_avg_price is None or weighted_avg_price <= 0:
        return None
    candidate = weighted_avg_price * 0.78
    hard_stop = max(candidate, previous_hard_stop) if previous_hard_stop is not None else candidate
    return HardStop(candidate=candidate, hard_stop=hard_stop)


def current_only_hard_stop_status(
    *,
    current_price: Optional[float],
    current_weighted_avg_price: Optional[float],
    previous_hard_stop: Optional[float] = None,
) -> tuple[Optional[HardStop], str]:
    """Diagnose a present-day legacy position without treating it as history."""

    hard_stop = calculate_hard_stop(current_weighted_avg_price, previous_hard_stop)
    if hard_stop is None or current_price is None:
        return hard_stop, DATA_GAP
    if current_price <= hard_stop.hard_stop:
        return hard_stop, PREEXISTING_HARD_STOP_BREACH
    return hard_stop, "NOT_BREACHED_CURRENT_ONLY"
