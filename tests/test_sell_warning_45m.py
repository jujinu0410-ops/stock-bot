"""Contrast and preservation tests for the completed-45m sell-warning sidecar."""

import copy
import gc
import os
import tempfile
import unittest
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

os.environ["STOCKBOT_TEST_MODE"] = "1"

from src.analysis.add_advisory_engine import AddAdvisory45mEngine
from src.analysis.sell_warning_45m_engine import SellWarning45mEngine
from src.database.db_manager import DatabaseManager


def add_metrics(**overrides):
    values = {
        "vwap9": 110.0,
        "vwap26": 100.0,
        "obv": 120.0,
        "obv9": 100.0,
        "obv_gap": 20.0,
        "obv_gap_delta": 3.0,
        "obv_gap_state": "EXPANDING",
        "chaikin_value": 20.0,
        "chaikin_delta": 2.0,
        "chaikin_state": "CHAIKIN_RISING",
    }
    values.update(overrides)
    return values


def trend_metrics(**overrides):
    values = {
        "close_45m": 110.0,
        "cloud_bottom_45m": 100.0,
        "adx_14_45m": 18.0,
        "plus_di_45m": 24.0,
        "minus_di_45m": 16.0,
    }
    values.update(overrides)
    return values


def completed_market_15m(days=4, partial_extreme=False):
    """Start-labelled KRX rows plus a deliberately incomplete post-cutoff bar."""
    indexes = []
    for day in pd.bdate_range("2026-09-14", periods=days):
        indexes.extend(pd.date_range(day + pd.Timedelta(hours=9), periods=24, freq="15min"))
    index = pd.DatetimeIndex(indexes)
    base = np.arange(len(index), dtype=float)
    close = 10_000.0 + base * 5.0
    frame = pd.DataFrame({
        "Open": close - 2.0,
        "High": close + 8.0,
        "Low": close - 8.0,
        "Close": close,
        "Volume": 1_000.0 + base * 3.0,
    }, index=index)

    last_day = pd.bdate_range("2026-09-14", periods=days)[-1]
    partial_index = pd.date_range(last_day + pd.Timedelta(hours=11, minutes=15), periods=2, freq="15min")
    partial_close = np.array([50_000.0, 1_000.0]) if partial_extreme else np.array([10_500.0, 10_505.0])
    partial = pd.DataFrame({
        "Open": partial_close,
        "High": partial_close + 10.0,
        "Low": partial_close - 10.0,
        "Close": partial_close,
        "Volume": [9_000_000.0, 9_000_000.0],
    }, index=partial_index)
    # Replace the regular rows at/after 11:15 so the index remains unique.
    return pd.concat([frame.loc[frame.index < partial_index[0]], partial]).sort_index()


def legacy_add_15m():
    index = pd.date_range("2026-08-27 09:00", periods=90, freq="15min")
    base = np.arange(90, dtype=float)
    close = 10_000.0 + base * 40.0 + 15.0
    return pd.DataFrame({
        "Open": close,
        "High": close + 5.0,
        "Low": close - 25.0,
        "Close": close,
        "Volume": 1_000.0 + base * 20.0,
    }, index=index)


