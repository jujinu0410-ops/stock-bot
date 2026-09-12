"""Cloud report state-path and completed-45m regression coverage."""

import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from main import _ensure_report_add_advisories
from src.database.db_manager import DatabaseManager
from src.notifications.mobile_renderer_v2 import get_latest_add_advisory_entry
from src.policy.policy_shadow_observer import observe_report_policy_shadow
from src.policy.policy_shadow_store import (
    PolicyShadowReader,
    PolicyShadowStore,
    resolve_policy_shadow_db_path,
)


class TestCloudReportStatePaths(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="stockbot_cloud_report_"))
        self.state_dir = self.temp_dir / "stockbot"
        self.state_dir.joinpath("data").mkdir(parents=True)
        self.stock_db_path = self.state_dir / "data" / "stock_system.db"
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _state_env(self):
        return patch.dict(
            os.environ,
            {"STOCKBOT_TEST_MODE": "0", "STOCKBOT_STATE_DIR": str(self.state_dir)},
            clear=False,
        )

    @staticmethod
    def _entry(bar_timestamp, state):
        return {
            "trading_date": "2026-09-02",
            "stock_code": "005930",
            "bar_timestamp": bar_timestamp,
            "evaluated_at": "2026-09-02 11:20:00 KST",
            "add_advisory_state": state,
            "data_quality": "VALID (36 bars)",
        }

    def test_01_renderer_reads_cloud_state_db_and_applies_report_cutoff(self):
        """No /app/data dependency: 11:20 may read 11:15, never future 12:00."""
        db = DatabaseManager(db_path=str(self.stock_db_path))
        db.insert_add_advisory_45m(self._entry("2026-09-02 11:15:00", "ADD_STRONG"))
        db.insert_add_advisory_45m(self._entry("2026-09-02 12:00:00", "ADD_BLOCKED"))

        with self._state_env():
            entry = get_latest_add_advisory_entry(
                "005930",
                target_date="2026-09-02",
                max_bar_timestamp="2026-09-02 11:20:00",
            )

        self.assertEqual(entry["add_advisory_state"], "ADD_STRONG")
        self.assertEqual(entry["bar_timestamp"], "2026-09-02 11:15:00")

    def test_02_report_sidecar_uses_1120_kst_completed_bar(self):
        db = MagicMock()
        db.get_latest_add_advisory_for_stock.return_value = None
        db.insert_add_advisory_45m.return_value = True
        engine = MagicMock()
        engine.evaluate_stock_advisory.return_value = self._entry("2026-09-02 11:15:00", "ADD_STRONG")
        asof = datetime(2026, 9, 2, 11, 20, tzinfo=ZoneInfo("Asia/Seoul"))

        inserted = _ensure_report_add_advisories(
            db, [{"stock_code": "005930", "technical_state": "HEALTHY"}], asof, engine
        )

        self.assertEqual(inserted, 1)
        self.assertEqual(engine.evaluate_stock_advisory.call_args.kwargs["bar_timestamp"], "2026-09-02 11:15:00")

    def test_03_report_sidecar_uses_1335_kst_completed_bar(self):
        db = MagicMock()
        db.get_latest_add_advisory_for_stock.return_value = None
        db.insert_add_advisory_45m.return_value = True
        engine = MagicMock()
        engine.evaluate_stock_advisory.return_value = self._entry("2026-09-02 13:30:00", "ADD_BLOCKED")
        asof = datetime(2026, 9, 2, 13, 35, tzinfo=ZoneInfo("Asia/Seoul"))

        _ensure_report_add_advisories(db, [{"stock_code": "005930"}], asof, engine)

        self.assertEqual(engine.evaluate_stock_advisory.call_args.kwargs["bar_timestamp"], "2026-09-02 13:30:00")

    def test_04_report_sidecar_does_not_repeat_a_valid_completed_bar(self):
        db = MagicMock()
        db.get_latest_add_advisory_for_stock.return_value = self._entry(
            "2026-09-02 11:15:00", "ADD_STRONG"
        )
        engine = MagicMock()
        asof = datetime(2026, 9, 2, 11, 20, tzinfo=ZoneInfo("Asia/Seoul"))

        inserted = _ensure_report_add_advisories(db, [{"stock_code": "005930"}], asof, engine)

        self.assertEqual(inserted, 0)
        engine.evaluate_stock_advisory.assert_not_called()
        db.insert_add_advisory_45m.assert_not_called()

    def test_05_policy_writer_and_reader_share_cloud_state_db(self):
        held = [{
            "stock_code": "005930", "stock_name": "삼성전자", "quantity": 10,
            "avg_buy_price": 70000.0, "current_price": 71000.0,
            "f_score": 80.0, "t_score": 75.0, "atr_14": 1000.0,
            "pnl_pct": 1.4, "technical_state": "HEALTHY",
            "data_validity_flag": 1, "trade_mode": "NORMAL",
        }]
        asof = datetime(2026, 9, 2, 11, 20, tzinfo=ZoneInfo("Asia/Seoul"))

        with self._state_env():
            store = PolicyShadowStore()
            snapshots = observe_report_policy_shadow(
                DatabaseManager(db_path=str(self.stock_db_path)), held, "20260902_1120", asof_dt=asof
            )
            reader = PolicyShadowReader()
            payload = reader.build_report_payload(asof.replace(tzinfo=None), held)

        self.assertEqual(store.db_path, reader.db_path)
        self.assertEqual(store.db_path, self.state_dir / "data" / "policy_shadow.db")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(payload["status"], "OK")
        self.assertEqual(payload["rows"][0]["actual_order_impact"], 0)

    def test_06_missing_policy_db_keeps_data_unavailable_fail_safe(self):
        with self._state_env():
            payload = PolicyShadowReader().build_report_payload(
                datetime(2026, 9, 2, 11, 20), [{"stock_code": "005930", "quantity": 10}]
            )
        self.assertEqual(payload["status"], "UNAVAILABLE")

    def test_07_test_mode_resolver_stays_temp_isolated(self):
        with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "1", "STOCKBOT_STATE_DIR": str(self.state_dir)}, clear=False):
            self.assertEqual(PolicyShadowStore().db_path, PolicyShadowReader().db_path)
            self.assertEqual(resolve_policy_shadow_db_path().parent, Path(tempfile.gettempdir()))


if __name__ == "__main__":
    unittest.main()
