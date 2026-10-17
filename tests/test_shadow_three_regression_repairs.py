import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from cloud_runner import CloudRunner
from src.analysis.industry_radar_engine import IndustryRadarEngine
from src.analysis.sell_warning_45m_engine import SellWarning45mEngine


class ShadowThreeRegressionRepairsTest(unittest.TestCase):
    def test_postmarket_runs_outcome_maintenance_fail_isolated(self):
        runner = CloudRunner.__new__(CloudRunner)
        runner.report_mode = "POSTMARKET"
        runner.force = False
        db = MagicMock()
        scheduler = MagicMock()
        scheduler._execute_outcome_maintenance.return_value = {
            "status": "SUCCESS",
            "total_journals": 12,
            "outcomes_evaluated": 12,
        }
        with patch("src.runtime.runtime_scheduler.RuntimeScheduler", return_value=scheduler):
            result = runner._run_postmarket_outcome_maintenance(db)
        self.assertEqual(result["status"], "SUCCESS")
        scheduler._execute_outcome_maintenance.assert_called_once_with(is_manual=False)

    def test_non_postmarket_does_not_run_outcome_maintenance(self):
        runner = CloudRunner.__new__(CloudRunner)
        runner.report_mode = "INTRADAY"
        runner.force = False
        with patch("src.runtime.runtime_scheduler.RuntimeScheduler") as scheduler_cls:
            self.assertIsNone(runner._run_postmarket_outcome_maintenance(MagicMock()))
        scheduler_cls.assert_not_called()

    def test_unmapped_industry_is_unrated_not_general_manufacturing(self):
        engine = IndustryRadarEngine.__new__(IndustryRadarEngine)
        engine.db = MagicMock()
        engine.db.get_company_industry_mapping.return_value = None
        profile = engine.get_industry_profile_for_stock("999999", "UNMAPPED TEST")
        self.assertEqual(profile["industry_id"], "UNMAPPED")
        self.assertEqual(profile["industry_gate"], "INDUSTRY_UNRATED")
        self.assertIsNone(profile["total_score"])
        shadow = engine.synthesize_shadow_state(
            industry_gate=profile["industry_gate"],
            industry_score=profile["total_score"],
            exposure_type=profile["exposure_type"],
            fundamental_state="STABLE",
            f_score=70.0,
            forward_opp_state="MODERATE",
            forward_risk_state="NONE",
            technical_action="BUY_WAIT",
        )
        self.assertEqual(shadow["primary_blocker"], "NONE")
        self.assertEqual(shadow["shadow_integrated_state"], "INDUSTRY_UNRATED")

    def test_sell_warning_requests_sparse_completed_45m(self):
        analyzer = MagicMock()
        analyzer.fetch_canonical_15m_data.return_value = (
            pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1.0]}),
            "TEST",
            "NONE",
        )
        engine = SellWarning45mEngine(intraday_analyzer=analyzer)
        with patch(
            "src.analysis.sell_warning_45m_engine.prepare_completed_45m_data",
            return_value=(None, "MALFORMED_INTERVAL"),
        ) as prepare:
            result = engine.evaluate_stock_warning(
                "005930",
                "2026-10-16",
                "2026-10-16 15:00:00",
            )
        self.assertEqual(result["reason_codes"], "MALFORMED_INTERVAL")
        self.assertTrue(prepare.call_args.kwargs["allow_sparse_completed"])


if __name__ == "__main__":
    unittest.main()
