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

    def test_unmapped_stock_insert_scan_journal_real_sqlite(self):
        import gc
        import tempfile
        from pathlib import Path
        from src.core.dto import ScanResultDTO
        from src.database.db_manager import DatabaseManager
        from src.formatters.gems_formatter import render_gems_markdown
        from src.analysis.outcome_evaluator import OutcomeEvaluator

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_unmapped_journal.db"
            db = DatabaseManager(db_path=str(db_path))
            engine = IndustryRadarEngine(db)
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
            j_id = "JRN_TEST_UNMAPPED_999999"
            entry = {
                "journal_id": j_id,
                "scan_timestamp": "2026-10-19 13:10:00 KST",
                "trading_date": "2026-10-19",
                "snapshot_reason": "DAILY_FIRST",
                "stock_code": "999999",
                "stock_name": "UNMAPPED TEST",
                "market_price": 15000.0,
                "industry_run_id": profile["run_id"],
                "industry_score": profile["total_score"],
                "industry_gate": profile["industry_gate"],
                "industry_confidence": profile["industry_confidence"],
                "exposure_type": profile["exposure_type"],
                "mapping_version": profile["mapping_version"],
                "fundamental_state": "STABLE",
                "turnaround_type": "NONE",
                "turnaround_label": "안정 성장",
                "forward_opportunity": "MODERATE",
                "forward_confidence": "MEDIUM",
                "forward_risk": "NONE",
                "forward_risk_override_tag": "NONE",
                "book_to_bill_summary": "N/A",
                "t_score": None,
                "technical_state": "NEUTRAL",
                "technical_action": "BUY_WAIT",
                "intraday_data_quality": "VALID",
                "atr14": None,
                "natr": None,
                "candidate_ref_price": None,
                "candidate_stop_price": None,
                "candidate_target_price": None,
                "atr_mode": "NORMAL",
                "shadow_integrated_state": shadow["shadow_integrated_state"],
                "primary_blocker": shadow["primary_blocker"],
                "all_blockers": shadow["all_blockers"],
                "existing_f_score": None,
                "existing_final_score": None,
                "buy_approval": "🔴 OFF (진입 불가/조건 미충족)",
                "p0_status": "NONE",
                "position_cycle_id": "NONE",
            }
            self.assertTrue(db.insert_scan_journal(entry))
            saved = db.get_scan_journal(j_id)
            self.assertIsNotNone(saved)
            self.assertIsNone(saved["industry_score"])
            self.assertEqual(saved["industry_gate"], "INDUSTRY_UNRATED")
            self.assertEqual(saved["shadow_integrated_state"], "INDUSTRY_UNRATED")

            outcome = OutcomeEvaluator(db).evaluate_journal_outcome(j_id)
            self.assertIsNotNone(outcome)

            dto = ScanResultDTO(
                stock_code="999999",
                stock_name="UNMAPPED TEST",
                collected_at="2026-10-19 13:10:00 KST",
                industry_profile=profile["industry_id"],
                industry_score=profile["total_score"],
                industry_gate=profile["industry_gate"],
                industry_bucket=profile["industry_bucket"],
            )
            md = render_gems_markdown(dto)
            self.assertIn("Score: UNRATED", md)

            del db
            gc.collect()


if __name__ == "__main__":
    unittest.main()
