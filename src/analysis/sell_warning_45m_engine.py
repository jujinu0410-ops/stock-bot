"""Completed-45m composite sell-warning sidecar.

This module is advisory-only. It consumes canonical raw indicator outputs,
does not mutate trading state, and has no order capability.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd

from src.analysis.add_advisory_engine import AddAdvisory45mEngine
from src.analysis.bollinger_atr_strategy import prepare_completed_45m_data
from src.analysis.intraday_analysis import Intraday45mAnalyzer
from src.utils.logger import logger


SELL_WARNING_VERSION = "SELL_WARNING_45M_V1"
ADX_BEAR_THRESHOLD = 22.0


def _finite_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class SellWarning45mEngine:
    """Read-only consumer that classifies deterioration on completed 45m bars."""

    def __init__(self, intraday_analyzer: Optional[Intraday45mAnalyzer] = None):
        self.intraday_analyzer = intraday_analyzer or Intraday45mAnalyzer()

    @staticmethod
    def _base_result(stock_code: str, trading_date: str, bar_timestamp: str) -> Dict[str, Any]:
        return {
            "trading_date": trading_date,
            "stock_code": str(stock_code).strip().zfill(6),
            "bar_timestamp": bar_timestamp,
            "evaluated_at": f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST",
            "engine_version": SELL_WARNING_VERSION,
            "sell_warning_state": "UNKNOWN",
            "sell_confirmed": 0,
            "data_quality": "INVALID",
            "reason_codes": "CRITICAL_DATA_MISSING",
            "bearish_axes_count": None,
            "price_weakness": None,
            "obv_weakness": None,
            "chaikin_weakness": None,
            "bear_trend": None,
            "chaikin_recovery_conflict": None,
            "bull_trend_conflict": None,
            "vwap_dead": None,
            "is_price_below_cloud_45m": None,
            "completed_45m_timestamp": None,
            "completed_45m_bar_count": 0,
            "vwap9": None,
            "vwap26": None,
            "close_45m": None,
            "cloud_bottom_45m": None,
            "obv": None,
            "obv_wma9": None,
            "obv_gap": None,
            "obv_gap_delta": None,
            "obv_gap_state": "UNKNOWN",
            "chaikin_value": None,
            "chaikin_delta": None,
            "chaikin_state": "UNKNOWN",
            "adx_14_45m": None,
            "plus_di_45m": None,
            "minus_di_45m": None,
        }

    def evaluate_canonical_axes(
        self,
        stock_code: str,
        trading_date: str,
        bar_timestamp: str,
        add_metrics: Dict[str, Any],
        trend_metrics: Dict[str, Any],
        *,
        completed_bar_count: int = 0,
    ) -> Dict[str, Any]:
        """Classify four independent axes from canonical raw values only."""
        result = self._base_result(stock_code, trading_date, bar_timestamp)
        required = {
            "vwap9": add_metrics.get("vwap9"),
            "vwap26": add_metrics.get("vwap26"),
            "obv": add_metrics.get("obv"),
            "obv_wma9": add_metrics.get("obv9"),
            "obv_gap": add_metrics.get("obv_gap"),
            "obv_gap_delta": add_metrics.get("obv_gap_delta"),
            "chaikin_value": add_metrics.get("chaikin_value"),
            "chaikin_delta": add_metrics.get("chaikin_delta"),
            "close_45m": trend_metrics.get("close_45m"),
            "cloud_bottom_45m": trend_metrics.get("cloud_bottom_45m"),
            "adx_14_45m": trend_metrics.get("adx_14_45m"),
            "plus_di_45m": trend_metrics.get("plus_di_45m"),
            "minus_di_45m": trend_metrics.get("minus_di_45m"),
        }
        values = {name: _finite_float(value) for name, value in required.items()}
        missing = [name for name, value in values.items() if value is None]
        if missing:
            result["reason_codes"] = "MISSING_CANONICAL_VALUES:" + ",".join(sorted(missing))
            return result

        vwap_dead = values["vwap9"] < values["vwap26"]
        below_cloud = values["close_45m"] < values["cloud_bottom_45m"]
        price_weakness = vwap_dead or below_cloud

        obv_below_wma9 = values["obv"] < values["obv_wma9"]
        obv_gap_state = str(add_metrics.get("obv_gap_state") or "UNKNOWN").upper()
        obv_weakness_confirmation = (
            values["obv_gap"] < 0
            or values["obv_gap_delta"] < 0
            or obv_gap_state == "CONTRACTING"
        )
        obv_weakness = obv_below_wma9 and obv_weakness_confirmation

        chaikin_state = str(add_metrics.get("chaikin_state") or "UNKNOWN").upper()
        chaikin_negative = values["chaikin_value"] < 0
        chaikin_falling = values["chaikin_delta"] < 0 or chaikin_state == "CHAIKIN_FALLING"
        chaikin_weakness = chaikin_negative and chaikin_falling
        chaikin_recovery_conflict = chaikin_negative and (
            values["chaikin_delta"] > 0 or chaikin_state == "CHAIKIN_RISING"
        )

        bear_trend = (
            values["adx_14_45m"] >= ADX_BEAR_THRESHOLD
            and values["minus_di_45m"] > values["plus_di_45m"]
        )
        bull_trend_conflict = (
            values["adx_14_45m"] >= ADX_BEAR_THRESHOLD
            and values["plus_di_45m"] > values["minus_di_45m"]
        )

        axes = {
            "PRICE_WEAKNESS": price_weakness,
            "OBV_WEAKNESS": obv_weakness,
            "CHAIKIN_WEAKNESS": chaikin_weakness,
            "BEAR_TREND": bear_trend,
        }
        bearish_axes_count = sum(bool(value) for value in axes.values())
        money_axis_confirmed = obv_weakness or chaikin_weakness
        trend_or_double_money = bear_trend or (obv_weakness and chaikin_weakness)
        has_recovery_conflict = chaikin_recovery_conflict or bull_trend_conflict
        sell_warning = (
            price_weakness
            and bearish_axes_count >= 3
            and money_axis_confirmed
            and trend_or_double_money
            and not has_recovery_conflict
        )

        if sell_warning:
            state = "SELL_WARNING"
        elif bearish_axes_count >= 2:
            state = "CAUTION"
        else:
            state = "NORMAL"

        reasons = [name for name, active in axes.items() if active]
        if chaikin_recovery_conflict:
            reasons.append("CHAIKIN_RECOVERY_CONFLICT")
        if bull_trend_conflict:
            reasons.append("BULL_TREND_CONFLICT")
        if not reasons:
            reasons.append("NO_COMPOSITE_WEAKNESS")

        result.update({
            "sell_warning_state": state,
            "data_quality": "VALID",
            "reason_codes": ",".join(reasons),
            "bearish_axes_count": bearish_axes_count,
            "price_weakness": int(price_weakness),
            "obv_weakness": int(obv_weakness),
            "chaikin_weakness": int(chaikin_weakness),
            "bear_trend": int(bear_trend),
            "chaikin_recovery_conflict": int(chaikin_recovery_conflict),
            "bull_trend_conflict": int(bull_trend_conflict),
            "vwap_dead": int(vwap_dead),
            "is_price_below_cloud_45m": int(below_cloud),
            "completed_45m_timestamp": bar_timestamp,
            "completed_45m_bar_count": int(completed_bar_count),
            "vwap9": values["vwap9"],
            "vwap26": values["vwap26"],
            "close_45m": values["close_45m"],
            "cloud_bottom_45m": values["cloud_bottom_45m"],
            "obv": values["obv"],
            "obv_wma9": values["obv_wma9"],
            "obv_gap": values["obv_gap"],
            "obv_gap_delta": values["obv_gap_delta"],
            "obv_gap_state": obv_gap_state,
            "chaikin_value": values["chaikin_value"],
            "chaikin_delta": values["chaikin_delta"],
            "chaikin_state": chaikin_state,
            "adx_14_45m": values["adx_14_45m"],
            "plus_di_45m": values["plus_di_45m"],
            "minus_di_45m": values["minus_di_45m"],
        })
        return result

    def evaluate_stock_warning(
        self,
        stock_code: str,
        trading_date: str,
        bar_timestamp: str,
    ) -> Dict[str, Any]:
        """Fetch once, strictly slice at cutoff, then evaluate canonical axes."""
        result = self._base_result(stock_code, trading_date, bar_timestamp)
        try:
            cutoff = pd.Timestamp(bar_timestamp)
        except Exception:
            result["reason_codes"] = "INVALID_BAR_TIMESTAMP"
            return result

        try:
            raw_df, source, fetch_error = self.intraday_analyzer.fetch_canonical_15m_data(
                result["stock_code"]
            )
        except Exception:
            logger.warning("[SellWarning45m] intraday fetch failed for %s", result["stock_code"], exc_info=True)
            result["reason_codes"] = "INTRADAY_FETCH_FAILED"
            return result

        if fetch_error != "NONE" or raw_df is None:
            result["reason_codes"] = str(fetch_error or "REQUIRED_SOURCE_DATA_MISSING")
            return result

        completed, quality = prepare_completed_45m_data(raw_df, str(source), cutoff)
        if completed is None or quality != "VALID":
            result["reason_codes"] = quality
            result["data_quality"] = f"INVALID ({quality})"
            return result

        try:
            add_metrics = AddAdvisory45mEngine.calculate_canonical_45m_indicators(completed)
            trend_metrics = Intraday45mAnalyzer.calculate_canonical_45m_trend(completed)
            evaluated = self.evaluate_canonical_axes(
                result["stock_code"],
                trading_date,
                bar_timestamp,
                add_metrics,
                trend_metrics,
                completed_bar_count=len(completed),
            )
            evaluated["data_quality"] = f"VALID ({len(completed)} completed bars)"
            evaluated["completed_45m_timestamp"] = completed.index[-1].strftime("%Y-%m-%d %H:%M:%S")
            return evaluated
        except Exception as exc:
            logger.error("[SellWarning45m] indicator evaluation failed for %s: %s", result["stock_code"], exc_info=True)
            result["reason_codes"] = f"INDICATOR_ERROR:{type(exc).__name__}"
            return result
