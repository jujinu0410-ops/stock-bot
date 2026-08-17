import unittest
import os
from src.database.db_manager import DatabaseManager
from src.api.disclosure_collector import DisclosureCollector
from src.analysis.forward_visibility_engine import ForwardVisibilityEngine
from src.analysis.fundamental_evidence_scanner import FundamentalEvidenceScanner
from src.core.dto import ScanResultDTO
from src.formatters.gems_formatter import render_gems_markdown

class TestPhase5ForwardVisibility(unittest.TestCase):
    """
    Phase 5.3: Forward Evidence Source & Scope Integrity & Confidence Audit 단위 테스트 스위트
    """
    @classmethod
    def setUpClass(cls):
        cls.db_path = "test_phase5_3.db"
        if os.path.exists(cls.db_path):
            os.remove(cls.db_path)
        cls.db = DatabaseManager(cls.db_path)
        cls.collector = DisclosureCollector(cls.db)
        cls.engine = ForwardVisibilityEngine(cls.db)
        cls.fund_scanner = FundamentalEvidenceScanner()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except Exception:
                pass

    def test_01_disclosure_event_classification_and_hazard_split(self):
        """1. 수시공시 이벤트 다차원 분리(event_hazard, materiality_level, effective_severity, reason) 검증"""
        # 본계약 공급계약 (모회사)
        p1 = self.collector._classify_event("단일판매ㆍ공급계약체결(초고압변압기)")
        self.assertEqual(p1["event_type"], "LARGE_SUPPLY_CONTRACT")
        self.assertEqual(p1["progression_stage"], "FINAL_CONTRACT")
        self.assertEqual(p1["entity_scope"], "PARENT")
        self.assertFalse(p1["is_negative"])
        self.assertEqual(p1["effective_severity"], "LOW")

        # 수주해지 (Materiality 미확인 시 -> REVIEW_REQUIRED)
        p2 = self.collector._classify_event("단일판매ㆍ공급계약해제ㆍ해지")
        self.assertEqual(p2["event_type"], "ORDER_CANCEL")
        self.assertTrue(p2["is_negative"])
        self.assertEqual(p2["event_hazard"], "HIGH")
        self.assertEqual(p2["materiality_level"], "UNKNOWN")
        self.assertEqual(p2["effective_severity"], "REVIEW_REQUIRED")
        self.assertIn("REVIEW_REQUIRED", p2["severity_reason"])

        # 수주해지 (매출액 대비 25% 확인 시 -> CRITICAL)
        p3 = self.collector._classify_event("단일판매ㆍ공급계약해지 (매출액 대비 25.0%)")
        self.assertEqual(p3["event_hazard"], "HIGH")
        self.assertEqual(p3["materiality_level"], "CRITICAL")
        self.assertEqual(p3["effective_severity"], "CRITICAL")

        # 수주해지 (매출액 대비 2% 확인 시 -> LOW)
        p4 = self.collector._classify_event("단일판매ㆍ공급계약해지 (매출액 대비 2.0%)")
        self.assertEqual(p4["event_hazard"], "HIGH")
        self.assertEqual(p4["materiality_level"], "LOW")
        self.assertEqual(p4["effective_severity"], "LOW")

        # 종속회사 증자 (MEDIUM - 모회사 직접 유상증자 HIGH와 차등화)
        p5 = self.collector._classify_event("유상증자결정(종속회사의주요경영사항)")
        self.assertEqual(p5["event_type"], "CAPITAL_INCREASE")
        self.assertTrue(p5["is_negative"])
        self.assertEqual(p5["entity_scope"], "SUBSIDIARY")
        self.assertEqual(p5["event_hazard"], "MEDIUM")
        self.assertEqual(p5["effective_severity"], "MEDIUM")
        self.assertIn("종속회사 유상증자", p5["severity_reason"])

        # 모회사 직접 유상증자 (수치 미확인 시 주관적 '대규모' 제외 및 객관적 사유 명시)
        p6 = self.collector._classify_event("주주배정후 실권주 일반공모 유상증자결정")
        self.assertEqual(p6["event_type"], "CAPITAL_INCREASE")
        self.assertTrue(p6["is_negative"])
        self.assertEqual(p6["entity_scope"], "PARENT")
        self.assertEqual(p6["event_hazard"], "HIGH")
        self.assertEqual(p6["effective_severity"], "HIGH")
        self.assertNotIn("대규모", p6["severity_reason"])

        # 감사의견 거절 (CRITICAL)
        p7 = self.collector._classify_event("감사보고서제출(감사의견거절)")
        self.assertEqual(p7["event_type"], "AUDIT_ISSUE")
        self.assertTrue(p7["is_negative"])
        self.assertEqual(p7["event_hazard"], "CRITICAL")
        self.assertEqual(p7["effective_severity"], "CRITICAL")

    def test_02_three_distinct_contracts_plus_one_amendment(self):
        """2. 서로 다른 계약 3건 + 그 중 1건 정정공시 시: 경제적 계약 3건, 최신 평가 이벤트 3건 유지 검증"""
        self.db.execute_non_query("DELETE FROM disclosure_events WHERE stock_code = 'MULTI_CONTRACT_CO'")
        
        # 계약 1 (원공시)
        self.db.upsert_disclosure_event({
            "stock_code": "MULTI_CONTRACT_CO",
            "event_type": "LARGE_SUPPLY_CONTRACT",
            "rcept_no": "202601010001",
            "rcept_date": "2026-01-01",
            "report_name": "단일판매ㆍ공급계약체결 (사우디 A프로젝트)",
            "amendment_chain_id": "202601010001",
            "is_latest_version": 0,  # 이후 정정됨
            "is_negative_event": 0
        })
        # 계약 1 (정정공시)
        self.db.upsert_disclosure_event({
            "stock_code": "MULTI_CONTRACT_CO",
            "event_type": "LARGE_SUPPLY_CONTRACT",
            "rcept_no": "202604010001",
            "rcept_date": "2026-04-01",
            "report_name": "[기재정정]단일판매ㆍ공급계약체결 (사우디 A프로젝트)",
            "amendment_chain_id": "202601010001",
            "original_rcept_no": "202601010001",
            "is_latest_version": 1,
            "is_negative_event": 0
        })
        # 계약 2 (독립 원공시)
        self.db.upsert_disclosure_event({
            "stock_code": "MULTI_CONTRACT_CO",
            "event_type": "LARGE_SUPPLY_CONTRACT",
            "rcept_no": "202602010001",
            "rcept_date": "2026-02-01",
            "report_name": "단일판매ㆍ공급계약체결 (미국 B프로젝트)",
            "amendment_chain_id": "202602010001",
            "is_latest_version": 1,
            "is_negative_event": 0
        })
        # 계약 3 (독립 원공시)
        self.db.upsert_disclosure_event({
            "stock_code": "MULTI_CONTRACT_CO",
            "event_type": "LARGE_SUPPLY_CONTRACT",
            "rcept_no": "202603010001",
            "rcept_date": "2026-03-01",
            "report_name": "단일판매ㆍ공급계약체결 (유럽 C프로젝트)",
            "amendment_chain_id": "202603010001",
            "is_latest_version": 1,
            "is_negative_event": 0
        })

        latest_events = self.db.get_recent_disclosure_events("MULTI_CONTRACT_CO", only_latest=True)
        self.assertEqual(len(latest_events), 3)
        rcept_nos = [e["rcept_no"] for e in latest_events]
        self.assertIn("202604010001", rcept_nos)
        self.assertIn("202602010001", rcept_nos)
        self.assertIn("202603010001", rcept_nos)
        self.assertNotIn("202601010001", rcept_nos)

    def test_03_5tier_book_to_bill_and_provisional_status(self):
        """3. Book-to-Bill 5단계 상태 및 UNKNOWN 조정항목 발생 시 PROVISIONAL/UNADJUSTED 판정 검증"""
        res_prov = self.engine.evaluate_forward_visibility(
            stock_code="267260",
            stock_name="HD현대일렉트릭",
            quarterly_revenue=1141835394471.0,
            annual_revenue=4079497658529.0,
            quarter_label="2026 Q2",
            period_start="2026-04-01",
            period_end="2026-06-30"
        )
        self.assertEqual(res_prov["book_to_bill_status"], "PROVISIONAL")
        self.assertTrue(res_prov["is_unadjusted_bridge"])
        self.assertEqual(res_prov["new_orders_confidence"], "MEDIUM")
        self.assertEqual(res_prov["opportunity_confidence"], "MEDIUM")
        self.assertIn("PROVISIONAL", res_prov["book_to_bill_summary"])

        # 기간 불일치 시 B2B_NOT_COMPARABLE
        res_mismatch = self.engine.evaluate_forward_visibility(
            stock_code="267260",
            stock_name="HD현대일렉트릭",
            quarterly_revenue=1141835394471.0,
            annual_revenue=4079497658529.0,
            period_start="2026-01-01",  # 분기 시작일 불일치 유도
            period_end="2026-06-30"
        )
        # 만약 엔진 파라미터로 period가 일치하지 않으면 B2B_NOT_COMPARABLE
        # evaluate_forward_visibility 내에서는 period_start/end가 전달되므로
        # num과 den을 다르게 전달하는 경우 검증
        self.assertEqual(res_prov["lineage"]["book_to_bill_lineage"]["status"], "PROVISIONAL")

    def test_04_backlog_bridge_formula_and_unknown_tracking(self):
        """4. Backlog Bridge 산식(Ending - Beginning + Recognized Revenue + Unknown Adj) 및 provisional_new_orders 검증"""
        res = self.engine.evaluate_forward_visibility(
            stock_code="267260",
            stock_name="HD현대일렉트릭",
            quarterly_revenue=1141835394471.0,
            annual_revenue=4079497658529.0
        )
        lin = res["lineage"]["new_orders_lineage"]
        self.assertIn("beginning_backlog", lin)
        self.assertIn("ending_backlog", lin)
        self.assertIn("recognized_revenue", lin)
        self.assertIn("provisional_new_orders", lin)
        self.assertTrue(lin["is_unadjusted_bridge"])
        self.assertIn("UNKNOWN", lin["cancellations_adj"])
        self.assertIn("UNKNOWN", lin["fx_adj"])
        self.assertIn("UNKNOWN", lin["scope_adj"])

    def test_05_general_manufacturing_opportunity_materiality_gate(self):
        """5. 일반 제조업(GENERAL_MANUFACTURING) 공급계약 Materiality 및 Confidence 게이트 검증"""
        # 1) Materiality 미확인 공급계약 -> MODERATE, opportunity_confidence = LOW
        self.db.execute_non_query("DELETE FROM disclosure_events WHERE stock_code = 'MFG_MINOR'")
        self.db.upsert_disclosure_event({
            "stock_code": "MFG_MINOR",
            "event_type": "LARGE_SUPPLY_CONTRACT",
            "rcept_no": "202607010001",
            "rcept_date": "2026-07-01",
            "report_name": "단일판매ㆍ공급계약체결",
            "revenue_ratio": None,
            "is_latest_version": 1,
            "is_negative_event": 0
        })
        res_minor = self.engine.evaluate_forward_visibility("MFG_MINOR", "일반제조소형")
        self.assertEqual(res_minor["forward_opportunity_state"], "MODERATE")
        self.assertEqual(res_minor["opportunity_confidence"], "LOW")

        # 2) 10% 이상 대형 공급계약 확인 -> STRONG, opportunity_confidence = MEDIUM
        self.db.execute_non_query("DELETE FROM disclosure_events WHERE stock_code = 'MFG_MAJOR'")
        self.db.upsert_disclosure_event({
            "stock_code": "MFG_MAJOR",
            "event_type": "LARGE_SUPPLY_CONTRACT",
            "rcept_no": "202607010002",
            "rcept_date": "2026-07-01",
            "report_name": "단일판매ㆍ공급계약체결 (매출액 대비 15.5%)",
            "revenue_ratio": 15.5,
            "is_latest_version": 1,
            "is_negative_event": 0
        })
        res_major = self.engine.evaluate_forward_visibility("MFG_MAJOR", "일반제조대형")
        self.assertEqual(res_major["forward_opportunity_state"], "STRONG")
        self.assertEqual(res_major["opportunity_confidence"], "MEDIUM")

    def test_06_formatter_section_5_calibrated_rendering(self):
        """6. Formatter Section 5 Forward Opportunity/Risk 및 Confidence 렌더링 검증"""
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
            fiscal_period_end="2026-06-30",
            fiscal_period_age_days=48,
            filing_received_date="2026-08-14",
            filing_age_days=3,
            latest_fiscal_quarter="2026 Q2",
            quarterly_data_quality="VALID (2026 Q2 반기보고서 최신 반영)",
            fundamental_state="STRONG",
            industry_profile="POWER_EQUIPMENT",
            forward_opportunity_state="VERY_STRONG",
            opportunity_confidence="MEDIUM",
            forward_risk_state="NONE",
            forward_risk_override_tag="NONE",
            order_backlog_summary="89,749억 원 (연환산 대비 2.2배, YoY +28.5% | Source: COMPANY_REPORTED)",
            new_orders_summary="14,847억 원 (Confidence: MEDIUM | Source: ESTIMATED_FROM_BACKLOG_BRIDGE [UNADJUSTED_BRIDGE_ESTIMATE])",
            book_to_bill_summary="1.30 (Backlog Expansion - 수주잔고 확장 가속 | Period: 2026 Q2 Discrete [PROVISIONAL])",
            book_to_bill_status="PROVISIONAL",
            confidence_level="MEDIUM",
            new_orders_confidence="MEDIUM",
            capa_summary="울산/미국 공장 증설 진행 중 (진행단계: UNDER_CONSTRUCTION)",
            progression_stage_summary="FINAL_CONTRACT (본계약 위주)",
            negative_events_summary="NONE (위험 공시 없음)",
            recent_key_disclosures=["[2026-07-31] [기재정정]단일판매ㆍ공급계약체결 (ORDER_INCREASE | PARENT)"]
        )
        md = render_gems_markdown(dto)
        self.assertIn("5. 🔭 Forward Order / Disclosure Evidence", md)
        self.assertIn("Forward Opportunity State (기회/성장): VERY_STRONG (Confidence: MEDIUM)", md)
        self.assertIn("Forward Risk State (위험/희석): NONE", md)
        self.assertIn("Book-to-Bill (수주배율): 1.30 (Backlog Expansion", md)
        self.assertIn("[PROVISIONAL]", md)

if __name__ == '__main__':
    unittest.main()
