"""Operational shadow tests for SellWarning45mEngine integrated into RuntimeScheduler.

Verifies:
- 1st SELL_WARNING: sell_confirmed == 0
- 2nd consecutive SELL (same day or next trading day overnight): sell_confirmed == 1
- Interrupted streak (CAUTION/NORMAL/UNKNOWN or missed bar): sell_confirmed == 0
- Duplicate prevention: (stock_code, bar_timestamp) UNIQUE constraint
- Partial / non-completed bar rejection
- Fail-isolation: engine/DB errors do not disrupt main intraday scan
- Zero order API calls & zero notifications
"""

import os
import gc
import tempfile
import unittest
from datetime import datetime, time, date
from unittest.mock import MagicMock, patch

os.environ["STOCKBOT_TEST_MODE"] = "1"

from src.database.db_manager import DatabaseManager
from src.runtime.runtime_scheduler import RuntimeScheduler
from src.runtime.krx_calendar import KRXCalendar


class TestSellWarningOperationalShadow(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "shadow_test.db")
        self.db = DatabaseManager(db_path=self.db_path)
        self.scheduler = RuntimeScheduler(db_manager=self.db)

    def tearDown(self):
        if hasattr(self, "db"):
            del self.db
        if hasattr(self, "scheduler"):
            del self.scheduler
        gc.collect()
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Immediately preceding 45m bar detection helper tests
    # -------------------------------------------------------------------------
    def test_01_is_immediately_preceding_45m_bar_same_day(self):
        # Canonical consecutive pairs (45m apart)
        self.assertTrue(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-22 09:45:00", "2026-09-22 10:30:00"
        ))
        self.assertTrue(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-22 10:30:00", "2026-09-22 11:15:00"
        ))
        self.assertTrue(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-22 11:15:00", "2026-09-22 12:00:00"
        ))
        self.assertTrue(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-22 12:00:00", "2026-09-22 12:45:00"
        ))
        self.assertTrue(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-22 12:45:00", "2026-09-22 13:30:00"
        ))
        self.assertTrue(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-22 13:30:00", "2026-09-22 14:15:00"
        ))
        self.assertTrue(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-22 14:15:00", "2026-09-22 15:00:00"
        ))

        # Missed slot (90m gap)
        self.assertFalse(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-22 09:45:00", "2026-09-22 11:15:00"
        ))
        # Same timestamp
        self.assertFalse(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-22 10:30:00", "2026-09-22 10:30:00"
        ))
        # Backward timestamp
        self.assertFalse(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-22 11:15:00", "2026-09-22 10:30:00"
        ))

    def test_02_is_immediately_preceding_45m_bar_overnight(self):
        # 2026-09-21 is Monday, 2026-09-22 is Tuesday
        self.assertTrue(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-21 15:00:00", "2026-09-22 09:45:00"
        ))

        # Friday to Monday weekend gap (2026-09-18 Fri to 2026-09-21 Mon)
        self.assertTrue(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-18 15:00:00", "2026-09-21 09:45:00"
        ))

        # Skipped trading day (2026-09-21 Mon to 2026-09-23 Wed; Tuesday was skipped)
        self.assertFalse(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-21 15:00:00", "2026-09-23 09:45:00"
        ))

        # Prev bar was not 15:00
        self.assertFalse(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-21 14:15:00", "2026-09-22 09:45:00"
        ))

        # Curr bar is not 09:45
        self.assertFalse(RuntimeScheduler._is_immediately_preceding_45m_bar(
            "2026-09-21 15:00:00", "2026-09-22 10:30:00"
        ))

    # -------------------------------------------------------------------------
    # 2. Operational Sidecar Evaluation Tests (1st vs 2nd Consecutive SELL)
    # -------------------------------------------------------------------------
    def test_03_first_sell_sets_sell_confirmed_zero(self):
        mock_engine = MagicMock()
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-22",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-22 09:45:00",
            "evaluated_at": "2026-09-22 09:50:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID (27 completed bars)",
            "reason_codes": "PRICE_WEAKNESS,OBV_WEAKNESS,CHAIKIN_WEAKNESS,BEAR_TREND",
            "bearish_axes_count": 4,
            "price_weakness": 1,
            "obv_weakness": 1,
            "chaikin_weakness": 1,
            "bear_trend": 1,
            "chaikin_recovery_conflict": 0,
            "bull_trend_conflict": 0,
            "vwap_dead": 1,
            "is_price_below_cloud_45m": 1,
            "completed_45m_timestamp": "2026-09-22 09:45:00",
            "completed_45m_bar_count": 27,
            "vwap9": 90.0,
            "vwap26": 100.0,
            "close_45m": 80.0,
            "cloud_bottom_45m": 95.0,
            "obv": 70.0,
            "obv_wma9": 100.0,
            "obv_gap": -30.0,
            "obv_gap_delta": -8.0,
            "obv_gap_state": "CONTRACTING",
            "chaikin_value": -50.0,
            "chaikin_delta": -10.0,
            "chaikin_state": "CHAIKIN_FALLING",
            "adx_14_45m": 31.0,
            "plus_di_45m": 10.0,
            "minus_di_45m": 32.0,
        }
        self.scheduler._sell_warning_engine = mock_engine

        # Execute sidecar evaluation
        self.scheduler._evaluate_sell_warning_sidecar(
            "004960", {}, "2026-09-22", "2026-09-22 09:45:00"
        )

        record = self.db.get_latest_sell_warning_for_stock("004960")
        self.assertIsNotNone(record)
        self.assertEqual(record["sell_warning_state"], "SELL_WARNING")
        self.assertEqual(record["sell_confirmed"], 0)

    def test_04_second_consecutive_sell_sets_sell_confirmed_one(self):
        mock_engine = MagicMock()
        self.scheduler._sell_warning_engine = mock_engine

        # Bar 1: 09:45 SELL_WARNING
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-22",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-22 09:45:00",
            "evaluated_at": "2026-09-22 09:50:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID",
        }
        self.scheduler._evaluate_sell_warning_sidecar(
            "004960", {}, "2026-09-22", "2026-09-22 09:45:00"
        )

        r1 = self.db.get_latest_sell_warning_for_stock("004960")
        self.assertEqual(r1["sell_confirmed"], 0)

        # Bar 2: 10:30 SELL_WARNING (immediately consecutive)
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-22",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-22 10:30:00",
            "evaluated_at": "2026-09-22 10:35:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID",
        }
        self.scheduler._evaluate_sell_warning_sidecar(
            "004960", {}, "2026-09-22", "2026-09-22 10:30:00"
        )

        r2 = self.db.get_latest_sell_warning_for_stock("004960")
        self.assertEqual(r2["bar_timestamp"], "2026-09-22 10:30:00")
        self.assertEqual(r2["sell_warning_state"], "SELL_WARNING")
        self.assertEqual(r2["sell_confirmed"], 1)

    def test_05_interrupted_streak_resets_sell_confirmed(self):
        mock_engine = MagicMock()
        self.scheduler._sell_warning_engine = mock_engine

        # Bar 1: 09:45 SELL_WARNING
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-22",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-22 09:45:00",
            "evaluated_at": "2026-09-22 09:50:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID",
        }
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-22", "2026-09-22 09:45:00")

        # Bar 2: 10:30 CAUTION (breaks SELL streak)
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-22",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-22 10:30:00",
            "evaluated_at": "2026-09-22 10:35:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "CAUTION",
            "data_quality": "VALID",
        }
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-22", "2026-09-22 10:30:00")

        # Bar 3: 11:15 SELL_WARNING (not preceded by SELL, preceded by CAUTION)
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-22",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-22 11:15:00",
            "evaluated_at": "2026-09-22 11:20:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID",
        }
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-22", "2026-09-22 11:15:00")

        r3 = self.db.get_latest_sell_warning_for_stock("004960")
        self.assertEqual(r3["bar_timestamp"], "2026-09-22 11:15:00")
        self.assertEqual(r3["sell_warning_state"], "SELL_WARNING")
        self.assertEqual(r3["sell_confirmed"], 0)

    def test_06_non_consecutive_gap_resets_sell_confirmed(self):
        mock_engine = MagicMock()
        self.scheduler._sell_warning_engine = mock_engine

        # Bar 1: 09:45 SELL_WARNING
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-22",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-22 09:45:00",
            "evaluated_at": "2026-09-22 09:50:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID",
        }
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-22", "2026-09-22 09:45:00")

        # 10:30 missed/skipped!
        # Bar 3: 11:15 SELL_WARNING (gap of 90m)
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-22",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-22 11:15:00",
            "evaluated_at": "2026-09-22 11:20:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID",
        }
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-22", "2026-09-22 11:15:00")

        r3 = self.db.get_latest_sell_warning_for_stock("004960")
        self.assertEqual(r3["sell_confirmed"], 0)

    def test_07_overnight_consecutive_sell_confirmed(self):
        mock_engine = MagicMock()
        self.scheduler._sell_warning_engine = mock_engine

        # Friday 15:00 SELL_WARNING
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-18",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-18 15:00:00",
            "evaluated_at": "2026-09-18 15:05:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID",
        }
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-18", "2026-09-18 15:00:00")

        # Monday 09:45 SELL_WARNING
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-21",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-21 09:45:00",
            "evaluated_at": "2026-09-21 09:50:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID",
        }
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-21", "2026-09-21 09:45:00")

        mon_record = self.db.get_latest_sell_warning_for_stock("004960")
        self.assertEqual(mon_record["bar_timestamp"], "2026-09-21 09:45:00")
        self.assertEqual(mon_record["sell_confirmed"], 1)

    def test_08_duplicate_record_ignored(self):
        mock_engine = MagicMock()
        self.scheduler._sell_warning_engine = mock_engine

        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-22",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-22 09:45:00",
            "evaluated_at": "2026-09-22 09:50:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID",
        }
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-22", "2026-09-22 09:45:00")
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-22", "2026-09-22 09:45:00")

        all_warnings = self.db.get_sell_warnings_by_date("2026-09-22")
        self.assertEqual(len(all_warnings), 1)

    def test_09_non_completed_bar_skipped(self):
        mock_engine = MagicMock()
        self.scheduler._sell_warning_engine = mock_engine

        # Non-completed bar timestamp (e.g. manual scan at 09:15)
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-22", "2026-09-22 09:15:00")
        mock_engine.evaluate_stock_warning.assert_not_called()

        all_warnings = self.db.get_sell_warnings_by_date("2026-09-22")
        self.assertEqual(len(all_warnings), 0)

    def test_10_fail_isolation_does_not_break_intraday_scan(self):
        mock_engine = MagicMock()
        mock_engine.evaluate_stock_warning.side_effect = RuntimeError("Simulated sidecar engine failure")
        self.scheduler._sell_warning_engine = mock_engine

        # Should log error and return without raising exception
        try:
            self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-22", "2026-09-22 09:45:00")
        except Exception as e:
            self.fail(f"_evaluate_sell_warning_sidecar raised an exception: {e}")

    def test_11_zero_order_zero_notification_guarantee(self):
        # Verify scheduler and engine have no order methods
        for attr in ("place_order", "send_order", "buy_order", "sell_order"):
            self.assertFalse(hasattr(self.scheduler, attr))

        mock_engine = MagicMock()
        mock_engine.evaluate_stock_warning.return_value = {
            "trading_date": "2026-09-22",
            "stock_code": "004960",
            "bar_timestamp": "2026-09-22 09:45:00",
            "evaluated_at": "2026-09-22 09:50:00 KST",
            "engine_version": "SELL_WARNING_45M_V1",
            "sell_warning_state": "SELL_WARNING",
            "data_quality": "VALID",
        }
        self.scheduler._sell_warning_engine = mock_engine

        # Execute sidecar
        self.scheduler._evaluate_sell_warning_sidecar("004960", {}, "2026-09-22", "2026-09-22 09:45:00")

        # Confirm dispatch_history remains empty (0 emails, 0 kakao messages)
        rows = self.db.execute_query("SELECT * FROM dispatch_history")
        self.assertEqual(len(rows) if rows else 0, 0)


if __name__ == "__main__":
    unittest.main()
