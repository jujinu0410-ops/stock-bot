"""
tests/test_cloud_run_phase1.py

Comprehensive Phase 1 Cloud Run Job & GCS State Bundle Test Suite
Updated to cover:
1. GCS Immutable Dispatch Receipt Idempotency (Order: Commit -> Gmail -> Receipt, generation_match=0)
2. REPORT_MODE Fail-Closed (Strict validation, no auto-detection, delayed execution preservation)
3. 45m Bar Authority (No hardcoded 11:15/15:30 timestamps, reuses analyzer raw fields)
4. Policy DATA_UNAVAILABLE Graceful Handling (V4 mail proceeds, Policy failure visible, no action numbers)
5. 3-State Machine & Prepared Dispatch (ALREADY_COMPLETED / RESUME_PENDING_EMAIL / FRESH)
6. FIX-1 (Resume without V4/Policy/state mutation) & FIX-2 (report_payload Fail-Closed)
"""

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.utils.gcs_state_adapter import (
    GCSStateAdapter,
    StateAdapterError,
    StateConflictError,
    CorruptStateError,
    StateIntegrityError,
    sha256_file,
    verify_sqlite_integrity,
    MANIFEST_FILENAME,
    CURRENT_POINTER_FILENAME,
    STATE_FILES_TO_BUNDLE,
    PREPARED_DISPATCH_JSON,
    PREPARED_DISPATCH_XLSX,
    PREPARED_DISPATCH_DIR,
)
from src.database.db_manager import DatabaseManager
from src.policy.policy_shadow_store import PolicyShadowStore, PolicyShadowService, PolicyShadowReader
from src.policy.policy_shadow_observer import observe_report_policy_shadow
from src.notifications.mobile_renderer_v2 import (
    generate_mobile_html_report_v2,
    build_today_action_summary_html,
    build_all_holdings_strategy_table_html,
)
from cloud_runner import (
    CloudRunner,
    build_run_id,
    get_current_kst_time,
    _validate_report_payload,
    _SM_ALREADY_COMPLETED,
    _SM_RESUME,
    _SM_FRESH,
)


class MockBlob:
    def __init__(self, name: str, data: bytes = b"", generation: int = 1, exists: bool = True):
        self.name = name
        self._data = data
        self.generation = generation
        self._exists = exists
        self.content_type = "application/octet-stream"

    def exists(self) -> bool:
        return self._exists

    def reload(self):
        pass

    def download_as_bytes(self) -> bytes:
        if not self._exists:
            raise RuntimeError(f"Blob {self.name} does not exist")
        return self._data

    def download_to_filename(self, filename: str):
        with open(filename, "wb") as f:
            f.write(self._data)

    def upload_from_filename(self, filename: str):
        with open(filename, "rb") as f:
            self._data = f.read()
        self._exists = True
        self.generation += 1

    def upload_from_string(self, data: bytes, content_type: str = "text/plain", if_generation_match: Optional[int] = None):
        if if_generation_match == 0:
            if self._exists:
                raise StateConflictError(
                    f"Precondition failed: expected generation 0 (non-existent) but blob {self.name} already exists with gen {self.generation}"
                )
        elif if_generation_match is not None and if_generation_match != self.generation:
            raise StateConflictError(
                f"Precondition failed: expected generation {if_generation_match} but current is {self.generation}"
            )
        self._data = data
        self.content_type = content_type
        self._exists = True
        self.generation += 1


class MockBucket:
    def __init__(self, name: str):
        self.name = name
        self.blobs: Dict[str, MockBlob] = {}

    def blob(self, name: str) -> MockBlob:
        if name not in self.blobs:
            self.blobs[name] = MockBlob(name, exists=False, generation=0)
        return self.blobs[name]


class MockStorageClient:
    def __init__(self):
        self.buckets: Dict[str, MockBucket] = {}

    def bucket(self, name: str) -> MockBucket:
        if name not in self.buckets:
            self.buckets[name] = MockBucket(name)
        return self.buckets[name]


