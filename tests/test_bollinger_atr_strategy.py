import copy
import math
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import main
import src.analysis.bollinger_atr_strategy as strategy


REQUIRED_SCHEMA = {
    "overlay_version", "overlay_scope", "overlay_status", "data_quality", "reason_codes",
    "report_session", "report_asof_kst", "completed_45m_timestamp", "completed_close",
    "bb_window", "bb_sigma", "bb_middle", "bb_upper", "bb_lower",
    "upper_confirmation_threshold", "lower_confirmation_threshold", "upper_confirmed",
    "lower_confirmed", "profit_basis", "profit_reference_price", "profit_state", "bb_mode",
    "atr_used", "atr_source", "atr_multiplier", "bb_reference_floor",
    "authoritative_existing_floor", "effective_advisory_floor", "higher_priority_state",
    "overlay_suppressed", "advisory_text", "actual_order_impact",
}


def raw_15m(final_close=10_000.0, cutoff="2026-09-11 15:00:00", include_post_cutoff=True):
    rows = []
    index = []
    for day in pd.date_range("2026-09-07", "2026-09-11", freq="D"):
        for stamp in pd.date_range(day + pd.Timedelta(hours=9), day + pd.Timedelta(hours=14, minutes=45), freq="15min"):
            price = 10_000.0
            index.append(stamp)
            rows.append([price, price + 20, price - 20, price, 1_000])
    frame = pd.DataFrame(rows, index=pd.DatetimeIndex(index), columns=["Open", "High", "Low", "Close", "Volume"])
    target = pd.Timestamp(cutoff) - pd.Timedelta(minutes=15)
    frame.loc[target, ["Open", "Close"]] = final_close
    frame.loc[target, "High"] = final_close + 20
    frame.loc[target, "Low"] = final_close - 20
    if include_post_cutoff:
        # Explicit partial 15:00~15:30 data. It must never enter the 15:00
        # completed candle or completed_close.
        for stamp in pd.date_range("2026-09-11 15:00", periods=3, freq="15min"):
            frame.loc[stamp] = [50_000, 50_020, 49_980, 50_000, 999]
        frame = frame.sort_index()
    return frame


def base_item(**updates):
    item = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "avg_buy_price": 9_000.0,
        "current_price": 99_999.0,
        "current_completed_atr": 1_000.0,
        "effective_exit_line": 9_500.0,
        "is_etf": False,
        "trade_mode": "NORMAL",
        "data_validity_flag": 1,
        "data_hold_reason": "정상",
        "is_stop_breached": False,
        "action_status": "계속 보유",
        "recommended_order_qty": 0,
        "confirmed_stop_price": 9_500.0,
        "prev_confirmed_stop": 9_400.0,
        "profit_trail": 0,
        "auto_order_enabled": True,
    }
    item.update(updates)
    return item


