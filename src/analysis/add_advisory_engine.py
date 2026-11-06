"""
Phase 1: 30m ADD ADVISORY Sidecar Engine
- Calculates VWAP9/26, OBV/OBV9, Chaikin Oscillator (13, 26) on completed 30m bars
- Evaluates ADD_STRONG, ADD_WATCH, ADD_BLOCKED, NEUTRAL, UNKNOWN
- Tracks alert_candidate state transitions
- Sidecar / Shadow ONLY: strictly preserves existing V1.0 technical_state and scan_journal semantics
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional, List
from src.utils.logger import logger
from src.analysis.intraday_analysis import Intraday30mAnalyzer

class AddAdvisory30mEngine:
    """Sidecar evaluation engine for 30m ADD ADVISORY"""

    def __init__(self, intraday_analyzer: Optional[Intraday30mAnalyzer] = None):
        self.intraday_analyzer = intraday_analyzer or Intraday30mAnalyzer()

    @staticmethod
    def calculate_canonical_30m_indicators(df_30m: pd.DataFrame) -> Dict[str, Any]:
        """Return the ADD sidecar's canonical raw indicators for prepared 30m bars.

        The caller owns bar-completeness validation.  Keeping the calculation
        here prevents WMA9 OBV, VWAP and Chaikin definitions from being copied
        by read-only consumers such as the sell-warning sidecar.
        """
        if not isinstance(df_30m, pd.DataFrame) or len(df_30m) < 26:
            raise ValueError("INSUFFICIENT_COMPLETED_30M_BARS")

        required = {"High", "Low", "Close", "Volume"}
        if not required.issubset(df_30m.columns):
            raise ValueError("MALFORMED_30M_OHLCV")

        typical_price = (df_30m["High"] + df_30m["Low"] + df_30m["Close"]) / 3.0
        tp_vol = typical_price * df_30m["Volume"]
        vol_sum9 = df_30m["Volume"].rolling(window=9).sum()
        vol_sum26 = df_30m["Volume"].rolling(window=26).sum()
        vwap9_series = tp_vol.rolling(window=9).sum() / (vol_sum9 + 1e-9)
        vwap26_series = tp_vol.rolling(window=26).sum() / (vol_sum26 + 1e-9)

        close = df_30m["Close"]
        vol = df_30m["Volume"]
        price_diff = close.diff()
        obv_direction = np.where(price_diff > 0, 1.0, np.where(price_diff < 0, -1.0, 0.0))
        obv_series = pd.Series(np.cumsum(obv_direction * vol), index=df_30m.index)
        wma_weights = np.arange(1, 10, dtype=float)
        obv9_series = obv_series.rolling(window=9).apply(
            lambda window: np.dot(window, wma_weights) / wma_weights.sum(), raw=True
        )
        obv_gap_series = obv_series - obv9_series
        obv_gap_delta_series = obv_gap_series.diff()

        high = df_30m["High"]
        low = df_30m["Low"]
        hl_diff = high - low
        mfm = np.where(
            hl_diff == 0,
            0.0,
            ((close - low) - (high - close)) / (hl_diff + 1e-9),
        )
        mfv = mfm * vol
        adl_series = pd.Series(mfv, index=df_30m.index).cumsum()
        chaikin_series = (
            adl_series.ewm(span=13, adjust=False).mean()
            - adl_series.ewm(span=26, adjust=False).mean()
        )
        chaikin_delta_series = chaikin_series.diff()

        v9 = float(vwap9_series.iloc[-1])
        v26 = float(vwap26_series.iloc[-1])
        is_gold_hist = vwap9_series > vwap26_series
        is_gold_curr = v9 > v26
        age = 0
        if is_gold_curr:
            vwap_state = "VWAP_GOLD"
            for k in range(len(is_gold_hist) - 1, 0, -1):
                if is_gold_hist.iloc[k]:
                    if not is_gold_hist.iloc[k - 1]:
                        age = (len(is_gold_hist) - 1) - k
                        break
                else:
                    break
            vwap_cross_state = "VWAP_GOLD_CROSS" if age == 0 else "VWAP_GOLD"
        else:
            vwap_state = "VWAP_DEAD"
            for k in range(len(is_gold_hist) - 1, 0, -1):
                if not is_gold_hist.iloc[k]:
                    if is_gold_hist.iloc[k - 1]:
                        age = (len(is_gold_hist) - 1) - k
                        break
                else:
                    break
            vwap_cross_state = "VWAP_DEAD_CROSS" if age == 0 else "VWAP_DEAD"

        obv_val = float(obv_series.iloc[-1])
        obv9_val = float(obv9_series.iloc[-1])
        obv_prev_val = float(obv_series.iloc[-2])
        obv9_prev_val = float(obv9_series.iloc[-2])
        if obv_val > obv9_val:
            obv_state = "OBV_GOLD_CROSS" if obv_prev_val <= obv9_prev_val else "OBV_GOLD"
        else:
            obv_state = "OBV_DEAD_CROSS" if obv_prev_val >= obv9_prev_val else "OBV_DEAD"

        obv_gap = float(obv_gap_series.iloc[-1])
        obv_gap_delta = float(obv_gap_delta_series.iloc[-1])
        if obv_gap_delta > 1e-6:
            obv_gap_state = "EXPANDING"
        elif obv_gap_delta < -1e-6:
            obv_gap_state = "CONTRACTING"
        else:
            obv_gap_state = "STABLE"

        chaikin_value = float(chaikin_series.iloc[-1])
        chaikin_prev = float(chaikin_series.iloc[-2])
        chaikin_delta = float(chaikin_delta_series.iloc[-1])
        if chaikin_delta > 1e-6:
            chaikin_state = "CHAIKIN_RISING"
        elif chaikin_delta < -1e-6:
            chaikin_state = "CHAIKIN_FALLING"
        else:
            chaikin_state = "CHAIKIN_FLAT"

        return {
            "vwap9": v9,
            "vwap26": v26,
            "vwap_state": vwap_state,
            "vwap_cross_state": vwap_cross_state,
            "vwap_cross_age": age,
            "obv": obv_val,
            "obv9": obv9_val,
            "obv_state": obv_state,
            "obv_gap": obv_gap,
            "obv_gap_delta": obv_gap_delta,
            "obv_gap_state": obv_gap_state,
            "chaikin_value": chaikin_value,
            "chaikin_prev": chaikin_prev,
            "chaikin_delta": chaikin_delta,
            "chaikin_state": chaikin_state,
        }

    def evaluate_stock_advisory(
        self,
        stock_code: str,
        trading_date: str,
        bar_timestamp: str,
        technical_state_reference: str = "UNKNOWN",
        latest_previous_advisory: Optional[Dict[str, Any]] = None,
        holding_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluates 30m ADD ADVISORY for a single stock code on completed 30m bars.
        """
        code = str(stock_code).strip().zfill(6)
        default_result = {
            "trading_date": trading_date,
            "stock_code": code,
            "bar_timestamp": bar_timestamp,
            "evaluated_at": f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST",
            "vwap9": None,
            "vwap26": None,
            "vwap_state": "UNKNOWN",
            "vwap_cross_state": "UNKNOWN",
            "vwap_cross_age": None,
            "obv": None,
            "obv9": None,
            "obv_state": "UNKNOWN",
            "obv_gap": None,
            "obv_gap_delta": None,
            "obv_gap_state": "UNKNOWN",
            "chaikin_value": None,
            "chaikin_prev": None,
            "chaikin_delta": None,
            "chaikin_state": "UNKNOWN",
            "technical_state_reference": technical_state_reference,
            "add_advisory_state": "UNKNOWN",
            "alert_candidate": 0,
            "reason_codes": "CRITICAL_DATA_MISSING",
            "data_quality": "INVALID (NO_INTRADAY_DATA)"
        }

        # 1. Fetch 15m Canonical OHLCV Data
        df_15m, source_name, err_code = self.intraday_analyzer.fetch_canonical_15m_data(code)
        if df_15m is None or len(df_15m) < 15:
            default_result["data_quality"] = f"INVALID ({err_code if err_code != 'NONE' else 'INSUFFICIENT_BARS'})"
            return default_result

        try:
            # 2. Resample to 30m OHLCV
            from src.analysis.m30_bars import prepare_completed_30m_data
            cutoff = datetime.strptime(bar_timestamp, "%Y-%m-%d %H:%M:%S")
            df_30m, quality = prepare_completed_30m_data(df_15m, source_name, cutoff, allow_sparse_completed=True)
            if df_30m is None:
                default_result["data_quality"] = "INVALID_30M_"+quality
                return default_result

            if len(df_30m) < 26:
                default_result["data_quality"] = f"INVALID (INSUFFICIENT_BARS_{len(df_30m)})"
                return default_result

            # 3. Indicator Calculations (canonical ADD definitions)
            metrics = self.calculate_canonical_30m_indicators(df_30m)
            v9 = metrics["vwap9"]
            v26 = metrics["vwap26"]
            vwap_state = metrics["vwap_state"]
            vwap_cross_state = metrics["vwap_cross_state"]
            vwap_cross_age = metrics["vwap_cross_age"]
            obv_val = metrics["obv"]
            obv9_val = metrics["obv9"]
            obv_state = metrics["obv_state"]
            obv_gap = metrics["obv_gap"]
            obv_gap_delta = metrics["obv_gap_delta"]
            obv_gap_state = metrics["obv_gap_state"]
            ch_val = metrics["chaikin_value"]
            ch_prev = metrics["chaikin_prev"]
            ch_delta = metrics["chaikin_delta"]
            chaikin_state = metrics["chaikin_state"]

            # ADD ADVISORY State Determination
            reasons = []
            is_vwap_gold = (v9 > v26)
            is_obv_gold = (obv_val > obv9_val)
            is_gap_ok = (obv_gap_state in ["EXPANDING", "STABLE"])
            is_cho_rising = (chaikin_state == "CHAIKIN_RISING")
            is_not_damaged = (technical_state_reference != "DAMAGED")

            # Condition A: ADD_STRONG
            if is_vwap_gold and is_obv_gold and is_gap_ok and is_cho_rising and is_not_damaged:
                advisory_state = "ADD_STRONG"
                reasons.append("VWAP_GOLD,OBV_GOLD,GAP_EXPANDING_OR_STABLE,CHAIKIN_RISING,NOT_DAMAGED")
            # Condition B: ADD_WATCH
            elif is_vwap_gold and (is_obv_gold or is_cho_rising) and is_not_damaged:
                advisory_state = "ADD_WATCH"
                if is_obv_gold:
                    reasons.append("VWAP_GOLD,OBV_GOLD")
                if is_cho_rising:
                    reasons.append("VWAP_GOLD,CHAIKIN_RISING")
            # Condition C: ADD_BLOCKED
            elif (not is_vwap_gold) or (technical_state_reference == "DAMAGED") or (is_obv_gold and obv_gap_state == "CONTRACTING" and chaikin_state == "CHAIKIN_FALLING"):
                advisory_state = "ADD_BLOCKED"
                if not is_vwap_gold:
                    reasons.append("VWAP_DEAD")
                if technical_state_reference == "DAMAGED":
                    reasons.append("TECHNICAL_STATE_DAMAGED")
                if is_obv_gold and obv_gap_state == "CONTRACTING" and chaikin_state == "CHAIKIN_FALLING":
                    reasons.append("OBV_GAP_CONTRACTING_AND_CHAIKIN_FALLING")
            # Condition D: NEUTRAL
            else:
                advisory_state = "NEUTRAL"
                reasons.append("NEUTRAL_CONDITIONS")

            from src.analysis.advisory_safety import priority
            protection = priority(holding_context)
            if protection:
                advisory_state = "ADD_BLOCKED"
                reasons.insert(0, protection)

            # Determine alert_candidate
            prev_state = latest_previous_advisory.get("add_advisory_state") if latest_previous_advisory else None
            alert_candidate = 0

            if prev_state is None:
                if advisory_state in ["ADD_STRONG", "ADD_WATCH"]:
                    alert_candidate = 1
            else:
                if prev_state != advisory_state:
                    if (prev_state == "NEUTRAL" and advisory_state in ["ADD_WATCH", "ADD_STRONG"]) or \
                       (prev_state == "ADD_WATCH" and advisory_state == "ADD_STRONG") or \
                       (prev_state == "ADD_BLOCKED" and advisory_state in ["ADD_WATCH", "ADD_STRONG"]):
                        alert_candidate = 1

            return {
                "trading_date": trading_date,
                "stock_code": code,
                "bar_timestamp": bar_timestamp,
                "evaluated_at": f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST",
                "vwap9": round(v9, 2),
                "vwap26": round(v26, 2),
                "vwap_state": vwap_state,
                "vwap_cross_state": vwap_cross_state,
                "vwap_cross_age": int(vwap_cross_age),
                "obv": round(obv_val, 2),
                "obv9": round(obv9_val, 2),
                "obv_state": obv_state,
                "obv_gap": round(obv_gap, 2),
                "obv_gap_delta": round(obv_gap_delta, 2),
                "obv_gap_state": obv_gap_state,
                "chaikin_value": round(ch_val, 2),
                "chaikin_prev": round(ch_prev, 2),
                "chaikin_delta": round(ch_delta, 2),
                "chaikin_state": chaikin_state,
                "technical_state_reference": technical_state_reference,
                "add_advisory_state": advisory_state,
                "alert_candidate": alert_candidate,
                "reason_codes": ",".join(reasons),
                "data_quality": f"VALID ({len(df_30m)} bars)"
            }

        except Exception as e:
            logger.error(f"[AddAdvisory30mEngine] Error evaluating {code}: {e}", exc_info=True)
            default_result["data_quality"] = f"INVALID (EXCEPTION: {e})"
            return default_result