class TestCloudRunPhase1(unittest.TestCase):
    """Phase 1 Cloud Run & GCS State Bundle Comprehensive Test Suite"""

    def setUp(self):
        self._orig_env = os.environ.copy()
        self.test_dir = Path(tempfile.mkdtemp(prefix="stockbot_cloud_test_"))
        self.state_dir = self.test_dir / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "data").mkdir(parents=True, exist_ok=True)
        (self.state_dir / "config").mkdir(parents=True, exist_ok=True)
        (self.state_dir / "logs").mkdir(parents=True, exist_ok=True)

        # Setup sample valid DBs
        self.db_path = self.state_dir / "data" / "stock_system.db"
        self.db = DatabaseManager(db_path=str(self.db_path))
        self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES ('005930', '삼성전자')")

        self.policy_db_path = self.state_dir / "data" / "policy_shadow.db"
        self.policy_store = PolicyShadowStore(db_path=self.policy_db_path)

        self.holdings_path = self.state_dir / "config" / "portfolio_holdings.json"
        with open(self.holdings_path, "w", encoding="utf-8") as f:
            json.dump([{"stock_code": "005930", "stock_name": "삼성전자", "quantity": 10}], f)

        self.mock_client = MockStorageClient()
        self.adapter = GCSStateAdapter(
            bucket_name="test-bucket",
            prefix="stockbot-state",
            state_dir=self.state_dir,
            storage_client=self.mock_client,
        )

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)
        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass

    def _sample_report_payload(self):
        mock_notifier = MagicMock()
        mock_notifier.recipient_email = "recipient@example.com"
        mock_notifier.send_email.return_value = True
        return {
            "excel_path": self.state_dir / "data" / "dummy.xlsx",
            "csv_held_path": self.state_dir / "data" / "dummy_held.csv",
            "csv_summary_path": self.state_dir / "data" / "dummy_sum.csv",
            "html_report": "<html><body>data-render-version=\"V2\" V4-PILOT-C 주요 대응 지침 data-held-stock-codes='[\"005930\"]'</body></html>",
            "subject": "[1차 장중 리포트(11:20)] V4-PILOT-C",
            "dispatch_id": "20260901_1120",
            "dispatch_tag": "1차 장중 리포트(11:20)",
            "notifier": mock_notifier,
            "attachments": [],
            "render_version": "V2",
            "policy_display": {"status": "OK", "rows": []},
            "fingerprint_id": "FP_test1234",
        }

    # =========================================================================
    # Group 1: GCS State Bundle Lifecycle & Integrity (Tests 1-8)
    # =========================================================================

    def test_01_bundle_upload_and_download(self):
        """1. State bundle creation, upload, and successful restoration."""
        bundle_path, manifest = self.adapter.create_and_upload_bundle(run_id="RUN_001")
        self.assertIn("stockbot-state/bundles/", bundle_path)
        self.assertEqual(manifest["run_id"], "RUN_001")

        # Download in new clean state directory
        new_state_dir = self.test_dir / "restored_state"
        new_adapter = GCSStateAdapter(
            bucket_name="test-bucket",
            prefix="stockbot-state",
            state_dir=new_state_dir,
            storage_client=self.mock_client,
        )
        restored_manifest, gen = new_adapter.download_current_state()
        self.assertEqual(restored_manifest["run_id"], "RUN_001")
        self.assertTrue((new_state_dir / "data" / "stock_system.db").exists())
        self.assertTrue((new_state_dir / "data" / "policy_shadow.db").exists())
        self.assertTrue((new_state_dir / "config" / "portfolio_holdings.json").exists())

    def test_02_manifest_hash_validation(self):
        """2. Manifest checksum validation matches extracted files."""
        _, manifest = self.adapter.create_and_upload_bundle(run_id="RUN_002")
        for rel_path, expected_hash in manifest["files"].items():
            actual_hash = sha256_file(self.state_dir / rel_path)
            self.assertEqual(expected_hash, actual_hash, f"Hash mismatch on {rel_path}")

    def test_03_corrupt_bundle_fail(self):
        """3. Corrupt bundle zip raises CorruptStateError."""
        self.adapter.create_and_upload_bundle(run_id="RUN_003")

        bucket = self.mock_client.bucket("test-bucket")
        ptr_blob = bucket.blob("stockbot-state/current.json")
        ptr_data = json.loads(ptr_blob.download_as_bytes().decode("utf-8"))
        bundle_blob = bucket.blob(ptr_data["bundle_path"])
        bundle_blob._data = b"NOT_A_VALID_ZIP_ARCHIVE"

        new_state = self.test_dir / "corrupt_state"
        corrupt_adapter = GCSStateAdapter("test-bucket", "stockbot-state", new_state, self.mock_client)
        with self.assertRaises(CorruptStateError):
            corrupt_adapter.download_current_state()

    def test_04_sqlite_integrity_fail(self):
        """4. Corrupted SQLite file fails PRAGMA integrity_check."""
        with open(self.db_path, "wb") as f:
            f.write(b"CORRUPTED_SQLITE_HEADER" + bytes([0] * 500))

        with self.assertRaises(StateIntegrityError):
            self.adapter.create_and_upload_bundle(run_id="RUN_004")

    def test_05_cas_success(self):
        """5. CAS update succeeds when expected generation matches."""
        _, gen1 = self.adapter.create_and_upload_bundle(run_id="RUN_005_1")
        ptr_blob = self.mock_client.bucket("test-bucket").blob("stockbot-state/current.json")
        current_gen = ptr_blob.generation

        bundle_path, _ = self.adapter.create_and_upload_bundle(run_id="RUN_005_2", expected_generation=current_gen)
        self.assertIn("RUN_005_2", bundle_path)

    def test_06_cas_conflict(self):
        """6. CAS update fails with StateConflictError on generation mismatch."""
        self.adapter.create_and_upload_bundle(run_id="RUN_006_1")
        stale_gen = 999

        with self.assertRaises(StateConflictError):
            self.adapter.create_and_upload_bundle(run_id="RUN_006_2", expected_generation=stale_gen)

    def test_07_old_state_overwrite_blocked(self):
        """7. Concurrent writer state overwrite is blocked by CAS."""
        _, gen = self.adapter.create_and_upload_bundle(run_id="RUN_007_A")

        ptr_blob = self.mock_client.bucket("test-bucket").blob("stockbot-state/current.json")
        ptr_blob.generation += 1

        with self.assertRaises(StateConflictError):
            self.adapter.create_and_upload_bundle(run_id="RUN_007_B", expected_generation=gen)

    def test_08_partial_upload_non_promotion(self):
        """8. If current.json update fails, pointer does not point to new bundle."""
        self.adapter.create_and_upload_bundle(run_id="RUN_008_ORIG")
        ptr_blob = self.mock_client.bucket("test-bucket").blob("stockbot-state/current.json")
        orig_data = json.loads(ptr_blob.download_as_bytes().decode("utf-8"))

        with self.assertRaises(StateConflictError):
            self.adapter.create_and_upload_bundle(run_id="RUN_008_FAILED", expected_generation=9999)

        current_data = json.loads(ptr_blob.download_as_bytes().decode("utf-8"))
        self.assertEqual(orig_data["run_id"], current_data["run_id"])
        self.assertEqual(current_data["run_id"], "RUN_008_ORIG")

    # =========================================================================
    # Group 2: Cloud Runner Execution & Idempotency Receipts (Tests 9-18)
    # =========================================================================

    def test_09_intraday_mode_resolution(self):
        """9. CloudRunner resolves INTRADAY mode and session code 1120."""
        with patch("cloud_runner.get_current_kst_time", return_value=datetime(2026, 9, 3, 11, 20, 0)):
            runner = CloudRunner(report_mode="INTRADAY", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)
        self.assertEqual(runner.report_mode, "INTRADAY")
        self.assertEqual(runner.session_code, "1120")
        self.assertEqual(runner.run_id, "20260903_INTRADAY_1120")
        self.assertEqual(runner.dispatch_id, "20260903_1120")

    def test_10_postmarket_mode_resolution(self):
        """10. CloudRunner resolves POSTMARKET mode and session code 1535."""
        with patch("cloud_runner.get_current_kst_time", return_value=datetime(2026, 9, 3, 15, 35, 0)):
            runner = CloudRunner(report_mode="POSTMARKET", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)
        self.assertEqual(runner.report_mode, "POSTMARKET")
        self.assertEqual(runner.session_code, "1535")
        self.assertEqual(runner.run_id, "20260903_POSTMARKET_1535")
        self.assertEqual(runner.dispatch_id, "20260903_1535")

    def test_11_invalid_report_mode_fails_closed(self):
        """11. Invalid or missing REPORT_MODE fails closed with ValueError (auto-detection forbidden)."""
        with self.assertRaises(ValueError):
            CloudRunner(report_mode="INVALID_MODE", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)

        with self.assertRaises(ValueError):
            CloudRunner(report_mode=None, bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)

    def test_12_delayed_execution_uses_explicit_report_mode(self):
        """12. Delayed execution (e.g. 11:20 job at 11:55) respects explicit REPORT_MODE without change."""
        dt_delayed = datetime(2026, 9, 1, 11, 55, 0)
        run_id = build_run_id(dt_delayed, "INTRADAY")
        self.assertEqual(run_id, "20260901_INTRADAY_1120")

    def test_12b_1335_intraday_has_distinct_session_and_dispatch_ids(self):
        """13:35 INTRADAY uses its own session, run ID, and dispatch ID."""
        with patch("cloud_runner.get_current_kst_time", return_value=datetime(2026, 9, 3, 13, 35, 0)):
            runner = CloudRunner(report_mode="INTRADAY", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)

        self.assertEqual(runner.session_code, "1335")
        self.assertEqual(runner.run_id, "20260903_INTRADAY_1335")
        self.assertEqual(runner.dispatch_id, "20260903_1335")

    def test_12c_1120_receipt_does_not_block_1335_intraday(self):
        """A same-day 11:20 receipt must not suppress the 13:35 report."""
        self.adapter.create_and_upload_bundle(run_id="BOOTSTRAP")
        with patch("cloud_runner.get_current_kst_time", return_value=datetime(2026, 9, 3, 11, 20, 0)):
            runner_1120 = CloudRunner(report_mode="INTRADAY", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)
        self.adapter.create_dispatch_receipt(runner_1120.run_id, {"status": "PREVIOUS_SUCCESS"})

        with patch("cloud_runner.get_current_kst_time", return_value=datetime(2026, 9, 3, 13, 35, 0)):
            runner_1335 = CloudRunner(report_mode="INTRADAY", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)

        self.assertNotEqual(runner_1120.run_id, runner_1335.run_id)
        self.assertEqual(runner_1335._detect_state(), _SM_FRESH)

    def test_12d_1335_receipt_blocks_1335_intraday_rerun(self):
        """A same-day 13:35 receipt must still suppress its duplicate rerun."""
        self.adapter.create_and_upload_bundle(run_id="BOOTSTRAP")
        with patch("cloud_runner.get_current_kst_time", return_value=datetime(2026, 9, 3, 13, 35, 0)):
            runner = CloudRunner(report_mode="INTRADAY", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)
        self.adapter.create_dispatch_receipt(runner.run_id, {"status": "PREVIOUS_SUCCESS"})

        self.assertEqual(runner._detect_state(), _SM_ALREADY_COMPLETED)

    def test_13_state_commit_then_gmail_success_creates_gcs_receipt(self):
        """13. Successful run sequence (V4 -> commit -> Gmail -> receipt) creates immutable GCS receipt."""
        self.adapter.create_and_upload_bundle(run_id="BOOTSTRAP")
        runner = CloudRunner(report_mode="INTRADAY", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)

        fake_held = [{"stock_code": "005930", "stock_name": "삼성전자", "quantity": 10, "avg_buy_price": 70000}]
        fake_payload = self._sample_report_payload()

        with patch("main.run_post_market_analysis", return_value=(fake_held, [], fake_payload)) as mock_main:
            res = runner.run()
            self.assertEqual(res["status"], "SUCCESS")
            self.assertTrue(self.adapter.has_dispatch_receipt(runner.run_id))
        self.assertEqual(mock_main.call_args.kwargs["report_session"], runner.session_code)
        self.assertEqual(mock_main.call_args.kwargs["report_run_id"], runner.run_id)

    def test_14_gcs_receipt_exists_skips_rerun(self):
        """14. If GCS dispatch receipt exists, runner immediately skips execution (ALREADY_COMPLETED)."""
        self.adapter.create_and_upload_bundle(run_id="BOOTSTRAP")
        runner = CloudRunner(report_mode="INTRADAY", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)

        self.adapter.create_dispatch_receipt(runner.run_id, {"status": "PREVIOUS_SUCCESS"})

        res = runner.run()
        self.assertEqual(res["status"], _SM_ALREADY_COMPLETED)
        self.assertEqual(res["run_id"], runner.run_id)

    def test_15_gmail_failure_suppresses_receipt(self):
        """15. If Gmail dispatch fails, GCS dispatch receipt is NOT created."""
        self.adapter.create_and_upload_bundle(run_id="BOOTSTRAP")
        runner = CloudRunner(report_mode="INTRADAY", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)

        fake_held = [{"stock_code": "005930", "stock_name": "삼성전자", "quantity": 10, "avg_buy_price": 70000}]
        fake_payload = self._sample_report_payload()
        fake_payload["notifier"].send_email.return_value = False

        with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "0"}):
            with patch("main.run_post_market_analysis", return_value=(fake_held, [], fake_payload)):
                with self.assertRaises(RuntimeError):
                    runner.run()

        self.assertFalse(self.adapter.has_dispatch_receipt(runner.run_id))

    def test_16_receipt_cas_duplicate_creation_blocked(self):
        """16. Receipt creation with generation_match=0 blocks duplicate receipt creation on same run_id."""
        self.adapter.create_dispatch_receipt("RUN_CAS_01", {"attempt": 1})
        with self.assertRaises(StateConflictError):
            self.adapter.create_dispatch_receipt("RUN_CAS_01", {"attempt": 2})

    def test_17_state_commit_failure_suppresses_gmail(self):
        """17. If state commit to GCS fails, runner raises error before Gmail dispatch."""
        self.adapter.create_and_upload_bundle(run_id="BOOTSTRAP")
        runner = CloudRunner(report_mode="INTRADAY", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)

        fake_held = [{"stock_code": "005930", "stock_name": "삼성전자", "quantity": 10, "avg_buy_price": 70000}]
        fake_payload = self._sample_report_payload()

        with patch("main.run_post_market_analysis", return_value=(fake_held, [], fake_payload)):
            with patch.object(runner.state_adapter, "create_and_upload_bundle", side_effect=StateConflictError("CAS conflict")):
                with self.assertRaises(StateConflictError):
                    runner.run()

        fake_payload["notifier"].send_email.assert_not_called()

    def test_18_main_failure_no_state_commit(self):
        """18. Failure during analysis does NOT commit corrupted state to GCS."""
        self.adapter.create_and_upload_bundle(run_id="BOOTSTRAP")
        ptr_blob = self.mock_client.bucket("test-bucket").blob("stockbot-state/current.json")
        initial_gen = ptr_blob.generation

        runner = CloudRunner(report_mode="INTRADAY", bucket_name="test-bucket", storage_client=self.mock_client, state_dir=self.state_dir)

        with patch("main.run_post_market_analysis", side_effect=RuntimeError("Simulated Kiwoom API failure")):
            with self.assertRaises(RuntimeError):
                runner.run()

        self.assertEqual(ptr_blob.generation, initial_gen)

    # =========================================================================
    # Group 3: Single-Shot Policy Shadow & 45m Authority (Tests 19-25)
    # =========================================================================

    def test_19_policy_observer_does_not_fabricate_45m_timestamp(self):
        """19. Cloud Policy observer does not fabricate or hardcode 11:15/15:30 timestamps."""
        held_no_ts = [{
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "quantity": 10,
            "avg_buy_price": 70000.0,
            "current_price": 72000.0,
            "atr_14": 1500.0,
            "f_score": 80.0,
            "t_score": 75.0,
            "is_etf": False,
        }]
        snaps = observe_report_policy_shadow(self.db, held_no_ts, "RUN_NO_TS", asof_dt=datetime(2026, 9, 1, 11, 20), policy_store=self.policy_store)
        self.assertEqual(len(snaps), 1)
        self.assertIsNone(snaps[0]["completed_45m_timestamp"])

    def test_20_policy_observer_reuses_authoritative_45m_raw_fields(self):
        """20. Cloud Policy observer reuses authoritative is_45m_bearish_2plus and is_45m_breakdown."""
        held_with_45m = [{
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "quantity": 10,
            "avg_buy_price": 70000.0,
            "current_price": 72000.0,
            "atr_14": 1500.0,
            "f_score": 80.0,
            "t_score": 75.0,
            "is_45m_bearish_2plus": True,
            "is_45m_breakdown": False,
            "completed_45m_timestamp": "2026-09-01 11:15:00",
            "is_etf": False,
        }]
        snaps = observe_report_policy_shadow(self.db, held_with_45m, "RUN_45M_RAW", asof_dt=datetime(2026, 9, 1, 11, 20), policy_store=self.policy_store)
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["is_45m_bearish_2plus"], 1)
        self.assertEqual(snaps[0]["is_45m_breakdown"], 0)
        self.assertEqual(snaps[0]["completed_45m_timestamp"], "2026-09-01 11:15:00")

    def test_21_policy_failure_preserves_v4_mail_and_sets_data_unavailable(self):
        """21. Policy observation failure logs warning, maintains V4 report generation, and sets DATA_UNAVAILABLE."""
        from tests.fixtures.sample_portfolio_fixture import SAMPLE_HELD_PORTFOLIO
        held = SAMPLE_HELD_PORTFOLIO[:2]

        with open(self.policy_db_path, "wb") as f:
            f.write(b"CORRUPT_POLICY_DB")

        reader = PolicyShadowReader(db_path=self.policy_db_path)
        payload = reader.build_report_payload(datetime.now(), held)
        self.assertEqual(payload["status"], "UNAVAILABLE")

        html_out = generate_mobile_html_report_v2(
            date_str="2026-09-01 11:20",
            total_count=len(held),
            caught_signals=[],
            all_results=[],
            held_portfolio=held,
            policy_display=payload,
        )
        self.assertIn("Policy Shadow", html_out)
        self.assertIn("DATA_UNAVAILABLE", html_out)

    def test_22_policy_unavailable_suppresses_action_numbers(self):
        """22. When Policy is DATA_UNAVAILABLE, summary and overview cards suppress policy action numbers and stage gates."""
        held = [{
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "quantity": 10,
            "avg_buy_price": 70000.0,
            "current_price": 71000.0,
            "atr_14": 1500.0,
            "trade_mode": "NORMAL",
            "is_etf": False,
        }]
        unavailable_payload = {"status": "UNAVAILABLE", "rows": [], "transition_rows": []}

        summary_html = build_today_action_summary_html(held, unavailable_payload)
        self.assertIn("Policy Shadow</b>: DATA_UNAVAILABLE", summary_html)
        self.assertNotIn("STAGE2_READY", summary_html)
        self.assertNotIn("STAGE3_READY", summary_html)

        strategy_html = build_all_holdings_strategy_table_html(held, unavailable_payload)
        self.assertIn("Policy: ⚠️ DATA_UNAVAILABLE", strategy_html)

    def test_23_new_433_state_succession(self):
        """23. NEW_433 cycle status and stage succession across observations."""
        self.policy_store.set_meta_json("bootstrap_codes", [])

        held = [{
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "quantity": 10,
            "avg_buy_price": 70000.0,
            "current_price": 71000.0,
            "atr_14": 1500.0,
            "f_score": 80.0,
            "t_score": 70.0,
            "risk_target_qty": 25,
            "is_etf": False,
        }]
        snaps1 = observe_report_policy_shadow(self.db, held, "RUN_1", asof_dt=datetime(2026, 9, 1, 11, 20), policy_store=self.policy_store)
        cycle1 = self.policy_store.latest_cycle("005930")
        self.assertIsNotNone(cycle1)
        self.assertEqual(cycle1["position_kind"], "NEW_433_CYCLE")
        self.assertEqual(cycle1["stage"], 1)

    def test_24_loss_defense_state_succession(self):
        """24. Loss defense state triggers and persists when T3D decline and loss threshold breached."""
        for day, t_val in [(1, 75.0), (2, 60.0), (3, 40.0)]:
            h_prev = [{
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "quantity": 10,
                "avg_buy_price": 70000.0,
                "current_price": 70000.0,
                "atr_14": 1500.0,
                "f_score": 80.0,
                "t_score": t_val,
                "is_etf": False,
            }]
            observe_report_policy_shadow(self.db, h_prev, f"RUN_DAY_{day}", asof_dt=datetime(2026, 9, day, 11, 20), policy_store=self.policy_store)

        held_loss = [{
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "quantity": 10,
            "avg_buy_price": 70000.0,
            "current_price": 56000.0,
            "atr_14": 1500.0,
            "f_score": 80.0,
            "t_score": 30.0,
            "is_45m_bearish_2plus": 1,
            "is_etf": False,
        }]
        snaps = observe_report_policy_shadow(self.db, held_loss, "RUN_DEFENSE", asof_dt=datetime(2026, 9, 4, 11, 20), policy_store=self.policy_store)
        cycle = self.policy_store.latest_cycle("005930")
        self.assertIsNotNone(cycle)
        self.assertIn(cycle["defense_state"], ["WAIT_REBOUND", "TRAIL_ACTIVE"])

    def test_25_missing_historical_input_data_gap(self):
        """25. Missing F/T score on ETF yields NOT_APPLICABLE/OK rather than unhandled exception."""
        etf_held = [{
            "stock_code": "161510",
            "stock_name": "PLUS 고배당주",
            "quantity": 50,
            "avg_buy_price": 25000.0,
            "current_price": 25500.0,
            "atr_14": 500.0,
            "f_score": None,
            "t_score": 75.0,
            "is_etf": True,
        }]
        snaps = observe_report_policy_shadow(self.db, etf_held, "RUN_ETF", asof_dt=datetime(2026, 9, 1, 11, 20), policy_store=self.policy_store)
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["f_baseline_status"], "NOT_APPLICABLE")

    # =========================================================================
    # Group 4: External I/O Zero-Call Verification in TEST_MODE (Tests 26-28)
    # =========================================================================

    def test_26_test_mode_gmail_zero_calls(self):
        """26. Under STOCKBOT_TEST_MODE=1, Gmail send_email returns False without opening SMTP."""
        from src.notifications.gmail_notifier import GmailNotifier
        with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "1"}):
            notifier = GmailNotifier(sender_email="test@test.com", app_password="dummy")
            with patch("smtplib.SMTP_SSL") as mock_smtp:
                sent = notifier.send_email("Test Subject", "<p>Test Content</p>")
                self.assertFalse(sent)
                mock_smtp.assert_not_called()

    def test_27_test_mode_gcs_real_call_blocked(self):
        """27. Under STOCKBOT_TEST_MODE=1, GCSStateAdapter without mock client raises StateAdapterError."""
        with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "1"}):
            adapter_unmocked = GCSStateAdapter(bucket_name="test", storage_client=None)
            with self.assertRaises(StateAdapterError):
                adapter_unmocked._get_storage_client()

    def test_28_test_mode_kiwoom_real_call_blocked(self):
        """28. Under Mock/Test mode, Kiwoom API returns MOCK_ACCESS_TOKEN without real HTTP request."""
        from src.api.kiwoom_api import KiwoomAPIClient
        client = KiwoomAPIClient(use_mock=True)
        with patch("requests.post") as mock_post:
            token = client.get_access_token()
            self.assertEqual(token, "MOCK_ACCESS_TOKEN")
            mock_post.assert_not_called()


