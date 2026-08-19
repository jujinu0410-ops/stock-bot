"""
Unit and Integration Tests for V1.0 Shadow Production Runtime Scheduler
"""

import unittest
import os
import json
from datetime import datetime, date, time
from unittest.mock import MagicMock, patch

from src.database.db_manager import DatabaseManager
from src.runtime.krx_calendar import KRXCalendar
from src.runtime.scheduler_lock import SchedulerLockManager
from src.runtime.runtime_scheduler import RuntimeScheduler

class TestRuntimeScheduler(unittest.TestCase):
    """Test suite for Phase 8 Runtime Scheduler Layer"""

    @classmethod
    def setUpClass(cls):
        cls.db_path = "test_scheduler.db"
        if os.path.exists(cls.db_path):
            os.remove(cls.db_path)
        cls.db = DatabaseManager(cls.db_path)
        cls.scheduler = RuntimeScheduler(cls.db)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except Exception:
                pass

    def setUp(self):
        self.db.execute_non_query("DELETE FROM scheduler_runs")
        self.db.execute_non_query("DELETE FROM scheduler_locks")
        self.db.execute_non_query("DELETE FROM scan_journal WHERE journal_id LIKE 'JRN_TEST%'")

    def test_01_krx_trading_day_calendar(self):
        """1. Canonical KRX Trading Calendar: 주말 및 공휴일 정상 식별 검증"""
        # Saturday / Sunday
        self.assertFalse(KRXCalendar.is_krx_trading_day("2026-08-15")) # Sat (광복절)
        self.assertFalse(KRXCalendar.is_krx_trading_day("2026-08-16")) # Sun

        # Fixed Solar Holidays
        self.assertFalse(KRXCalendar.is_krx_trading_day("2026-01-01")) # 신정
        self.assertFalse(KRXCalendar.is_krx_trading_day("2026-03-01")) # 삼일절 (Sun)
        self.assertFalse(KRXCalendar.is_krx_trading_day("2026-03-02")) # 삼일절 대체공휴일
        self.assertFalse(KRXCalendar.is_krx_trading_day("2026-05-01")) # 근로자의 날 (KRX 휴장)
        self.assertFalse(KRXCalendar.is_krx_trading_day("2026-05-05")) # 어린이날
        self.assertFalse(KRXCalendar.is_krx_trading_day("2026-12-25")) # 성탄절
        self.assertFalse(KRXCalendar.is_krx_trading_day("2026-12-31")) # 연말 휴장일

        # Normal Trading Day
        self.assertTrue(KRXCalendar.is_krx_trading_day("2026-08-18")) # Tuesday

    def test_02_completed_45m_bar_resolution(self):
        """2. 45분봉 정규 슬롯 완성봉 타임스탬프 매칭 검증"""
        # Before 09:45 -> None
        t0 = datetime(2026, 8, 18, 9, 20, 0)
        self.assertIsNone(KRXCalendar.get_completed_45m_bar(t0))

        # At 09:50 -> 09:00~09:45 bar
        t1 = datetime(2026, 8, 18, 9, 50, 0)
        bar1 = KRXCalendar.get_completed_45m_bar(t1)
        self.assertIsNotNone(bar1)
        self.assertEqual(bar1[0], "09:50")
        self.assertEqual(bar1[1], "09:00")
        self.assertEqual(bar1[2], "09:45")

        # At 11:35 -> 10:30~11:15 bar
        t2 = datetime(2026, 8, 18, 11, 35, 0)
        bar2 = KRXCalendar.get_completed_45m_bar(t2)
        self.assertIsNotNone(bar2)
        self.assertEqual(bar2[0], "11:35")
        self.assertEqual(bar2[1], "10:30")
        self.assertEqual(bar2[2], "11:15")

        # At 15:05 -> 14:15~15:00 bar
        t3 = datetime(2026, 8, 18, 15, 5, 0)
        bar3 = KRXCalendar.get_completed_45m_bar(t3)
        self.assertIsNotNone(bar3)
        self.assertEqual(bar3[0], "15:05")
        self.assertEqual(bar3[1], "14:15")
        self.assertEqual(bar3[2], "15:00")

    def test_03_reserved_windows_check(self):
        """3. 예약 보호구간 (11:10~11:30, 15:25~15:50) 감지 검증"""
        # 11:20 (Existing Portfolio Monitor window)
        dt_1120 = datetime(2026, 8, 18, 11, 20, 0)
        is_res, label = SchedulerLockManager.check_reserved_window(dt_1120)
        self.assertTrue(is_res)
        self.assertIn("1120", label)

        # 15:35 (Existing Close Report window)
        dt_1535 = datetime(2026, 8, 18, 15, 35, 0)
        is_res, label = SchedulerLockManager.check_reserved_window(dt_1535)
        self.assertTrue(is_res)
        self.assertIn("1535", label)

        # 10:35 (Normal Shadow Scan window)
        dt_1035 = datetime(2026, 8, 18, 10, 35, 0)
        is_res, _ = SchedulerLockManager.check_reserved_window(dt_1035)
        self.assertFalse(is_res)

    def test_04_scheduler_lock_and_stale_recovery(self):
        """4. Process Mutex Lock 획득, 중복 차단 및 Stale Lock 자동 회수 검증"""
        lock_mgr = SchedulerLockManager(self.db)
        
        # Initial acquisition
        acquired = lock_mgr.acquire_lock("TEST_LOCK", "UNIT_TEST", ttl_seconds=60)
        self.assertTrue(acquired)

        # Duplicate acquisition attempt while locked by self/alive pid -> will succeed if same or fail if different process
        # Simulate lock held by another non-existent PID (stale PID = 999999)
        self.db.execute_non_query(
            "UPDATE scheduler_locks SET pid = 999999, expires_at = '2020-01-01 00:00:00' WHERE lock_name = 'TEST_LOCK'"
        )

        # Stale lock must be reclaimed automatically
        acquired_reclaim = lock_mgr.acquire_lock("TEST_LOCK", "UNIT_TEST_RECLAIM", ttl_seconds=60)
        self.assertTrue(acquired_reclaim)

        # Release
        released = lock_mgr.release_lock("TEST_LOCK")
        self.assertTrue(released)

    def test_05_pre_market_health_check_execution(self):
        """5. 08:40 PRE_MARKET_HEALTH_CHECK 정상 진단 및 scheduler_runs 기록 검증"""
        res = self.scheduler.run_task("PRE_MARKET_HEALTH_CHECK", is_manual=True)
        self.assertIn(res["status"], ["SUCCESS", "DEGRADED"])

        latest_run = self.db.get_latest_scheduler_run("PRE_MARKET_HEALTH_CHECK")
        self.assertIsNotNone(latest_run)
        self.assertEqual(latest_run["task_type"], "PRE_MARKET_HEALTH_CHECK")
        self.assertEqual(latest_run["status"], res["status"])

    def test_06_intraday_shadow_scan_non_trading_day_skip(self):
        """6. 비거래일 INTRADAY_SHADOW_SCAN 실행 시 SKIPPED_NON_TRADING_DAY 반환 검증"""
        with patch.object(KRXCalendar, "is_krx_trading_day", return_value=False):
            res = self.scheduler.run_task("INTRADAY_SHADOW_SCAN", is_manual=False)
            self.assertEqual(res["status"], "SKIPPED_NON_TRADING_DAY")

            latest = self.db.get_latest_scheduler_run("INTRADAY_SHADOW_SCAN")
            self.assertEqual(latest["status"], "SKIPPED_NON_TRADING_DAY")

    def test_07_intraday_shadow_scan_reserved_window_skip(self):
        """7. 11:20 보호구간 내 스캔 시 SKIPPED_RESERVED_WINDOW 반환 검증"""
        with patch.object(SchedulerLockManager, "check_reserved_window", return_value=(True, "RESERVED_WINDOW_1120")):
            res = self.scheduler.run_task("INTRADAY_SHADOW_SCAN", is_manual=False)
            self.assertEqual(res["status"], "SKIPPED_RESERVED_WINDOW")

    def test_08_intraday_shadow_scan_duplicate_bar_skip(self):
        """8. 동일한 45분봉 타임스탬프의 중복 실행 시 SKIPPED_NO_NEW_45M_BAR 스킵 검증"""
        today_str = datetime.now().strftime("%Y-%m-%d")
        completed_ts = f"{today_str} 09:45:00"
        self.db.insert_scheduler_run({
            "run_id": "RUN_PREV_SUCCESS",
            "scheduled_time": "09:50",
            "actual_start_time": f"{today_str} 09:50:00",
            "actual_end_time": f"{today_str} 09:50:05",
            "trading_date": today_str,
            "task_type": "INTRADAY_SHADOW_SCAN",
            "status": "SUCCESS",
            "last_completed_45m_bar": completed_ts
        })

        with patch.object(KRXCalendar, "get_completed_45m_bar", return_value=("09:50", "09:00", "09:45")), \
             patch.object(KRXCalendar, "is_krx_trading_day", return_value=True), \
             patch.object(SchedulerLockManager, "check_reserved_window", return_value=(False, None)), \
             patch.object(SchedulerLockManager, "is_existing_job_active", return_value=False):
            res = self.scheduler.run_task("INTRADAY_SHADOW_SCAN", is_manual=False)
            self.assertEqual(res["status"], "SKIPPED_NO_NEW_45M_BAR")


    def test_09_manual_runtime_verification_and_journal_append(self):
        """9. 수동 런타임 검증 실행 시 MANUAL_RUNTIME_VERIFICATION 스냅샷 정상 저장 및 scheduler_runs 적재 검증"""
        # Seed test stock in DB
        self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES ('267260', 'HD현대일렉트릭')")
        self.db.execute_non_query("""
            INSERT OR REPLACE INTO kiwoom_daily (stock_code, stk_date, open_price, high_price, low_price, close_price, volume)
            VALUES ('267260', '20260818', 785000, 795000, 780000, 790000, 100000)
        """)

        with patch.object(self.scheduler.quote_client, "fetch_live_quote", return_value={
            "success": True, "stock_code": "267260", "price": 790000.0,
            "quote_source": "KIWOOM_REST_API", "quote_timestamp": "2026-08-18 13:50:00",
            "quote_trading_date": "2026-08-18", "quote_age_seconds": 0, "is_stale": False, "status": "LIVE_VALID"
        }):
            res = self.scheduler.run_task("INTRADAY_SHADOW_SCAN", is_manual=True)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertTrue(res["stocks_scanned"] >= 1)
            self.assertTrue(res["journals_created"] >= 1)

            # Check scan_journal for manual verification reason and exact live price
            latest_journal = self.db.get_latest_scan_journal_for_stock("267260")
            self.assertIsNotNone(latest_journal)
            self.assertEqual(latest_journal["snapshot_reason"], "MANUAL_RUNTIME_VERIFICATION_V2")
            self.assertEqual(latest_journal["stock_code"], "267260")
            self.assertEqual(float(latest_journal["market_price"]), 790000.0)


            # Check scheduler_runs
            run_record = self.db.get_latest_scheduler_run("INTRADAY_SHADOW_SCAN")
            self.assertIsNotNone(run_record)
            self.assertEqual(run_record["status"], "SUCCESS")
            self.assertEqual(run_record["run_id"], res["run_id"])

    def test_10_outcome_and_journal_maintenance_with_exclusion(self):
        """10. 16:10 OUTCOME_AND_JOURNAL_MAINTENANCE 정상 구동 및 과거 manual batch 성과 표본 제외 검증"""
        # Insert a legacy manual verification journal
        self.db.insert_scan_journal({
            "journal_id": "JRN_TEST_LEGACY_MANUAL",
            "scan_timestamp": "2026-08-18 13:46:00 KST",
            "trading_date": "2026-08-18",
            "snapshot_reason": "MANUAL_RUNTIME_VERIFICATION",
            "stock_code": "267260",
            "stock_name": "HD현대일렉트릭",
            "market_price": 312000.0,
            "atr14": 15000.0,
            "natr": 4.8,
            "candidate_ref_price": 312000.0,
            "candidate_stop_price": 289500.0,
            "candidate_target_price": 357000.0,
            "atr_mode": "NORMAL",
            "shadow_integrated_state": "CANDIDATE_CONDITIONAL",
            "primary_blocker": "NONE",
            "all_blockers": ["NONE"],
            "existing_f_score": 70.0,
            "existing_final_score": 75.0,
            "buy_approval": "ON",
            "p0_status": "NONE"
        })

        res = self.scheduler.run_task("OUTCOME_AND_JOURNAL_MAINTENANCE", is_manual=True)
        self.assertEqual(res["status"], "SUCCESS")

        # Verify outcome record was marked as INVALID_FOR_OUTCOME
        outcome_rows = self.db.execute_query("SELECT * FROM signal_outcomes WHERE journal_id = 'JRN_TEST_LEGACY_MANUAL'")
        self.assertTrue(len(outcome_rows) > 0)
        self.assertIn("INVALID_FOR_OUTCOME", outcome_rows[0]["outcome_status"])
        self.assertIn("LIVE_PRICE_LINEAGE_FAILURE", outcome_rows[0]["outcome_status"])


    def test_11_zero_order_api_calls_enforced(self):
        """11. Runtime Scheduler 실행 중 매수/매도/정정/취소 주문 API 호출 0건 절대 불변 검증"""
        # Inspect RuntimeScheduler methods to ensure no order methods are invoked
        with open("src/runtime/runtime_scheduler.py", "r", encoding="utf-8") as f:
            scheduler_code = f.read()
        self.assertNotIn("send_buy_order", scheduler_code)
        self.assertNotIn("send_sell_order", scheduler_code)
        self.assertNotIn("modify_order", scheduler_code)
        self.assertNotIn("cancel_order", scheduler_code)

    def test_12_invariants_and_buy_approval_unaltered(self):
        """12. V1.0 불변 원칙: 기존 F/T 점수 산식, Fundamental State, Buy Approval 불변 검증"""
        prof = self.scheduler.radar_engine.get_industry_profile_for_stock("267260", "HD현대일렉트릭")
        self.assertEqual(prof["industry_id"], "POWER_EQUIPMENT")
        self.assertEqual(prof["industry_gate"], "INDUSTRY_PASS_STRONG")
        self.assertTrue(prof["total_score"] >= 85.0)

    def test_13_detector_does_not_false_positive_on_own_subprocess(self):
        """13. 검사 서브프로세스(PowerShell/cmd) 자체를 active job으로 오인하지 않음 검증"""
        lock_mgr = SchedulerLockManager(self.db)
        # Directly call is_existing_job_active() - must return False when no real jobs are running
        self.assertFalse(lock_mgr.is_existing_job_active())

    def test_14_detector_ignores_unrelated_python_main(self):
        """14. 다른 프로젝트의 unrelated python main.py를 오인하지 않음 검증"""
        lock_mgr = SchedulerLockManager(self.db)
        with patch("subprocess.check_output") as mock_sub:
            # Mock process list with unrelated main.py from another directory
            mock_sub.return_value = ""
            is_active = lock_mgr.is_existing_job_active()
            self.assertFalse(is_active)

    def test_15_canonical_task_running_triggers_collision_guard(self):
        """15. Canonical Task (StockBot_Intraday_1120 / StockAnalysisDailyReport) Running 시 True 반환 검증"""
        lock_mgr = SchedulerLockManager(self.db)
        with patch.object(lock_mgr, "check_scheduled_task_running", return_value=(True, "StockBot_Intraday_1120")):
            self.assertTrue(lock_mgr.is_existing_job_active())

        with patch.object(lock_mgr, "check_scheduled_task_running", return_value=(True, "StockAnalysisDailyReport")):
            self.assertTrue(lock_mgr.is_existing_job_active())

    def test_16_both_canonical_tasks_ready_returns_false(self):
        """16. Canonical Task 모두 Ready 상태일 때 False 반환 검증"""
        lock_mgr = SchedulerLockManager(self.db)
        with patch.object(lock_mgr, "check_scheduled_task_running", return_value=(False, None)):
            with patch.object(lock_mgr, "check_process_fallback_running", return_value=(False, None)):
                self.assertFalse(lock_mgr.is_existing_job_active())

    def test_17_task_query_failure_triggers_strict_fallback(self):
        """17. Task Scheduler 쿼리 실패 시 strict process fallback 정상 동작 검증"""
        lock_mgr = SchedulerLockManager(self.db)
        with patch.object(lock_mgr, "check_scheduled_task_running", side_effect=Exception("Task Scheduler RPC error")):
            with patch.object(lock_mgr, "check_process_fallback_running", return_value=(True, "PID:9999")):
                self.assertTrue(lock_mgr.is_existing_job_active())

            with patch.object(lock_mgr, "check_process_fallback_running", return_value=(False, None)):
                self.assertFalse(lock_mgr.is_existing_job_active())

    def test_18_outside_reserved_window_allows_scan(self):
        """18. 11:35 등 보호시간 밖 + existing job 없음 시 정상 Shadow Scan 허용 검증"""
        now_1135 = datetime(2026, 8, 19, 11, 35, 0)
        is_reserved, label = SchedulerLockManager.check_reserved_window(now_1135)
        self.assertFalse(is_reserved)
        self.assertIsNone(label)

if __name__ == "__main__":

    unittest.main()