class TestSellWarning45m(unittest.TestCase):
    def setUp(self):
        self.engine = SellWarning45mEngine(intraday_analyzer=MagicMock())

    def evaluate(self, add_values, trend_values):
        return self.engine.evaluate_canonical_axes(
            "004960", "2026-09-17", "2026-09-17 11:15:00",
            add_values, trend_values, completed_bar_count=27,
        )

    def test_01_hanshin_construction_shape_is_sell_warning(self):
        result = self.evaluate(
            add_metrics(
                vwap9=90.0, vwap26=100.0,
                obv=70.0, obv9=100.0, obv_gap=-30.0,
                obv_gap_delta=-8.0, obv_gap_state="CONTRACTING",
                chaikin_value=-50.0, chaikin_delta=-10.0,
                chaikin_state="CHAIKIN_FALLING",
            ),
            trend_metrics(
                close_45m=80.0, cloud_bottom_45m=95.0,
                adx_14_45m=31.0, plus_di_45m=10.0, minus_di_45m=32.0,
            ),
        )
        self.assertEqual(result["sell_warning_state"], "SELL_WARNING")
        self.assertEqual(result["bearish_axes_count"], 4)

    def test_02_hd_hyundai_electric_shape_is_caution(self):
        result = self.evaluate(
            add_metrics(
                vwap9=95.0, vwap26=100.0,
                obv=85.0, obv9=100.0, obv_gap=-15.0,
                obv_gap_delta=-2.0, obv_gap_state="CONTRACTING",
                chaikin_value=-20.0, chaikin_delta=7.0,
                chaikin_state="CHAIKIN_RISING",
            ),
            trend_metrics(adx_14_45m=20.0, plus_di_45m=21.0, minus_di_45m=20.0),
        )
        self.assertEqual(result["sell_warning_state"], "CAUTION")
        self.assertEqual(result["chaikin_recovery_conflict"], 1)

    def test_03_dongkook_pharma_shape_is_normal(self):
        result = self.evaluate(
            add_metrics(),
            trend_metrics(adx_14_45m=35.0, plus_di_45m=34.0, minus_di_45m=12.0),
        )
        self.assertEqual(result["sell_warning_state"], "NORMAL")
        self.assertEqual(result["bear_trend"], 0)
        self.assertEqual(result["bull_trend_conflict"], 1)

    def test_04_missing_canonical_value_is_unknown(self):
        values = add_metrics(chaikin_delta=None)
        result = self.evaluate(values, trend_metrics())
        self.assertEqual(result["sell_warning_state"], "UNKNOWN")
        self.assertIn("chaikin_delta", result["reason_codes"])

    def test_05_high_adx_plus_di_dominance_never_warns(self):
        result = self.evaluate(
            add_metrics(vwap9=90.0, vwap26=100.0),
            trend_metrics(adx_14_45m=42.0, plus_di_45m=38.0, minus_di_45m=11.0),
        )
        self.assertNotEqual(result["sell_warning_state"], "SELL_WARNING")
        self.assertFalse(result["bear_trend"])

    def test_06_chaikin_recovery_suppresses_sell_warning(self):
        result = self.evaluate(
            add_metrics(
                vwap9=90.0, vwap26=100.0,
                obv=70.0, obv9=100.0, obv_gap=-30.0,
                obv_gap_delta=-5.0, obv_gap_state="CONTRACTING",
                chaikin_value=-50.0, chaikin_delta=12.0,
                chaikin_state="CHAIKIN_RISING",
            ),
            trend_metrics(adx_14_45m=30.0, plus_di_45m=10.0, minus_di_45m=30.0),
        )
        self.assertEqual(result["bearish_axes_count"], 3)
        self.assertEqual(result["sell_warning_state"], "CAUTION")

    def test_07_obv_only_does_not_overwarn(self):
        result = self.evaluate(
            add_metrics(
                obv=70.0, obv9=100.0, obv_gap=-30.0,
                obv_gap_delta=-5.0, obv_gap_state="CONTRACTING",
            ),
            trend_metrics(),
        )
        self.assertEqual(result["bearish_axes_count"], 1)
        self.assertEqual(result["sell_warning_state"], "NORMAL")

    def test_08_partial_bar_and_future_rows_are_excluded(self):
        analyzer = MagicMock()
        engine = SellWarning45mEngine(intraday_analyzer=analyzer)
        cutoff = "2026-09-17 11:15:00"
        analyzer.fetch_canonical_15m_data.return_value = (
            completed_market_15m(partial_extreme=False), "TEST_15M_BAR_START", "NONE"
        )
        baseline = engine.evaluate_stock_warning("004960", "2026-09-17", cutoff)
        analyzer.fetch_canonical_15m_data.return_value = (
            completed_market_15m(partial_extreme=True), "TEST_15M_BAR_START", "NONE"
        )
        extreme = engine.evaluate_stock_warning("004960", "2026-09-17", cutoff)

        self.assertTrue(baseline["data_quality"].startswith("VALID"))
        self.assertEqual(baseline["completed_45m_timestamp"], cutoff)
        for field in ("vwap9", "vwap26", "obv", "chaikin_value", "adx_14_45m"):
            self.assertAlmostEqual(baseline[field], extreme[field], places=8)

    def test_09_insufficient_completed_bars_is_unknown(self):
        analyzer = MagicMock()
        analyzer.fetch_canonical_15m_data.return_value = (
            completed_market_15m(days=1), "TEST_15M_BAR_START", "NONE"
        )
        result = SellWarning45mEngine(analyzer).evaluate_stock_warning(
            "004960", "2026-09-14", "2026-09-14 11:15:00"
        )
        self.assertEqual(result["sell_warning_state"], "UNKNOWN")
        self.assertIn("INSUFFICIENT_COMPLETED_45M_BARS", result["reason_codes"])

    def test_10_duplicate_prevention_and_scan_journal_isolation(self):
        result = self.evaluate(add_metrics(), trend_metrics())
        temp_dir = tempfile.TemporaryDirectory()
        try:
            db = DatabaseManager(db_path=os.path.join(temp_dir.name, "sidecar.db"))
            self.assertTrue(db.insert_sell_warning_45m(result))
            self.assertFalse(db.insert_sell_warning_45m(result))
            self.assertEqual(len(db.get_sell_warnings_by_date("2026-09-17")), 1)
            self.assertIsNone(db.get_latest_scan_journal_for_stock("004960"))
        finally:
            if "db" in locals():
                del db
            gc.collect()
            temp_dir.cleanup()

    def test_11_existing_add_advisory_state_is_unchanged(self):
        analyzer = MagicMock()
        analyzer.fetch_canonical_15m_data.return_value = (legacy_add_15m(), "MOCK_SOURCE", "NONE")
        result = AddAdvisory45mEngine(analyzer).evaluate_stock_advisory(
            "005930", "2026-08-27", "2026-08-27 15:00:00", technical_state_reference="STRONG"
        )
        self.assertEqual(result["add_advisory_state"], "ADD_STRONG")
        self.assertEqual(result["technical_state_reference"], "STRONG")

    def test_12_technical_state_input_is_immutable(self):
        holding = {"stock_code": "004960", "technical_state": "DAMAGED"}
        before = copy.deepcopy(holding)
        self.evaluate(add_metrics(), trend_metrics())
        self.assertEqual(holding, before)

    def test_13_engine_has_no_order_api(self):
        for name in ("send_order", "buy_order", "sell_order", "place_order"):
            self.assertFalse(hasattr(self.engine, name))


if __name__ == "__main__":
    unittest.main()