class TestCloudRunPhase1StateMachine(unittest.TestCase):
    """
    Group 5: 3-State Machine, Prepared Dispatch, FIX-1, FIX-2 (Tests 29-46)
    Covers all 18 required scenarios:
    1. state commit -> Gmail failure
    2. same run_id rerun -> RESUME_PENDING_EMAIL
    3. resume V4 0 calls
    4. resume Policy observe 0 calls
    5. resume state mutation 0 calls
    6. prepared HTML reuse
    7. prepared XLSX reuse
    8. Gmail success -> receipt creation
    9. receipt exists -> complete skip
    10. prepared hash mismatch -> Fail-Closed
    11. prepared missing -> Fail-Closed
    12. committed_run_id match + invalid prepared -> NEVER fall through to FRESH
    13. new run_id -> FRESH
    14. payload None -> Fail-Closed
    15. missing required payload field -> Fail-Closed
    16. payload failure -> state commit 0
    17. payload failure -> Gmail 0
    18. payload failure -> receipt 0
    """

    def setUp(self):
        self._orig_env = os.environ.copy()
        os.environ["STOCKBOT_TEST_MODE"] = "1"
        self.test_dir = Path(tempfile.mkdtemp(prefix="stockbot_sm_test_"))
        self.state_dir = self.test_dir / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("data", "config", "logs"):
            (self.state_dir / sub).mkdir(parents=True, exist_ok=True)

        self.db_path = self.state_dir / "data" / "stock_system.db"
        self.db = DatabaseManager(db_path=str(self.db_path))
        self.db.execute_non_query(
            "INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES ('005930', '삼성전자')"
        )

        self.policy_db_path = self.state_dir / "data" / "policy_shadow.db"
        PolicyShadowStore(db_path=self.policy_db_path)

        self.holdings_path = self.state_dir / "config" / "portfolio_holdings.json"
        with open(self.holdings_path, "w", encoding="utf-8") as f:
            json.dump([{"stock_code": "005930", "stock_name": "삼성전자", "quantity": 10}], f)

        self.mock_client = MockStorageClient()
        self.adapter = GCSStateAdapter(
            bucket_name="test-bucket",
            prefix="stockbot-state",
            state_dir=self.state_dir,
            storage_client=self.mock_client,
        )
        self.adapter.create_and_upload_bundle(run_id="BOOTSTRAP_SM")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_runner(self, force: bool = False) -> CloudRunner:
        return CloudRunner(
            report_mode="INTRADAY",
            bucket_name="test-bucket",
            storage_client=self.mock_client,
            state_dir=self.state_dir,
            force=force,
        )

    def _make_mock_notifier(self, send_success: bool = True) -> MagicMock:
        n = MagicMock()
        n.recipient_email = "recipient@example.com"
        n.send_email.return_value = send_success
        return n

    def _fresh_report_payload(self, notifier: MagicMock) -> dict:
        return {
            "excel_path": self.state_dir / "data" / "dummy.xlsx",
            "csv_held_path": self.state_dir / "data" / "dummy_held.csv",
            "csv_summary_path": self.state_dir / "data" / "dummy_sum.csv",
            "html_report": (
                '<html><body data-render-version="V2">'
                'V4-PILOT-C 주요 대응 지침'
                " data-held-stock-codes='[\"005930\"]'</body></html>"
            ),
            "subject": "[1차 장중 리포트(11:20)] V4-PILOT-C",
            "dispatch_id": "20260901_1120",
            "dispatch_tag": "1차 장중 리포트(11:20)",
            "notifier": notifier,
            "attachments": [],
            "render_version": "V2",
            "policy_display": {"status": "OK", "rows": []},
            "fingerprint_id": "FP_test1234",
        }

    def _setup_committed_resume_state(self, run_id: str, html: str = "<html>resume</html>", xlsx_path: Optional[Path] = None):
        """Helper to simulate state commit before Gmail failure."""
        self.adapter.save_prepared_dispatch(
            run_id=run_id,
            subject="[테스트] Resume Subject",
            html_content=html,
            report_mode="INTRADAY",
            xlsx_path=xlsx_path,
        )
        ptr_blob = self.mock_client.bucket("test-bucket").blob("stockbot-state/current.json")
        ptr_blob.reload()
        gen = ptr_blob.generation
        ptr_data = json.loads(ptr_blob.download_as_bytes().decode("utf-8"))
        ptr_data["run_id"] = run_id
        ptr_blob.upload_from_string(
            json.dumps(ptr_data).encode("utf-8"),
            content_type="application/json",
            if_generation_match=gen,
        )

    # 1. state commit -> Gmail failure
    def test_29_state_commit_gmail_failure_suppresses_receipt(self):
        """29. State commit success -> Gmail failure -> receipt NOT created."""
        runner = self._make_runner()
        fake_held = [{"stock_code": "005930", "stock_name": "삼성전자", "quantity": 10}]
        notifier = self._make_mock_notifier(send_success=False)
        payload = self._fresh_report_payload(notifier)

        with patch("main.run_post_market_analysis", return_value=(fake_held, [], payload)):
            with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "0"}):
                with self.assertRaises(RuntimeError):
                    runner.run()

        self.assertFalse(self.adapter.has_dispatch_receipt(runner.run_id))

    # 2. same run_id rerun -> RESUME_PENDING_EMAIL
    def test_30_same_run_id_rerun_detects_resume_state(self):
        """30. After state commit (Gmail failed), same run_id rerun detects RESUME_PENDING_EMAIL."""
        runner = self._make_runner()
        self._setup_committed_resume_state(runner.run_id)
        detected = runner._detect_state()
        self.assertEqual(detected, _SM_RESUME)

    # 3. resume V4 0 calls
    def test_31_resume_path_does_not_call_v4(self):
        """31. On RESUME_PENDING_EMAIL, run_post_market_analysis is NOT called."""
        runner = self._make_runner()
        self._setup_committed_resume_state(runner.run_id)

        mock_notifier = self._make_mock_notifier(send_success=True)
        with patch("main.run_post_market_analysis") as mock_v4:
            with patch("src.notifications.gmail_notifier.GmailNotifier", return_value=mock_notifier):
                res = runner.run()

        mock_v4.assert_not_called()
        self.assertEqual(res["status"], _SM_RESUME)

    # 4. resume Policy observe 0 calls
    def test_32_resume_path_does_not_call_policy_observe(self):
        """32. On RESUME_PENDING_EMAIL, observe_report_policy_shadow is NOT called."""
        runner = self._make_runner()
        self._setup_committed_resume_state(runner.run_id)

        mock_notifier = self._make_mock_notifier(send_success=True)
        with patch("src.policy.policy_shadow_observer.observe_report_policy_shadow") as mock_obs:
            with patch("src.notifications.gmail_notifier.GmailNotifier", return_value=mock_notifier):
                runner.run()

        mock_obs.assert_not_called()

    # 5. resume state mutation 0 calls
    def test_33_resume_path_does_not_mutate_state(self):
        """33. On RESUME_PENDING_EMAIL, GCS bundle CAS commit is NOT called."""
        runner = self._make_runner()
        self._setup_committed_resume_state(runner.run_id)

        ptr_blob = self.mock_client.bucket("test-bucket").blob("stockbot-state/current.json")
        gen_after_setup = ptr_blob.generation

        mock_notifier = self._make_mock_notifier(send_success=True)
        with patch("src.notifications.gmail_notifier.GmailNotifier", return_value=mock_notifier):
            runner.run()

        ptr_blob2 = self.mock_client.bucket("test-bucket").blob("stockbot-state/current.json")
        self.assertEqual(ptr_blob2.generation, gen_after_setup,
                         "RESUME must not commit new state bundle (generation changed)")

    # 6. prepared HTML reuse
    def test_34_resume_uses_prepared_html(self):
        """34. On RESUME, persisted HTML is used (not regenerated)."""
        runner = self._make_runner()
        unique_html = "<html>UNIQUE_RESUME_MARKER_12345</html>"
        self._setup_committed_resume_state(runner.run_id, html=unique_html)

        captured_html = []
        mock_notifier = MagicMock()
        mock_notifier.recipient_email = "r@example.com"
        mock_notifier.send_email.side_effect = lambda subject, html_content, attachments: (
            captured_html.append(html_content) or True
        )

        with patch("src.notifications.gmail_notifier.GmailNotifier", return_value=mock_notifier):
            runner.run()

        self.assertEqual(len(captured_html), 1)
        self.assertIn("UNIQUE_RESUME_MARKER_12345", captured_html[0])

    # 7. prepared XLSX reuse
    def test_35_resume_uses_prepared_xlsx(self):
        """35. On RESUME, persisted XLSX is included in attachments if it exists."""
        runner = self._make_runner()
        xlsx_src = self.state_dir / "data" / "resume_test.xlsx"
        xlsx_src.write_bytes(b"PK\x03\x04FAKE_XLSX")

        self._setup_committed_resume_state(runner.run_id, xlsx_path=xlsx_src)

        captured_attachments = []
        mock_notifier = MagicMock()
        mock_notifier.recipient_email = "r@example.com"
        mock_notifier.send_email.side_effect = lambda subject, html_content, attachments: (
            captured_attachments.extend(attachments) or True
        )

        with patch("src.notifications.gmail_notifier.GmailNotifier", return_value=mock_notifier):
            runner.run()

        self.assertTrue(
            any(Path(a).name == "report.xlsx" or PREPARED_DISPATCH_XLSX in str(a).replace("\\", "/") for a in captured_attachments),
            f"Expected prepared XLSX in attachments, got: {captured_attachments}"
        )

    # 8. Gmail success -> receipt creation
    def test_36_resume_gmail_success_creates_receipt(self):
        """36. On RESUME, successful Gmail creates GCS dispatch receipt."""
        runner = self._make_runner()
        self._setup_committed_resume_state(runner.run_id)

        mock_notifier = self._make_mock_notifier(send_success=True)
        with patch("src.notifications.gmail_notifier.GmailNotifier", return_value=mock_notifier):
            res = runner.run()

        self.assertEqual(res["status"], _SM_RESUME)
        self.assertTrue(self.adapter.has_dispatch_receipt(runner.run_id))

    # 9. receipt exists -> complete skip
    def test_37_receipt_exists_skips_everything(self):
        """37. Receipt already exists -> ALREADY_COMPLETED, no bundle download, no V4, no Gmail."""
        runner = self._make_runner()
        self.adapter.create_dispatch_receipt(runner.run_id, {"pre_existing": True})

        with patch("main.run_post_market_analysis") as mock_v4:
            with patch.object(runner.state_adapter, "download_current_state") as mock_dl:
                res = runner.run()

        self.assertEqual(res["status"], _SM_ALREADY_COMPLETED)
        mock_v4.assert_not_called()
        mock_dl.assert_not_called()

    # 10. prepared hash mismatch -> Fail-Closed
    def test_38_prepared_dispatch_hash_mismatch_fails_closed(self):
        """38. Tampered prepared dispatch JSON (hash mismatch) -> PREPARED_DISPATCH_INVALID RuntimeError."""
        runner = self._make_runner()
        self._setup_committed_resume_state(runner.run_id)

        # Tamper the JSON
        pd_path = self.state_dir / PREPARED_DISPATCH_JSON
        with open(pd_path, "r", encoding="utf-8") as f:
            pd = json.load(f)
        pd["html_content"] = "<html>TAMPERED</html>"
        with open(pd_path, "w", encoding="utf-8") as f:
            json.dump(pd, f)

        mock_notifier = self._make_mock_notifier()
        with patch("src.notifications.gmail_notifier.GmailNotifier", return_value=mock_notifier):
            with self.assertRaises(RuntimeError) as ctx:
                runner.run()
        self.assertIn("PREPARED_DISPATCH_INVALID", str(ctx.exception))

    # 11. prepared missing -> Fail-Closed
    def test_39_prepared_dispatch_missing_fails_closed(self):
        """39. Missing prepared dispatch file on resume -> PREPARED_DISPATCH_INVALID RuntimeError."""
        runner = self._make_runner()
        run_id = runner.run_id

        # Set current.json to point to run_id but do not create prepared_dispatch file
        ptr_blob = self.mock_client.bucket("test-bucket").blob("stockbot-state/current.json")
        ptr_blob.reload()
        gen = ptr_blob.generation
        ptr_data = json.loads(ptr_blob.download_as_bytes().decode("utf-8"))
        ptr_data["run_id"] = run_id
        ptr_blob.upload_from_string(
            json.dumps(ptr_data).encode("utf-8"),
            content_type="application/json",
            if_generation_match=gen,
        )

        pd_path = self.state_dir / PREPARED_DISPATCH_JSON
        if pd_path.exists():
            pd_path.unlink()

        with self.assertRaises(RuntimeError) as ctx:
            runner.run()
        self.assertIn("PREPARED_DISPATCH_INVALID", str(ctx.exception))

    # 12. committed_run_id match + invalid prepared -> NEVER fall through to FRESH
    def test_40_invalid_prepared_never_falls_through_to_fresh(self):
        """40. committed_run_id == run_id with invalid prepared dispatch NEVER calls V4 (no FRESH fall-through)."""
        runner = self._make_runner()
        self._setup_committed_resume_state(runner.run_id)

        # Invalidate prepared dispatch
        pd_path = self.state_dir / PREPARED_DISPATCH_JSON
        with open(pd_path, "w", encoding="utf-8") as f:
            f.write("CORRUPTED_JSON_CONTENT")

        with patch("main.run_post_market_analysis") as mock_v4:
            with self.assertRaises(RuntimeError) as ctx:
                runner.run()
            mock_v4.assert_not_called()
        self.assertIn("PREPARED_DISPATCH_INVALID", str(ctx.exception))

    # 13. new run_id -> FRESH
    def test_41_committed_run_id_mismatch_triggers_fresh(self):
        """41. committed_run_id != current run_id -> FRESH execution path."""
        runner = self._make_runner()
        committed = self.adapter.get_committed_run_id()
        self.assertNotEqual(committed, runner.run_id,
                            "Precondition: BOOTSTRAP_SM != today run_id")
        detected = runner._detect_state()
        self.assertEqual(detected, "FRESH")

    # 14. payload None -> Fail-Closed
    def test_42_report_payload_none_raises_runtime_error(self):
        """42. _validate_report_payload(None) -> RuntimeError with REPORT_PAYLOAD_MISSING."""
        with self.assertRaises(RuntimeError) as ctx:
            _validate_report_payload(None, "RUN_NONE_TEST")
        self.assertIn("REPORT_PAYLOAD_MISSING", str(ctx.exception))

    def test_43_report_payload_none_does_not_return_success(self):
        """43. When report_payload is None, runner.run() does NOT return SUCCESS."""
        runner = self._make_runner()
        fake_held = [{"stock_code": "005930", "quantity": 10}]

        with patch("main.run_post_market_analysis", return_value=(fake_held, [], None)):
            with self.assertRaises(RuntimeError) as ctx:
                runner.run()
        self.assertIn("REPORT_PAYLOAD_MISSING", str(ctx.exception))

    # 15. missing required payload field -> Fail-Closed
    def test_44_report_payload_missing_field_fails_closed(self):
        """44. Payload missing required field (subject) -> RuntimeError REPORT_PAYLOAD_MISSING_FIELDS."""
        incomplete_payload = {
            "html_report": "<html>test</html>",
            "notifier": MagicMock(),
            "dispatch_id": "20260901_1120",
            "dispatch_tag": "test",
        }
        with self.assertRaises(RuntimeError) as ctx:
            _validate_report_payload(incomplete_payload, "RUN_MISSING_FIELD")
        self.assertIn("REPORT_PAYLOAD_MISSING_FIELDS", str(ctx.exception))
        self.assertIn("subject", str(ctx.exception))

    # 16. payload failure -> state commit 0
    def test_45_payload_failure_prevents_state_commit(self):
        """45. report_payload=None -> GCS bundle CAS commit must NOT be called."""
        runner = self._make_runner()
        fake_held = [{"stock_code": "005930", "quantity": 10}]

        with patch("main.run_post_market_analysis", return_value=(fake_held, [], None)):
            with patch.object(
                runner.state_adapter, "create_and_upload_bundle"
            ) as mock_commit:
                with self.assertRaises(RuntimeError):
                    runner.run()

        mock_commit.assert_not_called()

    # 17. payload failure -> Gmail 0
    def test_46_payload_failure_prevents_gmail_and_receipt(self):
        """46. report_payload=None -> Gmail send_email must NOT be called & receipt NOT created."""
        runner = self._make_runner()
        fake_held = [{"stock_code": "005930", "quantity": 10}]
        mock_notifier = self._make_mock_notifier()

        with patch("main.run_post_market_analysis", return_value=(fake_held, [], None)):
            with self.assertRaises(RuntimeError):
                runner.run()

        mock_notifier.send_email.assert_not_called()
        self.assertFalse(self.adapter.has_dispatch_receipt(runner.run_id))

    # 18. Bootstrap manifest runtime contract compatibility
    def test_47_bootstrap_manifest_runtime_schema_compatibility(self):
        """47. Bootstrap manifest files mapping MUST be {rel_path: str_sha256} for GCSStateAdapter compatibility."""
        manifest = {
            "schema_version": "1.0",
            "bundle_type": "INITIAL_BOOTSTRAP",
            "created_at_kst": "2026-09-01 14:52:47",
            "files": {
                "data/stock_system.db": sha256_file(self.db_path),
                "data/policy_shadow.db": sha256_file(self.policy_db_path),
                "config/portfolio_holdings.json": sha256_file(self.holdings_path),
            },
            "file_details": {
                "data/stock_system.db": {
                    "relative_path": "data/stock_system.db",
                    "size_bytes": self.db_path.stat().st_size,
                    "sha256": sha256_file(self.db_path),
                    "integrity": "ok",
                },
            },
        }
        # Must pass _validate_manifest without raising CorruptStateError
        self.adapter._validate_manifest(manifest)

        # Confirm all values in files are str
        for rel_path, expected_hash in manifest["files"].items():
            self.assertIsInstance(expected_hash, str)

    # 19. Initial bootstrap restore cycle through GCSStateAdapter
    def test_48_bootstrap_bundle_download_and_restore_cycle(self):
        """48. GCSStateAdapter.download_current_state restores initial bootstrap bundle successfully."""
        # Create a bootstrap bundle and pointer
        bundle_path = "stockbot-state/bundles/state_bootstrap_test.zip"
        zip_path = self.test_dir / "bootstrap_bundle.zip"
        
        manifest_data = {
            "schema_version": "1.0",
            "bundle_type": "INITIAL_BOOTSTRAP",
            "created_at_kst": "2026-09-01 14:52:47",
            "files": {
                "data/stock_system.db": sha256_file(self.db_path),
                "data/policy_shadow.db": sha256_file(self.policy_db_path),
                "config/portfolio_holdings.json": sha256_file(self.holdings_path),
            },
            "previous_bundle": None,
        }
        manifest_file = self.test_dir / MANIFEST_FILENAME
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(manifest_file, arcname=MANIFEST_FILENAME)
            for rel in STATE_FILES_TO_BUNDLE:
                zf.write(self.state_dir / rel, arcname=rel)

        bundle_sha256 = sha256_file(zip_path)
        bucket = self.mock_client.bucket("test-bucket")
        bundle_blob = bucket.blob(bundle_path)
        bundle_blob.upload_from_filename(str(zip_path))

        pointer_data = {
            "bundle_path": bundle_path,
            "bundle_sha256": bundle_sha256,
            "run_id": "initial_bootstrap",
            "created_at_kst": "2026-09-01 14:52:47",
            "manifest": manifest_data,
        }
        ptr_blob = bucket.blob("stockbot-state/current.json")
        ptr_blob.upload_from_string(json.dumps(pointer_data).encode("utf-8"))

        # Restore into fresh state directory
        restore_dir = self.test_dir / "restore_test"
        restore_adapter = GCSStateAdapter("test-bucket", "stockbot-state", restore_dir, self.mock_client)
        restored_manifest, gen = restore_adapter.download_current_state()

        self.assertEqual(restored_manifest["bundle_type"], "INITIAL_BOOTSTRAP")
        self.assertTrue((restore_dir / "data" / "stock_system.db").exists())
        self.assertTrue((restore_dir / "data" / "policy_shadow.db").exists())
        self.assertTrue((restore_dir / "config" / "portfolio_holdings.json").exists())


if __name__ == "__main__":
    unittest.main()
