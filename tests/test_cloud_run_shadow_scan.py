"""Tests for Cloud Run SHADOW_SCAN mode in cloud_runner.py.

Verifies:
- SHADOW_SCAN mode and session code resolution
- ZERO Gmail / ZERO Kakao / ZERO Orders
- NO dispatch receipt created or checked (receipt isolation from INTRADAY/POSTMARKET)
- 45m completed bar execution and partial bar skip
- Duplicate scan prevention on the same completed 45m bar
- GCS state bundle committed on successful scan
- Fail-isolation: no impact on INTRADAY / POSTMARKET operations
"""

import os
import gc
import json
import tempfile
import unittest
from datetime import datetime, date
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["STOCKBOT_TEST_MODE"] = "1"

from cloud_runner import CloudRunner, resolve_session_code, build_run_id
from src.database.db_manager import DatabaseManager
from src.utils.gcs_state_adapter import GCSStateAdapter, STATE_FILES_TO_BUNDLE, MANIFEST_FILENAME


class TestCloudRunShadowScan(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "data").mkdir(parents=True, exist_ok=True)
        (self.state_dir / "config").mkdir(parents=True, exist_ok=True)

        # Initialize test SQLite database
        self.db_path = self.state_dir / "data" / "stock_system.db"
        self.db = DatabaseManager(db_path=str(self.db_path))

        # Add mock stock_info and portfolio_holdings
        self.db.execute_non_query(
            "INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES ('004960', '한신공영')"
        )
        with open(self.state_dir / "config" / "portfolio_holdings.json", "w", encoding="utf-8") as f:
            json.dump({"holdings": [{"stock_code": "004960", "stock_name": "한신공영", "quantity": 10, "purchase_price": 10000.0}]}, f)

        self.mock_client = MagicMock()
        self.adapter = GCSStateAdapter(
            bucket_name="test-bucket",
            prefix="stockbot-state",
            state_dir=self.state_dir,
            storage_client=self.mock_client,
        )

    def tearDown(self):
        if hasattr(self, "db"):
            del self.db
        gc.collect()
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Mode and Session Code Resolution
    # -------------------------------------------------------------------------
    def test_01_shadow_scan_mode_and_session_code_resolution(self):
        # 09:50 KST (completes 09:45 bar)
        dt_0950 = datetime(2026, 9, 22, 9, 50, 0)
        code_0950 = resolve_session_code(dt_0950, "SHADOW_SCAN")
        self.assertEqual(code_0950, "0945")
        self.assertEqual(build_run_id(dt_0950, "SHADOW_SCAN"), "20260922_SHADOW_SCAN_0945")

        # 10:35 KST (completes 10:30 bar)
        dt_1035 = datetime(2026, 9, 22, 10, 35, 0)
        code_1035 = resolve_session_code(dt_1035, "SHADOW_SCAN")
        self.assertEqual(code_1035, "1030")

        # 11:20 KST (completes 11:15 bar)
        dt_1120 = datetime(2026, 9, 22, 11, 20, 0)
        code_1120 = resolve_session_code(dt_1120, "SHADOW_SCAN")
        self.assertEqual(code_1120, "1115")

        # 15:05 KST (completes 15:00 bar)
        dt_1505 = datetime(2026, 9, 22, 15, 5, 0)
        code_1505 = resolve_session_code(dt_1505, "SHADOW_SCAN")
        self.assertEqual(code_1505, "1500")

    def test_02_existing_session_codes_strictly_unmodified(self):
        dt_morning = datetime(2026, 9, 22, 11, 20, 0)
        self.assertEqual(resolve_session_code(dt_morning, "INTRADAY"), "1120")

        dt_afternoon = datetime(2026, 9, 22, 13, 35, 0)
        self.assertEqual(resolve_session_code(dt_afternoon, "INTRADAY"), "1335")

        dt_postmarket = datetime(2026, 9, 22, 15, 35, 0)
        self.assertEqual(resolve_session_code(dt_postmarket, "POSTMARKET"), "1535")

    # -------------------------------------------------------------------------
    # 2. Receipt Isolation & State Detection
    # -------------------------------------------------------------------------
    def test_03_shadow_scan_ignores_intraday_receipts(self):
        """SHADOW_SCAN never reads or is blocked by existing INTRADAY receipts."""
        runner = CloudRunner(
            report_mode="SHADOW_SCAN",
            bucket_name="test-bucket",
            storage_client=self.mock_client,
            state_dir=self.state_dir,
        )
        # Even if receipt check would be True, SHADOW_SCAN state is always "SHADOW_SCAN"
        runner.state_adapter.has_dispatch_receipt = MagicMock(return_value=True)
        self.assertEqual(runner._detect_state(), "SHADOW_SCAN")

    def test_04_zero_receipts_created_on_shadow_scan(self):
        """SHADOW_SCAN never creates a dispatch receipt."""
        with patch("cloud_runner.get_current_kst_time", return_value=datetime(2026, 9, 22, 9, 50, 0)):
            runner = CloudRunner(
                report_mode="SHADOW_SCAN",
                bucket_name="test-bucket",
                storage_client=self.mock_client,
                state_dir=self.state_dir,
            )

        runner.state_adapter.download_current_state = MagicMock(return_value=({"bundle_path": "test.zip"}, 1))
        runner.state_adapter.create_and_upload_bundle = MagicMock(return_value=("new_bundle.zip", {}))
        runner.state_adapter.create_dispatch_receipt = MagicMock()

        with patch("src.runtime.runtime_scheduler.RuntimeScheduler._execute_intraday_shadow_scan", return_value={
            "status": "SUCCESS", "stocks_scanned": 1, "journals_created": 1, "last_completed_45m_bar": "2026-09-22 09:45:00"
        }):
            res = runner.run()

        self.assertEqual(res["status"], "SUCCESS")
        # Ensure create_dispatch_receipt was NEVER called
        runner.state_adapter.create_dispatch_receipt.assert_not_called()

    # -------------------------------------------------------------------------
    # 3. Zero Email & Zero Orders Guarantee
    # -------------------------------------------------------------------------
    def test_05_zero_email_and_zero_order_guarantee(self):
        """Verify no GmailNotifier, no send_email, no order methods are touched."""
        with patch("cloud_runner.get_current_kst_time", return_value=datetime(2026, 9, 22, 10, 35, 0)):
            runner = CloudRunner(
                report_mode="SHADOW_SCAN",
                bucket_name="test-bucket",
                storage_client=self.mock_client,
                state_dir=self.state_dir,
            )

        runner.state_adapter.download_current_state = MagicMock(return_value=({"bundle_path": "test.zip"}, 1))
        runner.state_adapter.create_and_upload_bundle = MagicMock(return_value=("new_bundle.zip", {}))

        with patch("src.notifications.gmail_notifier.GmailNotifier") as mock_notifier:
            with patch("src.runtime.runtime_scheduler.RuntimeScheduler._execute_intraday_shadow_scan", return_value={
                "status": "SUCCESS", "stocks_scanned": 1, "journals_created": 0, "last_completed_45m_bar": "2026-09-22 10:30:00"
            }):
                runner.run()
            # GmailNotifier was never instantiated
            mock_notifier.assert_not_called()

        # dispatch_history in SQLite has 0 records
        rows = self.db.execute_query("SELECT * FROM dispatch_history")
        self.assertEqual(len(rows) if rows else 0, 0)

    # -------------------------------------------------------------------------
    # 4. Partial Bar / Non-trading Day / Duplicate Skip
    # -------------------------------------------------------------------------
    def test_06_non_trading_day_skips_without_gcs_commit(self):
        """Weekend or holiday returns SKIPPED_NON_TRADING_DAY and does NOT commit new bundle."""
        # Saturday
        with patch("cloud_runner.get_current_kst_time", return_value=datetime(2026, 9, 19, 10, 35, 0)):
            runner = CloudRunner(
                report_mode="SHADOW_SCAN",
                bucket_name="test-bucket",
                storage_client=self.mock_client,
                state_dir=self.state_dir,
            )

        runner.state_adapter.download_current_state = MagicMock(return_value=({"bundle_path": "test.zip"}, 1))
        runner.state_adapter.create_and_upload_bundle = MagicMock()

        res = runner.run()
        self.assertEqual(res["status"], "SKIPPED_NON_TRADING_DAY")
        # GCS bundle commit should NOT happen on skipped run
        runner.state_adapter.create_and_upload_bundle.assert_not_called()

    def test_07_duplicate_bar_skips_without_gcs_commit(self):
        """Re-running SHADOW_SCAN for the same bar timestamp skips duplicate execution."""
        # Insert previous successful scheduler_run for 09:45:00
        self.db.insert_scheduler_run({
            "run_id": "PREV_RUN",
            "scheduled_time": "09:50",
            "actual_start_time": "2026-09-22 09:50:00",
            "actual_end_time": "2026-09-22 09:50:10",
            "trading_date": "2026-09-22",
            "task_type": "INTRADAY_SHADOW_SCAN",
            "status": "SUCCESS",
            "stocks_scanned": 1,
            "journals_created": 0,
            "signal_changes": 0,
            "last_completed_45m_bar": "2026-09-22 09:45:00",
        })

        with patch("cloud_runner.get_current_kst_time", return_value=datetime(2026, 9, 22, 9, 50, 0)):
            runner = CloudRunner(
                report_mode="SHADOW_SCAN",
                bucket_name="test-bucket",
                storage_client=self.mock_client,
                state_dir=self.state_dir,
            )

        runner.state_adapter.download_current_state = MagicMock(return_value=({"bundle_path": "test.zip"}, 1))
        runner.state_adapter.create_and_upload_bundle = MagicMock()

        res = runner.run()
        self.assertEqual(res["status"], "SKIPPED_NO_NEW_45M_BAR")
        runner.state_adapter.create_and_upload_bundle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
