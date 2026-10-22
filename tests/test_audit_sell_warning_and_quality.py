"""Audit verification tests for SELL monitoring, completed bar resolution, and Policy Shadow data quality."""

import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
import pandas as pd

os.environ["STOCKBOT_TEST_MODE"] = "1"

from src.policy.policy_shadow_observer import observe_report_policy_shadow
from src.runtime.runtime_scheduler import RuntimeScheduler
from src.analysis.sell_warning_45m_engine import SellWarning45mEngine


class TestAuditSellWarningAndQuality(unittest.TestCase):
    def test_policy_shadow_preserves_zero_validity_flag(self):
        """Verify that data_validity_flag=0 is strictly preserved as 0, NOT converted to 1."""
        mock_db = MagicMock()
        mock_store = MagicMock()
        held = [{
            "stock_code": "005930",
            "quantity": 100,
            "avg_buy_price": 70000.0,
            "market_price": 71000.0,
            "data_validity_flag": 0,
            "trade_mode": "ACTIVE",
            "completed_45m_timestamp": "2026-10-22 15:00:00",
        }]
        with patch("src.policy.policy_shadow_observer.PolicyShadowService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.observe.return_value = {"action": "HOLD"}
            mock_svc_cls.return_value = mock_svc

            observe_report_policy_shadow(mock_db, held, "RUN_TEST", asof_dt=datetime(2026, 10, 22, 15, 5), policy_store=mock_store)
            call_raw = mock_svc.observe.call_args[0][0]
            self.assertEqual(call_raw["data_validity"], 0, "Explicit 0 must NOT become 1")

    def test_policy_shadow_preserves_one_and_none_validity_flag(self):
        """Verify that data_validity_flag=1 is 1, and missing/None is None."""
        mock_db = MagicMock()
        mock_store = MagicMock()
        with patch("src.policy.policy_shadow_observer.PolicyShadowService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.observe.return_value = {"action": "HOLD"}
            mock_svc_cls.return_value = mock_svc

            held_valid = [{
                "stock_code": "005930",
                "quantity": 100,
                "avg_buy_price": 70000.0,
                "market_price": 71000.0,
                "data_validity_flag": 1,
                "trade_mode": "ACTIVE",
                "completed_45m_timestamp": "2026-10-22 15:00:00",
            }]
            observe_report_policy_shadow(mock_db, held_valid, "RUN_TEST_V", asof_dt=datetime(2026, 10, 22, 15, 5), policy_store=mock_store)
            call_raw_v = mock_svc.observe.call_args[0][0]
            self.assertEqual(call_raw_v["data_validity"], 1)

            held_none = [{
                "stock_code": "005930",
                "quantity": 100,
                "avg_buy_price": 70000.0,
                "market_price": 71000.0,
                "trade_mode": "ACTIVE",
                "completed_45m_timestamp": "2026-10-22 15:00:00",
            }]
            observe_report_policy_shadow(mock_db, held_none, "RUN_TEST_N", asof_dt=datetime(2026, 10, 22, 15, 5), policy_store=mock_store)
            call_raw_n = mock_svc.observe.call_args[0][0]
            self.assertIsNone(call_raw_n["data_validity"])

    def test_policy_shadow_filters_unaligned_completed_timestamp(self):
        """Execution timestamps like 11:20 or 15:02 must NOT be accepted as completed 45m bar ts."""
        mock_db = MagicMock()
        mock_store = MagicMock()
        with patch("src.policy.policy_shadow_observer.PolicyShadowService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.observe.return_value = {"action": "HOLD"}
            mock_svc_cls.return_value = mock_svc

            held = [{
                "stock_code": "005930",
                "quantity": 100,
                "avg_buy_price": 70000.0,
                "market_price": 71000.0,
                "data_validity_flag": 1,
                "trade_mode": "ACTIVE",
                "completed_45m_timestamp": "2026-10-22 11:20:00",
            }]
            observe_report_policy_shadow(mock_db, held, "RUN_TEST_UNALIGNED", asof_dt=datetime(2026, 10, 22, 11, 20, 5), policy_store=mock_store)
            call_raw_unaligned = mock_svc.observe.call_args[0][0]
            self.assertIsNone(call_raw_unaligned["completed_45m_timestamp"], "Unaligned 11:20:00 must be rejected")

            # Canonical bar end (11:15:00) must be accepted
            held[0]["completed_45m_timestamp"] = "2026-10-22 11:15:00"
            observe_report_policy_shadow(mock_db, held, "RUN_TEST_ALIGNED", asof_dt=datetime(2026, 10, 22, 11, 20, 5), policy_store=mock_store)
            call_raw_aligned = mock_svc.observe.call_args[0][0]
            self.assertEqual(call_raw_aligned["completed_45m_timestamp"], "2026-10-22 11:15:00")

    def test_runtime_scheduler_propagates_intraday_data_quality(self):
        """RuntimeScheduler must propagate the actual engine data quality instead of hardcoding VALID."""
        mock_db = MagicMock()
        mock_db.get_active_industry_run.return_value = {"run_id": "RUN_001"}
        mock_db.get_industry_company_score.return_value = {
            "run_id": "RUN_001", "total_score": 75.0, "industry_gate": "OPEN",
            "industry_confidence": 0.8, "exposure_type": "CORE", "mapping_version": "V1.0"
        }
        mock_db.get_fundamental_evidence.return_value = None
        mock_db.get_forward_visibility_evidence.return_value = None
        mock_db.get_latest_kiwoom_daily.return_value = {"close_price": 70000.0, "atr14": 1500.0, "natr": 2.1}

        with patch("src.runtime.runtime_scheduler.TradingEngine") as mock_te_cls, \
             patch("src.runtime.runtime_scheduler.IndustryRadarEngine") as mock_radar_cls, \
             patch("src.runtime.runtime_scheduler.MarketQuoteClient"), \
             patch("src.runtime.runtime_scheduler.SchedulerLockManager"):
            
            mock_radar = MagicMock()
            mock_radar_cls.return_value = mock_radar
            mock_radar.synthesize_shadow_state.return_value = {
                "shadow_integrated_state": "NEUTRAL",
                "primary_blocker": "NONE",
                "all_blockers": []
            }

            scheduler = RuntimeScheduler(db_manager=mock_db)
            mock_te = mock_te_cls.return_value

            # Case A: Engine returns INVALID quality
            mock_te.analyze_stock.return_value = {
                "t_score": 50.0,
                "technical_state": "BEARISH",
                "technical_action": "SELL_WAIT",
                "intraday_quality": "🔴 INVALID (분봉 데이터 미수집)",
            }
            res_a = scheduler._evaluate_stock_shadow("005930", "삼성전자", "2026-10-22", "2026-10-22 15:00:00")
            self.assertEqual(res_a["intraday_data_quality"], "🔴 INVALID (분봉 데이터 미수집)")

            # Case B: Engine analysis fails / None
            mock_te.analyze_stock.return_value = None
            res_b = scheduler._evaluate_stock_shadow("005930", "삼성전자", "2026-10-22", "2026-10-22 15:00:00")
            self.assertEqual(res_b["intraday_data_quality"], "UNKNOWN")

    def test_sidecar_guards_against_none_and_unaligned_bar_ts(self):
        """Sidecar evaluators must cleanly skip execution when completed_bar_ts is None or unaligned."""
        mock_db = MagicMock()
        with patch("src.runtime.runtime_scheduler.TradingEngine"), \
             patch("src.runtime.runtime_scheduler.IndustryRadarEngine"), \
             patch("src.runtime.runtime_scheduler.MarketQuoteClient"), \
             patch("src.runtime.runtime_scheduler.SchedulerLockManager"):
            scheduler = RuntimeScheduler(db_manager=mock_db)
            scheduler._sell_warning_engine = MagicMock()
            scheduler._add_advisory_engine = MagicMock()

            # Test None
            scheduler._evaluate_add_advisory_sidecar("005930", {}, "2026-10-22", None)
            scheduler._evaluate_sell_warning_sidecar("005930", {}, "2026-10-22", None)
            scheduler._add_advisory_engine.evaluate_stock_advisory.assert_not_called()
            scheduler._sell_warning_engine.evaluate_stock_warning.assert_not_called()

            # Test unaligned ts (14:50:00)
            scheduler._evaluate_add_advisory_sidecar("005930", {}, "2026-10-22", "2026-10-22 14:50:00")
            scheduler._evaluate_sell_warning_sidecar("005930", {}, "2026-10-22", "2026-10-22 14:50:00")
            scheduler._add_advisory_engine.evaluate_stock_advisory.assert_not_called()
            scheduler._sell_warning_engine.evaluate_stock_warning.assert_not_called()

    def test_sell_warning_diagnoses_cutoff_mismatch_feed_delay(self):
        """When 15m data lags behind cutoff, CUTOFF_MISMATCH reason code must detail data feed delay."""
        engine = SellWarning45mEngine(intraday_analyzer=MagicMock())
        # Use TEST_15M_BAR_START which is recognized by _source_interval_minutes
        idx = pd.date_range("2026-10-22 09:00", "2026-10-22 14:30", freq="15min")
        mock_df = pd.DataFrame({"Open": 100.0, "High": 105.0, "Low": 95.0, "Close": 100.0, "Volume": 1000.0}, index=idx)
        engine.intraday_analyzer.fetch_canonical_15m_data.return_value = (mock_df, "TEST_15M_BAR_START", "NONE")

        result = engine.evaluate_stock_warning("005930", "2026-10-22", "2026-10-22 15:00:00")
        self.assertIn("CUTOFF_MISMATCH:DATA_FEED_DELAY", result["reason_codes"])
        self.assertIn("raw_last=2026-10-22 14:30:00", result["reason_codes"])
        self.assertIn("cutoff=2026-10-22 15:00:00", result["reason_codes"])
        self.assertEqual(result["data_quality"], "INVALID (CUTOFF_MISMATCH)")


if __name__ == "__main__":
    unittest.main()