class TestBollingerATRStrategy(unittest.TestCase):
    ASOF = datetime(2026, 9, 11, 15, 35)

    def generate(self, *, final_close, avg_buy, bb=(9_000.0, 10_000.0, 11_000.0), item_updates=None, session="1535", policy_row=None, raw=None):
        cutoff = strategy.resolve_cutoff(self.ASOF, session).strftime("%Y-%m-%d %H:%M:%S")
        source = raw if raw is not None else raw_15m(final_close, cutoff)
        item = base_item(avg_buy_price=avg_buy, **(item_updates or {}))
        with patch.object(strategy.Intraday45mAnalyzer, "fetch_canonical_15m_data", return_value=(source, "TEST_15M_BAR_START", "NONE")), patch.object(strategy, "calculate_bb26_2", return_value=bb):
            return strategy.generate_bb_atr_advisory(item, self.ASOF, session, policy_row)

    def test_cutoffs_are_canonical_completed_times(self):
        self.assertEqual(strategy.resolve_cutoff(self.ASOF, "1120").strftime("%H:%M"), "11:15")
        self.assertEqual(strategy.resolve_cutoff(self.ASOF, "1335").strftime("%H:%M"), "13:30")
        self.assertEqual(strategy.resolve_cutoff(self.ASOF, "1535").strftime("%H:%M"), "15:00")

    def test_each_session_final_completed_timestamp(self):
        for session, expected in (("1120", "11:15:00"), ("1335", "13:30:00"), ("1535", "15:00:00")):
            with self.subTest(session=session):
                result = self.generate(final_close=10_500, avg_buy=9_000, session=session)
                self.assertEqual(result["completed_45m_timestamp"], f"2026-09-11 {expected}")

    def test_post_1500_partial_data_is_excluded(self):
        result = self.generate(final_close=10_500, avg_buy=9_000)
        self.assertEqual(result["completed_close"], 10_500)
        self.assertNotEqual(result["completed_close"], 50_000)

    def test_current_price_is_never_used_for_mode(self):
        result = self.generate(final_close=10_500, avg_buy=9_000, item_updates={"current_price": 1_000_000})
        self.assertEqual(result["bb_mode"], "BASE")
        self.assertEqual(result["completed_close"], 10_500)

    def test_profit_inside_band_is_base(self):
        self.assertEqual(self.generate(final_close=10_500, avg_buy=9_000)["bb_mode"], "BASE")

    def test_loss_above_middle_without_upper_break_is_base(self):
        self.assertEqual(self.generate(final_close=10_500, avg_buy=11_000)["bb_mode"], "BASE")

    def test_four_confirmed_state_machine_cases(self):
        cases = [
            (12_000, 10_000, "PROFIT_TRAILING"),
            (12_000, 13_000, "RECOVERY_TRAILING"),
            (8_000, 7_000, "PROFIT_PROTECTION"),
            (8_000, 9_000, "LOSS_MINIMIZATION"),
        ]
        for close, avg, expected in cases:
            with self.subTest(close=close, avg=avg):
                self.assertEqual(self.generate(final_close=close, avg_buy=avg)["bb_mode"], expected)

    def test_breakeven_is_loss_or_breakeven(self):
        result = self.generate(final_close=10_500, avg_buy=10_500)
        self.assertEqual(result["profit_state"], "LOSS_OR_BREAKEVEN")

    def test_atr_point_8_reference_is_real_and_legal(self):
        result = self.generate(final_close=12_000, avg_buy=10_000, item_updates={"current_completed_atr": 1_000, "effective_exit_line": 10_000})
        self.assertEqual(result["atr_multiplier"], 0.8)
        self.assertEqual(result["atr_used"], 1_000)
        self.assertEqual(result["bb_reference_floor"], 11_200)
        self.assertEqual(result["effective_advisory_floor"], 11_200)

    def test_authoritative_policy_floor_can_only_strengthen(self):
        policy = {"display_allowed": True, "shadow_effective_exit": 11_500, "defense_state": "OFF", "loss_defense_trigger": "OFF"}
        result = self.generate(final_close=12_000, avg_buy=10_000, item_updates={"effective_exit_line": 10_000}, policy_row=policy)
        self.assertEqual(result["authoritative_existing_floor"], 11_500)
        self.assertEqual(result["effective_advisory_floor"], 11_500)

    def test_invalid_atr_and_average_fail_closed(self):
        cases = [
            ({"current_completed_atr": None}, "INVALID_CURRENT_COMPLETED_ATR"),
            ({"current_completed_atr": 0}, "INVALID_CURRENT_COMPLETED_ATR"),
            ({"current_completed_atr": float("inf")}, "INVALID_CURRENT_COMPLETED_ATR"),
            ({"avg_buy_price": None}, "INVALID_AVG_BUY_PRICE"),
            ({"avg_buy_price": 0}, "INVALID_AVG_BUY_PRICE"),
        ]
        for updates, reason in cases:
            with self.subTest(updates=updates):
                item = base_item(**updates)
                result = strategy.generate_bb_atr_advisory(item, self.ASOF, "1535")
                self.assertEqual(result["overlay_status"], "NO_ADVISORY")
                self.assertEqual(result["reason_codes"], [reason])

    def test_completed_bars_25_fail_closed(self):
        completed = pd.DataFrame({"Close": np.arange(25) + 10_000}, index=pd.date_range("2026-09-10", periods=25, freq="45min"))
        with patch.object(strategy, "get_strictly_sliced_45m_df", return_value=(completed, "TEST_15M_BAR_START", "VALID")):
            result = strategy.generate_bb_atr_advisory(base_item(), self.ASOF, "1535")
        self.assertEqual(result["reason_codes"], ["INSUFFICIENT_COMPLETED_45M_BARS"])

    def test_malformed_bollinger_fails_closed(self):
        result = self.generate(final_close=10_000, avg_buy=9_000, bb=(10_000, 10_000, 11_000))
        self.assertEqual(result["reason_codes"], ["BB_INVALID_ORDER"])

    def test_duplicate_nonmonotonic_and_irregular_timestamp_fail_closed(self):
        cutoff = strategy.resolve_cutoff(self.ASOF, "1535")
        raw = raw_15m()
        duplicate = pd.concat([raw.iloc[:1], raw]).sort_index()
        frame, reason = strategy.prepare_completed_45m_data(duplicate, "TEST_15M_BAR_START", cutoff)
        self.assertIsNone(frame)
        self.assertEqual(reason, "DUPLICATE_TIMESTAMP")
        frame, reason = strategy.prepare_completed_45m_data(raw.iloc[::-1], "TEST_15M_BAR_START", cutoff)
        self.assertIsNone(frame)
        self.assertEqual(reason, "NON_MONOTONIC_TIMESTAMP")
        irregular = raw.drop(pd.Timestamp("2026-09-11 10:45"))
        frame, reason = strategy.prepare_completed_45m_data(irregular, "TEST_15M_BAR_START", cutoff)
        self.assertIsNone(frame)
        self.assertIn(reason, {"MALFORMED_INTERVAL", "PARTIAL_BAR_CONTAMINATION"})

    def test_unknown_timestamp_semantics_fail_closed(self):
        frame, reason = strategy.prepare_completed_45m_data(raw_15m(), "UNKNOWN_SOURCE", strategy.resolve_cutoff(self.ASOF, "1535"))
        self.assertIsNone(frame)
        self.assertEqual(reason, "UNSUPPORTED_TIMESTAMP_SEMANTICS")

    def test_population_std_is_explicit(self):
        values = np.arange(1.0, 27.0)
        frame = pd.DataFrame({"Close": values})
        lower, middle, upper = strategy.calculate_bb26_2(frame)
        self.assertAlmostEqual(middle, values.mean())
        self.assertAlmostEqual(upper, values.mean() + 2 * values.std(ddof=0))
        self.assertAlmostEqual(lower, values.mean() - 2 * values.std(ddof=0))

    def test_legal_tick_three_steps_cross_every_boundary(self):
        cases = [
            (1_998, 2_005, 2_005, 1_998),
            (4_990, 5_010, 5_010, 4_990),
            (19_980, 20_050, 20_050, 19_980),
            (49_900, 50_100, 50_100, 49_900),
            (199_800, 200_500, 200_500, 199_800),
            (499_000, 501_000, 501_000, 499_000),
        ]
        for below, expected_up, above, expected_down in cases:
            with self.subTest(boundary=above):
                self.assertEqual(strategy.step_legal_ticks(below, 3, "up", False), expected_up)
                self.assertEqual(strategy.step_legal_ticks(above, 3, "down", False), expected_down)

    def test_thresholds_normalize_outward_before_stepping(self):
        lower, upper = strategy.calculate_confirmation_thresholds(10_002, 20_003, False)
        self.assertEqual(lower, 9_970)
        self.assertEqual(upper, 20_200)

    def test_etf_tick_and_unknown_product_regime(self):
        self.assertEqual(strategy.step_legal_ticks(10_000, 3, "up", True), 10_015)
        self.assertEqual(strategy.step_legal_ticks(10_000, 3, "down", True), 9_985)
        result = strategy.generate_bb_atr_advisory(base_item(is_etf=None), self.ASOF, "1535")
        self.assertEqual(result["reason_codes"], ["UNSUPPORTED_TICK_REGIME"])
        missing = base_item()
        del missing["is_etf"]
        result = strategy.generate_bb_atr_advisory(missing, self.ASOF, "1535")
        self.assertEqual(result["reason_codes"], ["UNSUPPORTED_TICK_REGIME"])
        conflict = base_item(stock_code="161510", stock_name="PLUS 고배당주", is_etf=False)
        result = strategy.generate_bb_atr_advisory(conflict, self.ASOF, "1535")
        self.assertEqual(result["reason_codes"], ["UNSUPPORTED_TICK_REGIME"])

    def test_higher_priority_suppression(self):
        cases = [
            ({"data_validity_flag": 0}, None, "DATA_HOLD"),
            ({"trade_mode": "SUSPENDED_HOLD"}, None, "SUSPENDED"),
            ({"hard_stop_active": True}, None, "HARD_STOP"),
            ({"is_stop_breached": True}, None, "STOP_BREACHED"),
            ({}, {"display_allowed": True, "loss_defense_trigger": "ON", "defense_state": "WAIT_REBOUND"}, "LOSS_DEFENSE"),
        ]
        for updates, policy, expected in cases:
            with self.subTest(expected=expected):
                result = strategy.generate_bb_atr_advisory(base_item(**updates), self.ASOF, "1535", policy)
                self.assertEqual(result["overlay_status"], "SUPPRESSED_BY_HIGHER_PRIORITY")
                self.assertTrue(result["overlay_suppressed"])
                self.assertEqual(result["higher_priority_state"], expected)

    def test_schema_and_zero_order_impact(self):
        result = self.generate(final_close=12_000, avg_buy=10_000)
        self.assertTrue(REQUIRED_SCHEMA.issubset(result))
        self.assertEqual(result["overlay_scope"], "ADVISORY_ONLY")
        self.assertEqual(result["actual_order_impact"], 0)

    def test_overlay_does_not_mutate_v4_fields(self):
        item = base_item()
        before = copy.deepcopy(item)
        with patch.object(strategy.Intraday45mAnalyzer, "fetch_canonical_15m_data", return_value=(raw_15m(), "TEST_15M_BAR_START", "NONE")):
            strategy.generate_bb_atr_advisory(item, self.ASOF, "1535")
        for field in ("action_status", "trade_mode", "recommended_order_qty", "confirmed_stop_price", "prev_confirmed_stop", "profit_trail", "effective_exit_line", "auto_order_enabled"):
            self.assertEqual(item[field], before[field])

    def test_legacy_bb_26_1_7_source_is_unchanged(self):
        source = (Path(__file__).parents[1] / "src" / "analysis" / "technical_analysis.py").read_text(encoding="utf-8")
        self.assertIn("rolling(window=26).std()", source)
        self.assertIn("1.7 * std_26", source)

    def test_subject_and_canonical_session_contract(self):
        expected = {
            "1120": "장중 리포트 1 (11:20)",
            "1335": "장중 리포트 2 (13:35)",
            "1535": "장마감 리포트 (15:35)",
        }
        for session, title in expected.items():
            self.assertEqual(main._resolve_dispatch_tag(session), title)
            # skipped


if __name__ == "__main__":
    unittest.main()
