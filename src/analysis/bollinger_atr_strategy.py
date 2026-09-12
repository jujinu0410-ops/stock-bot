"""Stateless BB(26, 2.0) / ATR advisory overlay for held positions.

This module is isolated from the V4 risk state machine. It reads completed
intraday bars and existing protection floors, returns display-only metadata,
and never persists or places an order.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.analysis.intraday_analysis import Intraday45mAnalyzer
from src.analysis.technical_analysis import adjust_krx_tick_size
from src.utils.logger import logger


OVERLAY_VERSION = "BB_ATR_V1"
BB_WINDOW = 26
BB_SIGMA = 2.0
BB_DDOF = 0
ATR_MULTIPLIER = 0.8
KST = ZoneInfo("Asia/Seoul")

SESSION_CUTOFFS = {
    "1120": (11, 15),
    "1335": (13, 30),
    "1535": (15, 0),
}

# Both production APIs label rows by bar start. Naver's endpoint currently
# returns one-minute rows despite its range=15m URL parameter.
SOURCE_BAR_START_MINUTES = {
    "YFINANCE_15M": 15,
    "NAVER_MINUTE_API": 1,
    "TEST_15M_BAR_START": 15,
    "TEST_1M_BAR_START": 1,
}

KNOWN_ETF_CODES = {"088500", "161510", "371460", "484730", "490590"}
ETF_NAME_MARKERS = (
    "ETF", "ETN", "KODEX", "TIGER", "RISE", "PLUS", "ACE",
    "KBSTAR", "ARIRANG", "HANARO", "SOL ", "커버드콜",
)


def _finite_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_kst_naive(value: datetime | pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(KST).tz_localize(None)
    return stamp


def _report_asof_text(asof_dt: datetime) -> str:
    stamp = pd.Timestamp(asof_dt)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(KST)
    return stamp.strftime("%Y-%m-%d %H:%M:%S KST")


def _base_result(item: Dict[str, Any], asof_dt: datetime, session_code: str) -> Dict[str, Any]:
    return {
        "stock_code": str(item.get("stock_code") or "").zfill(6),
        "overlay_version": OVERLAY_VERSION,
        "overlay_scope": "ADVISORY_ONLY",
        "overlay_status": "NO_ADVISORY",
        "data_quality": "INVALID",
        "reason_codes": [],
        "report_session": str(session_code),
        "report_asof_kst": _report_asof_text(asof_dt),
        "completed_45m_timestamp": None,
        "completed_close": None,
        "bb_window": BB_WINDOW,
        "bb_sigma": BB_SIGMA,
        "bb_middle": None,
        "bb_upper": None,
        "bb_lower": None,
        "upper_confirmation_threshold": None,
        "lower_confirmation_threshold": None,
        "upper_confirmed": False,
        "lower_confirmed": False,
        "profit_basis": "AVG_BUY_PRICE",
        "profit_reference_price": None,
        "profit_state": None,
        "bb_mode": "NO_ADVISORY",
        "atr_used": None,
        "atr_source": "V4_CURRENT_COMPLETED_WILDER_ATR14",
        "atr_multiplier": ATR_MULTIPLIER,
        "bb_reference_floor": None,
        "authoritative_existing_floor": None,
        "effective_advisory_floor": None,
        "higher_priority_state": None,
        "overlay_suppressed": False,
        "advisory_text": "데이터 확인 필요 · 기존 V4 전략 유지",
        "actual_order_impact": 0,
        # Compatibility aliases for pre-overlay display consumers.
        "mode": "NO_ADVISORY",
        "lower_conf": None,
        "upper_conf": None,
        "advisory_floor": None,
        "advisory_msg": "데이터 확인 필요 · 기존 V4 전략 유지",
        "error": None,
    }


def make_no_advisory(
    item: Dict[str, Any],
    asof_dt: datetime,
    session_code: str,
    reason_code: str,
    *,
    data_quality: str = "INVALID",
) -> Dict[str, Any]:
    result = _base_result(item, asof_dt, session_code)
    result["data_quality"] = data_quality
    result["reason_codes"] = [reason_code]
    result["error"] = reason_code
    return result


def _suppress(item: Dict[str, Any], asof_dt: datetime, session_code: str, state: str) -> Dict[str, Any]:
    result = _base_result(item, asof_dt, session_code)
    result.update(
        overlay_status="SUPPRESSED_BY_HIGHER_PRIORITY",
        data_quality="SUPPRESSED",
        reason_codes=[state],
        bb_mode="SUPPRESSED",
        higher_priority_state=state,
        overlay_suppressed=True,
        advisory_text=f"{state} 우선 · 기존 방어전략 유지",
        mode="SUPPRESSED_BY_HIGHER_PRIORITY",
        advisory_msg=f"{state} 우선 · 기존 방어전략 유지",
        error=None,
    )
    return result


def _higher_priority_state(item: Dict[str, Any], policy_row: Optional[Dict[str, Any]]) -> Optional[str]:
    trade_mode = str(item.get("trade_mode") or "").upper()
    action_status = str(item.get("action_status") or "").upper()
    if bool(item.get("hard_stop_active")) or trade_mode in {"HARD_STOP", "EMERGENCY"}:
        return "HARD_STOP"
    if bool(item.get("is_stop_breached")) or "STOP_BREACHED" in action_status or "손절선 침범" in action_status:
        return "STOP_BREACHED"
    if bool(item.get("is_suspended")) or trade_mode == "SUSPENDED_HOLD":
        return "SUSPENDED"
    if trade_mode == "LOSS_DEFENSE" or item.get("loss_defense_trigger") == "ON" or item.get("defense_state") in {"WAIT_REBOUND", "TRAIL_ACTIVE"}:
        return "LOSS_DEFENSE"
    if item.get("data_validity_flag") != 1 or trade_mode == "HOLD" or "DATA_HOLD" in str(item.get("data_hold_reason") or "").upper():
        return "DATA_HOLD"
    if trade_mode == "USER_OVERRIDE":
        return "USER_OVERRIDE"
    if policy_row and policy_row.get("display_allowed"):
        if policy_row.get("shadow_action") == "HARD_STOP_EXIT":
            return "HARD_STOP"
        if policy_row.get("is_suspended"):
            return "SUSPENDED"
        if policy_row.get("loss_defense_trigger") == "ON" or policy_row.get("defense_state") in {"WAIT_REBOUND", "TRAIL_ACTIVE"}:
            return "LOSS_DEFENSE"
    return None


def resolve_tick_regime(item: Dict[str, Any]) -> Optional[bool]:
    """Return canonical is_etf, or None when product metadata is untrusted."""
    if "is_etf" not in item:
        return None
    raw = item["is_etf"]
    if isinstance(raw, bool):
        is_etf = raw
    elif isinstance(raw, int) and raw in (0, 1):
        is_etf = bool(raw)
    else:
        return None
    code = str(item.get("stock_code") or "").strip().zfill(6)
    name_upper = str(item.get("stock_name") or "").upper()
    looks_like_etf = code in KNOWN_ETF_CODES or any(marker in name_upper for marker in ETF_NAME_MARKERS)
    if looks_like_etf and not is_etf:
        return None
    return is_etf


def calculate_bb26_2(df: pd.DataFrame) -> Tuple[float, float, float]:
    """Calculate independent BB(26, 2.0) using population std (ddof=0)."""
    if not isinstance(df, pd.DataFrame) or len(df) < BB_WINDOW:
        return np.nan, np.nan, np.nan
    column = "Close" if "Close" in df.columns else "close_price" if "close_price" in df.columns else None
    if column is None:
        return np.nan, np.nan, np.nan
    tail = pd.to_numeric(df[column], errors="coerce").iloc[-BB_WINDOW:]
    if len(tail) != BB_WINDOW or tail.isna().any() or not np.isfinite(tail.to_numpy(dtype=float)).all():
        return np.nan, np.nan, np.nan
    middle = float(tail.mean())
    std = float(tail.std(ddof=BB_DDOF))
    return middle - BB_SIGMA * std, middle, middle + BB_SIGMA * std


def normalize_to_legal_tick(price: float, direction: str, is_etf: bool) -> int:
    value = _finite_float(price)
    if value is None or value <= 0 or direction not in {"up", "down"} or not isinstance(is_etf, bool):
        raise ValueError("KRX_TICK_NORMALIZATION_FAILED")
    normalized = adjust_krx_tick_size(value, direction=direction, is_etf=is_etf)
    if normalized <= 0:
        raise ValueError("KRX_TICK_NORMALIZATION_FAILED")
    return int(normalized)


def _stock_tick_step(price: int, direction: str) -> int:
    if direction == "up":
        if price < 2_000: return 1
        if price < 5_000: return 5
        if price < 20_000: return 10
        if price < 50_000: return 50
        if price < 200_000: return 100
        if price < 500_000: return 500
        return 1_000
    if price <= 2_000: return 1
    if price <= 5_000: return 5
    if price <= 20_000: return 10
    if price <= 50_000: return 50
    if price <= 200_000: return 100
    if price <= 500_000: return 500
    return 1_000


def step_legal_ticks(price: int, steps: int, direction: str, is_etf: bool) -> int:
    if direction not in {"up", "down"} or not isinstance(is_etf, bool) or not isinstance(steps, int) or steps < 0:
        raise ValueError("KRX_TICK_STEP_FAILED")
    current = int(price)
    if current <= 0:
        raise ValueError("KRX_TICK_STEP_FAILED")
    for _ in range(steps):
        unit = 5 if is_etf else _stock_tick_step(current, direction)
        current = current + unit if direction == "up" else current - unit
        if current <= 0:
            raise ValueError("KRX_TICK_STEP_FAILED")
    return current


def calculate_confirmation_thresholds(bb_lower: float, bb_upper: float, is_etf: bool) -> Tuple[int, int]:
    upper_outward = normalize_to_legal_tick(bb_upper, "up", is_etf)
    lower_outward = normalize_to_legal_tick(bb_lower, "down", is_etf)
    return (
        step_legal_ticks(lower_outward, 3, "down", is_etf),
        step_legal_ticks(upper_outward, 3, "up", is_etf),
    )


def resolve_cutoff(asof_dt: datetime, session_code: str) -> datetime:
    try:
        hour, minute = SESSION_CUTOFFS[str(session_code)]
    except KeyError as exc:
        raise ValueError("INVALID_REPORT_SESSION") from exc
    return asof_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _source_interval_minutes(source: str) -> Optional[int]:
    source_upper = str(source or "").upper()
    for prefix, minutes in SOURCE_BAR_START_MINUTES.items():
        if source_upper.startswith(prefix):
            return minutes
    return None


def prepare_completed_45m_data(raw_df: pd.DataFrame, source: str, cutoff_dt: datetime) -> Tuple[Optional[pd.DataFrame], str]:
    """Aggregate validated start-labelled rows into end-labelled 45m bars."""
    if not isinstance(raw_df, pd.DataFrame) or raw_df.empty:
        return None, "REQUIRED_SOURCE_DATA_MISSING"
    interval_minutes = _source_interval_minutes(source)
    if interval_minutes is None or 45 % interval_minutes != 0:
        return None, "UNSUPPORTED_TIMESTAMP_SEMANTICS"
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in raw_df.columns for column in required):
        return None, "MALFORMED_OHLCV"

    frame = raw_df.loc[:, required].copy()
    try:
        original_index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="raise"))
    except Exception:
        return None, "INVALID_TIMESTAMP"
    if original_index.has_duplicates:
        return None, "DUPLICATE_TIMESTAMP"
    if not original_index.is_monotonic_increasing:
        return None, "NON_MONOTONIC_TIMESTAMP"
    try:
        normalized_index = original_index.tz_convert(KST).tz_localize(None) if original_index.tz is not None else original_index
    except Exception:
        return None, "INVALID_TIMESTAMP"
    if normalized_index.has_duplicates or not normalized_index.is_monotonic_increasing:
        return None, "INVALID_TIMESTAMP"
    frame.index = normalized_index

    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    numeric = frame[required].to_numpy(dtype=float)
    if np.isnan(numeric).any() or not np.isfinite(numeric).all():
        return None, "MALFORMED_OHLCV"
    if (frame[["Open", "High", "Low", "Close"]] <= 0).any().any() or (frame["Volume"] < 0).any():
        return None, "MALFORMED_OHLCV"
    if (frame["High"] < frame[["Open", "Close", "Low"]].max(axis=1)).any() or (frame["Low"] > frame[["Open", "Close", "High"]].min(axis=1)).any():
        return None, "MALFORMED_OHLCV"

    cutoff = _as_kst_naive(cutoff_dt)
    duration = pd.Timedelta(minutes=interval_minutes)
    day = frame.index.normalize()
    day_open = day + pd.Timedelta(hours=9)
    day_last_completed = day + pd.Timedelta(hours=15)
    per_row_cutoff = pd.DatetimeIndex([min(pd.Timestamp(limit), cutoff) for limit in day_last_completed])
    eligible = frame.loc[(frame.index >= day_open) & (frame.index + duration <= per_row_cutoff)].copy()
    if eligible.empty:
        return None, "CUTOFF_MISMATCH"

    rows_per_candle = 45 // interval_minutes
    completed_rows = []
    for trading_day, daily in eligible.groupby(eligible.index.normalize(), sort=True):
        expected_open = pd.Timestamp(trading_day) + pd.Timedelta(hours=9)
        offsets = (daily.index - expected_open).total_seconds() / 60.0
        if any(offset < 0 or offset >= 360 or offset % interval_minutes != 0 for offset in offsets):
            return None, "MALFORMED_INTERVAL"
        if len(daily) > 1 and not (daily.index.to_series().diff().dropna() == duration).all():
            return None, "MALFORMED_INTERVAL"
        daily = daily.copy()
        daily["__slot"] = (offsets // 45).astype(int)
        for slot, group in daily.groupby("__slot", sort=True):
            bin_start = expected_open + pd.Timedelta(minutes=int(slot) * 45)
            bin_end = bin_start + pd.Timedelta(minutes=45)
            allowed_end = min(pd.Timestamp(trading_day) + pd.Timedelta(hours=15), cutoff)
            if bin_end > allowed_end:
                continue
            expected_index = pd.date_range(bin_start, periods=rows_per_candle, freq=f"{interval_minutes}min")
            if len(group) != rows_per_candle or not group.index.equals(expected_index):
                return None, "PARTIAL_BAR_CONTAMINATION"
            completed_rows.append((
                bin_end, float(group["Open"].iloc[0]), float(group["High"].max()),
                float(group["Low"].min()), float(group["Close"].iloc[-1]),
                float(group["Volume"].sum()),
            ))

    if not completed_rows:
        return None, "INSUFFICIENT_COMPLETED_45M_BARS"
    completed = pd.DataFrame(
        completed_rows,
        columns=["completed_45m_timestamp", "Open", "High", "Low", "Close", "Volume"],
    ).set_index("completed_45m_timestamp")
    if completed.index.has_duplicates or not completed.index.is_monotonic_increasing:
        return None, "INVALID_COMPLETED_45M_INDEX"
    if completed.index[-1] != cutoff:
        return None, "CUTOFF_MISMATCH"
    if len(completed) < BB_WINDOW:
        return None, "INSUFFICIENT_COMPLETED_45M_BARS"
    return completed, "VALID"


def get_strictly_sliced_45m_df(stock_code: str, cutoff_dt: datetime) -> Tuple[Optional[pd.DataFrame], str, str]:
    analyzer = Intraday45mAnalyzer()
    if not hasattr(analyzer, "fetch_canonical_15m_data"):
        return None, "NONE", "REQUIRED_SOURCE_DATA_MISSING"
    try:
        raw_df, source, fetch_error = analyzer.fetch_canonical_15m_data(stock_code)
    except Exception:
        logger.warning("[BB-ATR] intraday fetch failed for %s", stock_code, exc_info=True)
        return None, "NONE", "INTRADAY_FETCH_FAILED"
    if fetch_error != "NONE" or raw_df is None:
        return None, str(source or "NONE"), str(fetch_error or "REQUIRED_SOURCE_DATA_MISSING")
    completed, quality = prepare_completed_45m_data(raw_df, str(source), cutoff_dt)
    return completed, str(source), quality


def _authoritative_floor(item: Dict[str, Any], policy_row: Optional[Dict[str, Any]]) -> Optional[float]:
    v4_floor = _finite_float(item.get("effective_exit_line"))
    if v4_floor is None or v4_floor <= 0:
        return None
    floors = [v4_floor]
    if policy_row and policy_row.get("display_allowed"):
        shadow_floor = _finite_float(policy_row.get("shadow_effective_exit"))
        if shadow_floor is not None and shadow_floor > 0:
            floors.append(shadow_floor)
    return max(floors)


def generate_bb_atr_advisory(
    item: Dict[str, Any],
    asof_dt: datetime,
    session_code: str,
    policy_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a complete fail-closed payload without mutating the input."""
    priority = _higher_priority_state(item, policy_row)
    if priority:
        return _suppress(item, asof_dt, session_code, priority)
    avg_buy_price = _finite_float(item.get("avg_buy_price"))
    if avg_buy_price is None or avg_buy_price <= 0:
        return make_no_advisory(item, asof_dt, session_code, "INVALID_AVG_BUY_PRICE")
    atr = _finite_float(item.get("current_completed_atr"))
    if atr is None or atr <= 0:
        return make_no_advisory(item, asof_dt, session_code, "INVALID_CURRENT_COMPLETED_ATR")
    is_etf = resolve_tick_regime(item)
    if is_etf is None:
        return make_no_advisory(item, asof_dt, session_code, "UNSUPPORTED_TICK_REGIME")
    authoritative_floor = _authoritative_floor(item, policy_row)
    if authoritative_floor is None:
        return make_no_advisory(item, asof_dt, session_code, "MISSING_AUTHORITATIVE_EXISTING_FLOOR")
    try:
        cutoff = resolve_cutoff(asof_dt, session_code)
    except ValueError as exc:
        return make_no_advisory(item, asof_dt, session_code, str(exc))
    if _as_kst_naive(asof_dt) < _as_kst_naive(cutoff):
        return make_no_advisory(item, asof_dt, session_code, "REPORT_BEFORE_COMPLETED_CUTOFF")

    completed, source, quality = get_strictly_sliced_45m_df(
        str(item.get("stock_code") or "").zfill(6), cutoff
    )
    if completed is None:
        return make_no_advisory(item, asof_dt, session_code, quality)
    if len(completed) < BB_WINDOW:
        return make_no_advisory(item, asof_dt, session_code, "INSUFFICIENT_COMPLETED_45M_BARS")
    completed_close = _finite_float(completed["Close"].iloc[-1])
    if completed_close is None or completed_close <= 0:
        return make_no_advisory(item, asof_dt, session_code, "INVALID_COMPLETED_CLOSE")
    bb_lower, bb_middle, bb_upper = calculate_bb26_2(completed)
    if not all(math.isfinite(value) for value in (bb_lower, bb_middle, bb_upper)):
        return make_no_advisory(item, asof_dt, session_code, "BB_INVALID")
    if not bb_lower < bb_middle < bb_upper:
        return make_no_advisory(item, asof_dt, session_code, "BB_INVALID_ORDER")
    try:
        lower_threshold, upper_threshold = calculate_confirmation_thresholds(bb_lower, bb_upper, is_etf)
    except (TypeError, ValueError, OverflowError):
        return make_no_advisory(item, asof_dt, session_code, "KRX_LEGAL_TICK_FAILURE")

    upper_confirmed = completed_close >= upper_threshold
    lower_confirmed = completed_close <= lower_threshold
    profit_state = "PROFIT" if completed_close > avg_buy_price else "LOSS_OR_BREAKEVEN"
    if upper_confirmed:
        mode = "PROFIT_TRAILING" if profit_state == "PROFIT" else "RECOVERY_TRAILING"
        signal_text = "상단 +3호가 CONFIRMED"
        signal_code = "UPPER_CONFIRMED"
    elif lower_confirmed:
        mode = "PROFIT_PROTECTION" if profit_state == "PROFIT" else "LOSS_MINIMIZATION"
        signal_text = "하단 -3호가 CONFIRMED"
        signal_code = "LOWER_CONFIRMED"
    else:
        mode = "BASE"
        signal_text = "완료봉 밴드내"
        signal_code = "WITHIN_BANDS"

    bb_reference_floor: Optional[int] = None
    if mode != "BASE":
        raw_atr_reference = completed_close - ATR_MULTIPLIER * atr
        if mode == "PROFIT_PROTECTION":
            raw_atr_reference = max(raw_atr_reference, avg_buy_price)
        try:
            bb_reference_floor = normalize_to_legal_tick(raw_atr_reference, "down", is_etf)
        except ValueError:
            return make_no_advisory(item, asof_dt, session_code, "KRX_LEGAL_TICK_FAILURE")

    effective_floor = max(
        authoritative_floor,
        float(bb_reference_floor) if bb_reference_floor is not None else authoritative_floor,
    )
    advisory_text = (
        f"{signal_text} · 참고선 {bb_reference_floor:,}원"
        if bb_reference_floor is not None
        else "완료봉 밴드내 · 기존 V4 방어선 유지"
    )
    result = _base_result(item, asof_dt, session_code)
    result.update(
        overlay_status="ACTIVE", data_quality="VALID", reason_codes=[signal_code],
        completed_45m_timestamp=completed.index[-1].strftime("%Y-%m-%d %H:%M:%S"),
        completed_close=round(completed_close, 4),
        bb_middle=round(bb_middle, 4), bb_upper=round(bb_upper, 4), bb_lower=round(bb_lower, 4),
        upper_confirmation_threshold=upper_threshold,
        lower_confirmation_threshold=lower_threshold,
        upper_confirmed=bool(upper_confirmed), lower_confirmed=bool(lower_confirmed),
        profit_reference_price=avg_buy_price, profit_state=profit_state, bb_mode=mode,
        atr_used=atr, bb_reference_floor=bb_reference_floor,
        authoritative_existing_floor=authoritative_floor,
        effective_advisory_floor=effective_floor, advisory_text=advisory_text,
        mode=mode, lower_conf=lower_threshold, upper_conf=upper_threshold,
        advisory_floor=effective_floor, advisory_msg=advisory_text, error=None,
    )
    return result
