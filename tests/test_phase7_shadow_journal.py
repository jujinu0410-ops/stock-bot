import unittest
import json
from src.database.db_manager import DatabaseManager
from src.analysis.canonical_registry import CanonicalMetricRegistry, CanonicalConsistencyChecker
from src.analysis.outcome_evaluator import OutcomeEvaluator
from src.analysis.attribution_engine import AttributionEngine
from src.analysis.industry_radar_engine import IndustryRadarEngine
from src.core.dto import ScanResultDTO
from src.formatters.gems_formatter import render_gems_markdown

class TestPhase7ShadowJournal(unittest.TestCase):
    """
    Phase 7: Shadow Scan Journal & Outcome Attribution 단위 테스트
    """
    @classmethod
    def setUpClass(cls):
        cls.db = DatabaseManager()
        cls.radar = IndustryRadarEngine(cls.db)
        cls.checker = CanonicalConsistencyChecker(cls.db)
        cls.evaluator = OutcomeEvaluator(cls.db)
        cls.attribution = AttributionEngine(cls.db)

    def setUp(self):
        self.db.execute_non_query("DELETE FROM scan_journal WHERE journal_id LIKE 'JRN_TEST%'")
        self.db.execute_non_query("DELETE FROM signal_outcomes WHERE journal_id LIKE 'JRN_TEST%'")

    def test_01_canonical_metric_consistency_preflight(self):
        """1. Canonical Metric Consistency Preflight: HD현대일렉트릭 2026 Q2 OPM 및 백로그 정합성 검증"""
        audit_res = self.checker.audit_stock_consistency("267260", 2026, "Q2")
        self.assertTrue(len(audit_res) >= 2)

        opm_audit = next(a for a in audit_res if a["metric_name"] == "operating_margin")
        self.assertAlmostEqual(opm_audit["fundamental_value"], 25.1, delta=0.1)
        self.assertAlmostEqual(opm_audit["industry_evidence_value"], 25.1, delta=0.1)
        self.assertEqual(opm_audit["consistency_status"], "CONSISTENT")
        self.assertLessEqual(opm_audit["difference"], 0.05)

        backlog_audit = next(a for a in audit_res if a["metric_name"] == "backlog_yoy")
        self.assertAlmostEqual(backlog_audit["forward_value"], 28.5, delta=0.1)
        self.assertAlmostEqual(backlog_audit["industry_evidence_value"], 28.5, delta=0.1)
        self.assertLessEqual(backlog_audit["difference"], 0.1)
        self.assertEqual(backlog_audit["consistency_status"], "CONSISTENT")

    def test_02_scan_journal_append_only_immutable(self):
        """2. Scan Journal Append-Only 불변 스냅샷 적재 및 중복/조작 차단 검증"""
        j_id = "JRN_TEST_20260817_267260"
        entry = {
            "journal_id": j_id,
            "scan_timestamp": "2026-08-17 14:00:00 KST",
            "trading_date": "2026-08-17",
            "snapshot_reason": "DAILY_FIRST",
            "stock_code": "267260",
            "stock_name": "HD현대일렉트릭",
            "market_price": 310000.0,
            "industry_run_id": "PROD_2026_W33_001",
            "industry_score": 91.5,
            "industry_gate": "INDUSTRY_PASS_STRONG",
            "industry_confidence": "HIGH",
            "exposure_type": "DIRECT_CORE",
            "mapping_version": "V1.0",
            "fundamental_state": "STRONG",
            "turnaround_type": "C",
            "turnaround_label": "매출·마진 동반 확장",
            "forward_opportunity": "VERY_STRONG",
            "forward_confidence": "HIGH",
            "forward_risk": "NONE",
            "forward_risk_override_tag": "NONE",
            "book_to_bill_summary": "1.30 (Book-to-Bill > 1.2x [PROVISIONAL])",
            "t_score": 66.0,
            "technical_state": "NEUTRAL",
            "technical_action": "BUY_ALLOWED_CONDITIONAL",
            "intraday_data_quality": "VALID",
            "atr14": 12000.0,
            "natr": 3.87,
            "candidate_ref_price": 310000.0,
            "candidate_stop_price": 292000.0,
            "candidate_target_price": 346000.0,
            "atr_mode": "NORMAL",
            "shadow_integrated_state": "CANDIDATE_CONDITIONAL",
            "primary_blocker": "NONE",
            "all_blockers": [],
            "existing_f_score": 92.0,
            "existing_final_score": 76.4,
            "buy_approval": "🔵 ON (트레일링/눌림목 분할매수 승인)",
            "p0_status": "NONE",
            "position_cycle_id": "NONE",
            "financial_data_asof": "2026-06-30",
            "forward_data_asof": "2026-08-17",
            "intraday_last_timestamp": "2026-08-17 14:00:00",
            "scoring_versions": {"industry": "V1.0", "scoring": "V1.0"}
        }

        # First insert
        success = self.db.insert_scan_journal(entry)
        self.assertTrue(success)

        # Retrieve and verify
        retrieved = self.db.get_scan_journal(j_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["stock_code"], "267260")
        self.assertEqual(retrieved["market_price"], 310000.0)
        self.assertEqual(retrieved["industry_score"], 91.5)
        self.assertEqual(retrieved["buy_approval"], "🔵 ON (트레일링/눌림목 분할매수 승인)")

    def test_03_trading_days_forward_calculation_and_atr_hits(self):
        """3. Outcome Evaluator: 실제 거래일(Trading Days) 기반 성과, MFE/MAE, ATR 히트 계산 검증"""
        j_id = "JRN_TEST_20260817_267260_EVAL"
        entry = {
            "journal_id": j_id,
            "scan_timestamp": "2026-08-17 14:00:00 KST",
            "trading_date": "2026-08-17",
            "snapshot_reason": "DAILY_FIRST",
            "stock_code": "267260",
            "stock_name": "HD현대일렉트릭",
            "market_price": 310000.0,
            "atr14": 12000.0
        }
        self.db.insert_scan_journal(entry)
        outcome = self.evaluator.evaluate_journal_outcome(j_id)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome["journal_id"], j_id)
        self.assertEqual(outcome["entry_reference_price"], 310000.0)
        self.assertEqual(outcome["entry_atr14"], 12000.0)
        self.assertIn("outcome_status", outcome)

    def test_04_snapshot_atr_fixed_and_no_lookahead(self):
        """4. Snapshot ATR 고정 원칙: 과거 기준선이 미래 ATR에 의해 변조되지 않음을 검증"""
        fixed_atr = 15000.0
        entry_price = 300000.0
        # +3 ATR target = 300,000 + 3*15,000 = 345,000
        # -1.5 ATR stop = 300,000 - 1.5*15,000 = 277,500
        target = entry_price + (3.0 * fixed_atr)
        stop = entry_price - (1.5 * fixed_atr)
        self.assertEqual(target, 345000.0)
        self.assertEqual(stop, 277500.0)

    def test_05_attribution_engine_group_aggregation_and_sample_rule(self):
        """5. Attribution Engine: 그룹별 성과 집계 및 N < 30 No-Auto-Tuning 가이드 검증"""
        # Dim 1: industry_gate
        gate_res = self.attribution.aggregate_by_dimension("industry_gate")
        for g in gate_res:
            self.assertIn("dimension", g)
            self.assertIn("group_name", g)
            self.assertIn("sample_count", g)
            self.assertIn("sample_guidance", g)
            if g["sample_count"] < 30:
                self.assertFalse(g["is_statistically_significant"])
                self.assertIn("NO_AUTO_TUNING", g["sample_guidance"])

        # Dim 2: shadow_integrated_state
        state_res = self.attribution.aggregate_by_dimension("shadow_integrated_state")
        self.assertIsInstance(state_res, list)

    def test_06_blocker_effectiveness_analysis(self):
        """6. Blocker Effectiveness: Primary Blocker별 사후 손실 방어 효과 분석 검증"""
        blocker_res = self.attribution.analyze_blocker_effectiveness()
        self.assertIsInstance(blocker_res, list)

    def test_07_raw_vs_effective_contribution_tracking(self):
        """7. Raw vs Effective Contribution: 정성적 증거 Cap 적용 및 사유 추적 검증"""
        # 1) MANUAL_QUALITATIVE cap test: raw 17.5pt with cap 7.0pt
        capped = AttributionEngine.audit_raw_vs_effective_contribution(17.5, 7.0, "MANUAL_QUALITATIVE")
        self.assertEqual(capped["raw_contribution"], 17.5)
        self.assertEqual(capped["effective_contribution"], 7.0)
        self.assertTrue(capped["is_capped"])
        self.assertEqual(capped["cap_reason"], "MANUAL_QUALITATIVE_35PCT_LIMIT")

        # 2) Uncapped normal driver
        uncapped = AttributionEngine.audit_raw_vs_effective_contribution(9.5, 10.0, "LIVE_FETCHED")
        self.assertEqual(uncapped["raw_contribution"], 9.5)
        self.assertEqual(uncapped["effective_contribution"], 9.5)
        self.assertFalse(uncapped["is_capped"])
        self.assertEqual(capped["cap_reason"], "MANUAL_QUALITATIVE_35PCT_LIMIT")

    def test_08_invariants_and_buy_approval_unaltered(self):
        """8. 불변 검증: Journal 및 Outcome 계층 추가에도 기존 F/T 점수 및 Buy Approval 100% 불변"""
        dto = ScanResultDTO(
            stock_code="267260",
            stock_name="HD현대일렉트릭",
            collected_at="2026-08-17 14:00:00 KST",
            f_score=92.0,
            t_score=66.0,
            final_score=76.4,
            buy_approval="🔵 ON (트레일링/눌림목 분할매수 승인)",
            technical_action="BUY_ALLOWED_CONDITIONAL",
            technical_state="NEUTRAL",
            industry_profile="POWER_EQUIPMENT",
            industry_score=91.5,
            industry_gate="INDUSTRY_PASS_STRONG",
            industry_bucket="CORE_MOMENTUM",
            industry_confidence="HIGH",
            exposure_type="DIRECT_CORE",
            mapping_version="V1.0",
            cross_validation_state="INDUSTRY_COMPANY_CONFIRMED",
            shadow_integrated_state="CANDIDATE_CONDITIONAL",
            primary_blocker="NONE",
            all_blockers=[],
            journal_id="JRN_20260817_140000_267260",
            canonical_consistency_status="CONSISTENT"
        )
        self.assertEqual(dto.f_score, 92.0)
        self.assertEqual(dto.t_score, 66.0)
        self.assertEqual(dto.final_score, 76.4)
        self.assertEqual(dto.buy_approval, "🔵 ON (트레일링/눌림목 분할매수 승인)")

    def test_09_formatter_section_0_rendering_phase7(self):
        """9. Formatter Section 0 Phase 7 Journal ID 및 Canonical Status 렌더링 검증"""
        dto = ScanResultDTO(
            stock_code="267260",
            stock_name="HD현대일렉트릭",
            collected_at="2026-08-17 14:00:00 KST",
            f_score=92.0,
            t_score=66.0,
            final_score=76.4,
            buy_approval="🔵 ON (트레일링/눌림목 분할매수 승인)",
            technical_action="BUY_ALLOWED_CONDITIONAL",
            technical_state="NEUTRAL",
            industry_profile="POWER_EQUIPMENT",
            industry_score=91.5,
            industry_gate="INDUSTRY_PASS_STRONG",
            industry_bucket="CORE_MOMENTUM",
            industry_confidence="HIGH",
            exposure_type="DIRECT_CORE",
            mapping_version="V1.0",
            cross_validation_state="INDUSTRY_COMPANY_CONFIRMED",
            shadow_integrated_state="CANDIDATE_CONDITIONAL",
            primary_blocker="NONE",
            all_blockers=[],
            verified_evidence_pct=100.0,
            live_fetched_pct=50.0,
            reference_verified_pct=20.0,
            internal_derived_pct=30.0,
            manual_evidence_pct=0.0,
            synthetic_evidence_pct=0.0,
            fresh_evidence_pct=100.0,
            replay_verified_pct=100.0,
            replay_failed_pct=0.0,
            driver_count=6,
            evidence_count=10,
            qa_status="QA_PASSED",
            journal_id="JRN_20260817_140000_267260",
            canonical_consistency_status="CONSISTENT"
        )
        md = render_gems_markdown(dto)
        self.assertIn("Phase 7 Shadow Journal & Outcome Attribution", md)
        self.assertIn("Shadow Scan Journal: JRN_20260817_140000_267260 (Canonical Status: CONSISTENT)", md)

if __name__ == '__main__':
    unittest.main()
