import unittest
import os
from src.database.db_manager import DatabaseManager
from src.analysis.industry_radar_engine import IndustryRadarEngine
from src.core.dto import ScanResultDTO
from src.formatters.gems_formatter import render_gems_markdown

class TestPhase6IndustryRadar(unittest.TestCase):
    """
    Phase 6, 6.1 & 6.2: Weekly Industry Radar Integration, Evidence Authenticity & Scoring Integrity 단위 테스트 스위트
    """
    @classmethod
    def setUpClass(cls):
        cls.db_path = "test_phase6_2.db"
        if os.path.exists(cls.db_path):
            os.remove(cls.db_path)
        cls.db = DatabaseManager(cls.db_path)
        cls.engine = IndustryRadarEngine(cls.db)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except Exception:
                pass

    def test_01_fixture_run_not_selected_as_production(self):
        """1. TEST_FIXTURE run이 Production latest run으로 선택되지 않음을 물리적 분리 검증"""
        self.db.upsert_industry_score({
            "run_id": "FIXTURE_2026_SEED",
            "industry_id": "POWER_EQUIPMENT",
            "industry_name": "AI 전력망",
            "total_score": 99.0,
            "policy_continuity": 20.0,
            "earnings_linkage": 25.0,
            "visibility_order_revenue": 20.0,
            "catalysts_score": 15.0,
            "valuation_burden": 10.0,
            "downside_risk_score": 9.0,
            "industry_bucket": "CORE_MOMENTUM",
            "industry_gate": "INDUSTRY_PASS_STRONG",
            "industry_confidence": "HIGH"
        })
        prod_score = self.db.get_latest_industry_score("POWER_EQUIPMENT", run_type="PRODUCTION")
        self.assertIsNotNone(prod_score)
        self.assertEqual(prod_score["run_type"], "PRODUCTION")
        self.assertNotEqual(prod_score["total_score"], 99.0)

    def test_02_production_score_matches_factor_sum(self):
        """2. Production score가 6개 Factor 합과 정확히 일치함을 검증"""
        prod_score = self.db.get_latest_industry_score("POWER_EQUIPMENT", run_type="PRODUCTION")
        factor_sum = round(
            prod_score["policy_continuity"] +
            prod_score["earnings_linkage"] +
            prod_score["visibility_order_revenue"] +
            prod_score["catalysts_score"] +
            prod_score["valuation_burden"] +
            prod_score["downside_risk_score"], 1
        )
        self.assertEqual(prod_score["total_score"], factor_sum)

    def test_03_missing_evidence_does_not_invent_score(self):
        """3. Evidence 없는 Factor가 임의 점수를 생성하지 않고 0.0 처리 및 Confidence LOW 강등 검증"""
        partial_factors = {
            "policy_continuity": [{
                "family": "POL", "driver_id": "DRV_P1", "origin_type": "LIVE_COLLECTED",
                "src": "GOV", "ref": "REF1", "val": 15.0, "cap": 20.0, "dir": "POSITIVE", "rel": "HIGH", "fresh": 10, "desc": "정책"
            }],
            "earnings_linkage": [{
                "family": "MAR", "driver_id": "DRV_M1", "origin_type": "DERIVED_FROM_INTERNAL_DATA",
                "src": "DART", "ref": "REF2", "val": 20.0, "cap": 25.0, "dir": "POSITIVE", "rel": "HIGH", "fresh": 10, "desc": "마진"
            }]
            # 나머지 4개 Factor 결측
        }
        res = self.engine.calculate_and_save_production_industry(
            run_id="PROD_TEST_INSUFFICIENT",
            industry_id="TEST_PARTIAL_IND",
            industry_name="테스트 불완전 산업",
            thesis="데이터 불완전",
            factors_dict=partial_factors
        )
        self.assertEqual(res["visibility_order_revenue"], 0.0)
        self.assertEqual(res["catalysts_score"], 0.0)
        self.assertEqual(res["total_score"], 35.0)
        self.assertEqual(res["industry_confidence"], "LOW")
        self.assertEqual(res["industry_gate"], "INDUSTRY_BLOCK")
        self.assertEqual(res["qa_status"], "QA_FAILED")

    def test_04_stale_evidence_ratio_and_confidence(self):
        """4. Stale Evidence 발생 시 Freshness Ratio 기반 Confidence 제어 및 Strong Pass 강등 검증"""
        stale_factors = {
            "policy_continuity": [{"family": "P", "driver_id": "D1", "origin_type": "LIVE_COLLECTED", "src": "S1", "ref": "R1", "val": 18.0, "cap": 20.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2025-01-01", "desc": "구보고서"}],
            "earnings_linkage": [{"family": "E", "driver_id": "D2", "origin_type": "LIVE_COLLECTED", "src": "S2", "ref": "R2", "val": 22.0, "cap": 25.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2025-01-01", "desc": "구재무"}],
            "visibility_order_revenue": [{"family": "V", "driver_id": "D3", "origin_type": "LIVE_COLLECTED", "src": "S3", "ref": "R3", "val": 18.0, "cap": 20.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2025-01-01", "desc": "구백로그"}],
            "catalysts_score": [{"family": "C", "driver_id": "D4", "origin_type": "LIVE_COLLECTED", "src": "S4", "ref": "R4", "val": 13.0, "cap": 15.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2025-01-01", "desc": "구촉매"}],
            "valuation_burden": [{"family": "B", "driver_id": "D5", "origin_type": "LIVE_COLLECTED", "src": "S5", "ref": "R5", "val": 8.0, "cap": 10.0, "dir": "NEUTRAL", "rel": "HIGH", "pub_date": "2025-01-01", "desc": "구밸류"}],
            "downside_risk_score": [{"family": "R", "driver_id": "D6", "origin_type": "LIVE_COLLECTED", "src": "S6", "ref": "R6", "val": 8.0, "cap": 10.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2025-01-01", "desc": "구위험"}]
        }
        res = self.engine.calculate_and_save_production_industry(
            run_id="PROD_TEST_STALE",
            industry_id="TEST_STALE_IND",
            industry_name="테스트 구데이터 산업",
            thesis="신선도 경과",
            factors_dict=stale_factors,
            as_of_date="2026-08-17"
        )
        self.assertEqual(res["total_score"], 87.0)
        self.assertEqual(res["fresh_evidence_pct"], 0.0)
        self.assertEqual(res["industry_confidence"], "LOW")
        self.assertEqual(res["industry_gate"], "INDUSTRY_PASS")

    def test_05_historical_company_mapping_reproduction(self):
        """5. Historical Company Mapping (시점별 이력 및 버전) 재현 검증"""
        self.db.upsert_industry_company_map({
            "industry_id": "GENERAL_MANUFACTURING",
            "stock_code": "267260",
            "stock_name": "HD현대일렉트릭",
            "exposure_type": "INDIRECT",
            "evidence_rationale": "과거 2024년 구버전 간접 노출 매핑",
            "valid_from": "2024-01-01",
            "valid_to": "2025-12-31",
            "mapping_version": "V0.9",
            "is_active": False
        })
        curr_map = self.engine.get_industry_profile_for_stock("267260")
        self.assertEqual(curr_map["industry_id"], "POWER_EQUIPMENT")
        self.assertEqual(curr_map["exposure_type"], "DIRECT_CORE")
        self.assertEqual(curr_map["mapping_version"], "V1.0")

        hist_map = self.engine.get_industry_profile_for_stock("267260", as_of_date="2024-06-30")
        self.assertEqual(hist_map["industry_id"], "GENERAL_MANUFACTURING")
        self.assertEqual(hist_map["exposure_type"], "INDIRECT")
        self.assertEqual(hist_map["mapping_version"], "V0.9")

    def test_06_multiple_blockers_preservation(self):
        """6. 다중 차단 사유(Multiple Blockers) 보존 및 Primary Blocker 분리 검증 (예: 대동)"""
        shadow_res = self.engine.synthesize_shadow_state(
            industry_gate="INDUSTRY_BLOCK",
            industry_score=52.0,
            exposure_type="DIRECT_CORE",
            fundamental_state="STABLE",
            f_score=56.0,
            forward_opp_state="MODERATE",
            forward_risk_state="HIGH",
            technical_action="BUY_BLOCKED"
        )
        self.assertEqual(shadow_res["shadow_integrated_state"], "BLOCKED_BY_INDUSTRY")
        self.assertEqual(shadow_res["primary_blocker"], "BLOCKED_BY_INDUSTRY")
        self.assertEqual(len(shadow_res["all_blockers"]), 3)
        self.assertIn("BLOCKED_BY_INDUSTRY", shadow_res["all_blockers"])
        self.assertIn("BLOCKED_BY_FORWARD_RISK", shadow_res["all_blockers"])
        self.assertIn("BLOCKED_BY_TECHNICAL", shadow_res["all_blockers"])

    def test_07_theme_only_exclusion_and_primary_blocker(self):
        """7. THEME_ONLY 노출 기업 -> 후보군 제외 및 BLOCKED_BY_INDUSTRY (THEME_ONLY) 차단 검증"""
        shadow_res = self.engine.synthesize_shadow_state(
            industry_gate="INDUSTRY_PASS_STRONG",
            industry_score=92.0,
            exposure_type="THEME_ONLY",
            fundamental_state="STRONG",
            f_score=85.0,
            forward_opp_state="VERY_STRONG",
            forward_risk_state="NONE",
            technical_action="BUY_ALLOWED"
        )
        self.assertEqual(shadow_res["shadow_integrated_state"], "BLOCKED_BY_INDUSTRY")
        self.assertEqual(shadow_res["primary_blocker"], "BLOCKED_BY_INDUSTRY")
        self.assertIn("BLOCKED_BY_INDUSTRY (THEME_ONLY)", shadow_res["all_blockers"])

    def test_08_driver_duplication_cap_prevention(self):
        """8. Driver 중복 방지: 동일 Driver ID를 가진 여러 뉴스/리포트가 무한정 점수를 팽창시키지 않고 cap에 제한됨을 검증"""
        # 동일 driver_id (DRV_AI_POWER)에 10개의 3.0점 기사가 등록되었으나 cap=10.0으로 제한
        duplicate_articles = [
            {
                "family": "AI_POWER", "driver_id": "DRV_AI_POWER", "origin_type": "LIVE_COLLECTED",
                "src": f"NEWS_SRC_{i}", "ref": f"REF_{i}", "val": 3.0, "cap": 10.0,
                "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": f"기사 {i}"
            } for i in range(10)
        ]
        factors = {
            "policy_continuity": duplicate_articles,
            "earnings_linkage": duplicate_articles[:2],
            "visibility_order_revenue": duplicate_articles[:2],
            "catalysts_score": duplicate_articles[:2],
            "valuation_burden": duplicate_articles[:1],
            "downside_risk_score": duplicate_articles[:1]
        }
        res = self.engine.calculate_and_save_production_industry(
            run_id="PROD_TEST_DRIVER_CAP",
            industry_id="TEST_DRV_CAP",
            industry_name="테스트 드라이버 캡",
            thesis="중복 방지",
            factors_dict=factors
        )
        # 10개 기사 총합 30.0점이지만 driver cap 10.0점으로 정확히 제한되어 policy_continuity = 10.0
        self.assertEqual(res["policy_continuity"], 10.0)

    def test_09_manual_qualitative_evidence_cap(self):
        """9. 수동 정성(MANUAL_QUALITATIVE) 증거가 Factor 상한의 35%를 초과하지 못하도록 제한 검증"""
        # policy_continuity max 20.0점 -> 35%인 7.0점까지만 허용
        manual_factors = {
            "policy_continuity": [
                {
                    "family": "MAN_FAMILY", "driver_id": "DRV_MAN_1", "origin_type": "MANUAL_QUALITATIVE",
                    "src": "ANALYST_OPINION", "ref": "OP_REF", "val": 15.0, "cap": 20.0,
                    "dir": "POSITIVE", "rel": "MEDIUM", "pub_date": "2026-08-10", "desc": "정성적 견해"
                }
            ],
            "earnings_linkage": [{"family": "E", "driver_id": "D2", "origin_type": "LIVE_COLLECTED", "src": "S2", "ref": "R2", "val": 20.0, "cap": 25.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "실적"}],
            "visibility_order_revenue": [{"family": "V", "driver_id": "D3", "origin_type": "LIVE_COLLECTED", "src": "S3", "ref": "R3", "val": 15.0, "cap": 20.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "백로그"}],
            "catalysts_score": [{"family": "C", "driver_id": "D4", "origin_type": "LIVE_COLLECTED", "src": "S4", "ref": "R4", "val": 10.0, "cap": 15.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "촉매"}],
            "valuation_burden": [{"family": "B", "driver_id": "D5", "origin_type": "LIVE_COLLECTED", "src": "S5", "ref": "R5", "val": 7.0, "cap": 10.0, "dir": "NEUTRAL", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "밸류"}],
            "downside_risk_score": [{"family": "R", "driver_id": "D6", "origin_type": "LIVE_COLLECTED", "src": "S6", "ref": "R6", "val": 7.0, "cap": 10.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "위험"}]
        }
        res = self.engine.calculate_and_save_production_industry(
            run_id="PROD_TEST_MANUAL_CAP",
            industry_id="TEST_MAN_CAP",
            industry_name="테스트 정성 캡",
            thesis="정성 제한",
            factors_dict=manual_factors
        )
        # 15.0점 입력되었으나 20.0 * 0.35 = 7.0점으로 캡 제한
        self.assertEqual(res["policy_continuity"], 7.0)

    def test_10_unverified_source_discount_and_low_reliability(self):
        """10. 출처 미검증(source_reference 누락) 증거의 50% 감점 및 LOW 신뢰도 강제 검증"""
        unverified_factors = {
            "policy_continuity": [
                {
                    "family": "UNVER", "driver_id": "DRV_UNVER", "origin_type": "UNVERIFIED_SOURCE",
                    "src": "RUMOR", "ref": "", # reference 누락
                    "val": 10.0, "cap": 20.0, "dir": "POSITIVE", "rel": "HIGH",
                    "pub_date": "2026-08-10", "desc": "미검증 소문"
                }
            ],
            "earnings_linkage": [{"family": "E", "driver_id": "D2", "origin_type": "LIVE_COLLECTED", "src": "S2", "ref": "R2", "val": 20.0, "cap": 25.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "실적"}],
            "visibility_order_revenue": [{"family": "V", "driver_id": "D3", "origin_type": "LIVE_COLLECTED", "src": "S3", "ref": "R3", "val": 15.0, "cap": 20.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "백로그"}],
            "catalysts_score": [{"family": "C", "driver_id": "D4", "origin_type": "LIVE_COLLECTED", "src": "S4", "ref": "R4", "val": 10.0, "cap": 15.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "촉매"}],
            "valuation_burden": [{"family": "B", "driver_id": "D5", "origin_type": "LIVE_COLLECTED", "src": "S5", "ref": "R5", "val": 7.0, "cap": 10.0, "dir": "NEUTRAL", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "밸류"}],
            "downside_risk_score": [{"family": "R", "driver_id": "D6", "origin_type": "LIVE_COLLECTED", "src": "S6", "ref": "R6", "val": 7.0, "cap": 10.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "위험"}]
        }
        res = self.engine.calculate_and_save_production_industry(
            run_id="PROD_TEST_UNVER",
            industry_id="TEST_UNVER_IND",
            industry_name="테스트 미검증 산업",
            thesis="미검증",
            factors_dict=unverified_factors
        )
        # 10.0 -> 50% 할인으로 5.0점 반영
        self.assertEqual(res["policy_continuity"], 5.0)

    def test_11_synthetic_evidence_triggers_qa_failed(self):
        """11. Synthetic Evidence 존재 시 QA_FAILED 판정 및 노출 차단 검증"""
        synthetic_factors = {
            "policy_continuity": [
                {
                    "family": "SYN", "driver_id": "DRV_SYN", "origin_type": "SYNTHETIC",
                    "src": "SYN_GEN", "ref": "SYN_REF", "val": 15.0, "cap": 20.0,
                    "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "합성 데이터"
                }
            ],
            "earnings_linkage": [{"family": "E", "driver_id": "D2", "origin_type": "LIVE_COLLECTED", "src": "S2", "ref": "R2", "val": 20.0, "cap": 25.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "실적"}],
            "visibility_order_revenue": [{"family": "V", "driver_id": "D3", "origin_type": "LIVE_COLLECTED", "src": "S3", "ref": "R3", "val": 15.0, "cap": 20.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "백로그"}],
            "catalysts_score": [{"family": "C", "driver_id": "D4", "origin_type": "LIVE_COLLECTED", "src": "S4", "ref": "R4", "val": 10.0, "cap": 15.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "촉매"}],
            "valuation_burden": [{"family": "B", "driver_id": "D5", "origin_type": "LIVE_COLLECTED", "src": "S5", "ref": "R5", "val": 7.0, "cap": 10.0, "dir": "NEUTRAL", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "밸류"}],
            "downside_risk_score": [{"family": "R", "driver_id": "D6", "origin_type": "LIVE_COLLECTED", "src": "S6", "ref": "R6", "val": 7.0, "cap": 10.0, "dir": "POSITIVE", "rel": "HIGH", "pub_date": "2026-08-10", "desc": "위험"}]
        }
        res = self.engine.calculate_and_save_production_industry(
            run_id="PROD_TEST_SYN",
            industry_id="TEST_SYN_IND",
            industry_name="테스트 합성 산업",
            thesis="합성 차단",
            factors_dict=synthetic_factors
        )
        self.assertEqual(res["qa_status"], "QA_FAILED")
        self.assertEqual(res["industry_confidence"], "LOW")

    def test_12_cross_validation_branches(self):
        """12. Industry vs Forward 교차 검증 3대 분기 검증"""
        # 1) confirmed
        self.assertEqual(
            self.engine.cross_validate_industry_forward("INDUSTRY_PASS_STRONG", "VERY_STRONG"),
            "INDUSTRY_COMPANY_CONFIRMED"
        )
        # 2) sector strong, company weak
        self.assertEqual(
            self.engine.cross_validate_industry_forward("INDUSTRY_PASS", "WEAK"),
            "SECTOR_STRONG_COMPANY_WEAK"
        )
        # 3) company strong, sector weak
        self.assertEqual(
            self.engine.cross_validate_industry_forward("INDUSTRY_BLOCK", "STRONG"),
            "COMPANY_STRONG_SECTOR_WEAK"
        )

    def test_13_invariants_and_existing_buy_approval_unaltered(self):
        """13. 불변 검증: Shadow State 및 Industry Score는 기존 Buy Approval을 절대 변경하지 않음"""
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
            industry_score=92.0,
            industry_gate="INDUSTRY_PASS_STRONG",
            industry_bucket="CORE_MOMENTUM",
            industry_confidence="HIGH",
            exposure_type="DIRECT_CORE",
            mapping_version="V1.0",
            cross_validation_state="INDUSTRY_COMPANY_CONFIRMED",
            shadow_integrated_state="CANDIDATE_CONDITIONAL",
            primary_blocker="NONE",
            all_blockers=[]
        )
        self.assertEqual(dto.f_score, 92.0)
        self.assertEqual(dto.t_score, 66.0)
        self.assertEqual(dto.final_score, 76.4)
        self.assertEqual(dto.buy_approval, "🔵 ON (트레일링/눌림목 분할매수 승인)")

    def test_14_formatter_section_0_rendering_with_provenance_qa(self):
        """14. Formatter Section 0 Industry Radar & Provenance QA 렌더링 검증"""
        dto = ScanResultDTO(
            stock_code="000490",
            stock_name="대동",
            collected_at="2026-08-17 14:00:00 KST",
            f_score=56.0,
            t_score=78.0,
            final_score=69.2,
            buy_approval="🔴 OFF (매수 금지/관망)",
            technical_action="BUY_BLOCKED",
            technical_state="NEUTRAL",
            industry_profile="GENERAL_MANUFACTURING",
            industry_score=52.0,
            industry_gate="INDUSTRY_BLOCK",
            industry_bucket="EXCLUDE",
            industry_confidence="MEDIUM",
            exposure_type="DIRECT_CORE",
            mapping_version="V1.0",
            cross_validation_state="MIXED",
            shadow_integrated_state="BLOCKED_BY_INDUSTRY",
            primary_blocker="BLOCKED_BY_INDUSTRY",
            all_blockers=["BLOCKED_BY_INDUSTRY", "BLOCKED_BY_FORWARD_RISK", "BLOCKED_BY_TECHNICAL"],
            verified_evidence_pct=100.0,
            manual_evidence_pct=0.0,
            synthetic_evidence_pct=0.0,
            fresh_evidence_pct=100.0,
            driver_count=6,
            evidence_count=6,
            qa_status="QA_PASSED"
        )
        md = render_gems_markdown(dto)
        self.assertIn("0. 🛰️ Weekly Industry Radar & Integrated Shadow Matrix", md)
        self.assertIn("소속 산업 Profile: GENERAL_MANUFACTURING (Score: 52.0점", md)
        self.assertIn("Evidence Provenance QA: LiveFetched", md)
        self.assertIn("기업 Exposure Type: DIRECT_CORE (Mapping Ver: V1.0)", md)
        self.assertIn("Multi-Layer Shadow Integrated State: [BLOCKED_BY_INDUSTRY]", md)
        self.assertIn("Primary Blocker: BLOCKED_BY_INDUSTRY | All Blockers: BLOCKED_BY_INDUSTRY, BLOCKED_BY_FORWARD_RISK, BLOCKED_BY_TECHNICAL", md)

    def test_15_replay_engine_deterministic_reproducibility(self):
        """15. Deterministic Replay Engine: Source -> Fact JSON -> Normalization -> Value 100% 재현 검증"""
        audit_records = self.engine.execute_replay_audit("PROD_2026_W33_001")
        self.assertGreaterEqual(len(audit_records), 10)
        for rec in audit_records:
            self.assertTrue(rec["is_match"], f"Replay mismatch on {rec['source_document_id']}: diff={rec['difference']}")
            self.assertEqual(rec["replay_status"], "REPLAY_VERIFIED")

    def test_16_closed_research_classified_as_reference_verified(self):
        """16. 유료/폐쇄형 리서치(Wood Mackenzie, Clarksons 등)가 LIVE_FETCHED가 아닌 REFERENCE_VERIFIED로 분류됨을 검증"""
        evs = self.db.get_industry_evidence_for_run("PROD_2026_W33_001", "POWER_EQUIPMENT")
        wm_ev = next(e for e in evs if "WOOD_MACKENZIE" in e["source_name"])
        self.assertEqual(wm_ev["origin_type"], "REFERENCE_VERIFIED")
        self.assertEqual(wm_ev["fetch_method"], "CITATION_CROSS_CHECK")
        self.assertEqual(wm_ev["http_status"], "CITATION_VERIFIED")

    def test_17_canonical_fact_json_structure_and_hash(self):
        """17. 모든 Production Evidence에 Canonical Fact JSON 및 무결성 해시가 정상 적재됨을 검증"""
        evs = self.db.get_industry_evidence_for_run("PROD_2026_W33_001")
        for ev in evs:
            self.assertTrue(bool(ev["extracted_fact_json"]), f"Missing fact JSON for {ev['source_document_id']}")
            self.assertTrue(bool(ev["raw_payload_hash"]), f"Missing payload hash for {ev['source_document_id']}")
            self.assertTrue(bool(ev["source_published_at"]), f"Missing source_published_at for {ev['source_document_id']}")

    def test_18_production_run_replay_qa_passed(self):
        """18. Production Run 전체의 Replay 검증 및 QA 상태가 QA_PASSED임을 검증"""
        prod_run = self.db.execute_query("SELECT * FROM industry_runs WHERE run_type = 'PRODUCTION' ORDER BY id DESC LIMIT 1")
        self.assertIsNotNone(prod_run)
        r = dict(prod_run[0])
        self.assertEqual(r["qa_status"], "QA_PASSED")
        self.assertEqual(r["synthetic_evidence_count"], 0)
        self.assertEqual(r["replay_failed_count"], 0)
        self.assertGreater(r["replay_verified_count"], 0)

if __name__ == '__main__':
    unittest.main()
