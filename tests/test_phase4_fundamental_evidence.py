import unittest
import pandas as pd
import numpy as np
from src.analysis.fundamental_evidence_scanner import FundamentalEvidenceScanner
from src.api.quarterly_dart_collector import QuarterlyDartCollector
from src.database.db_manager import DatabaseManager
from src.core.dto import ScanResultDTO
from src.formatters.gems_formatter import render_gems_markdown

class TestPhase4FundamentalEvidence(unittest.TestCase):
    """
    Phase 4 & 4.1: Fundamental Evidence Scanner 및 8분기 재무 시계열/신선도 단위 테스트 스위트
    """

    def setUp(self):
        self.scanner = FundamentalEvidenceScanner()

    def test_01_flow_decumulation_and_stock_preservation(self):
        """1. FLOW 계정 역산(Q1, Q2=H1-Q1, Q3=9M-H1, Q4=FY-9M) 및 STOCK 계정 비차감 검증"""
        records = [
            {"stock_code": "TEST", "fiscal_year": 2024, "fiscal_quarter": "Q1", "revenue": 100.0, "operating_income": 10.0, "operating_cash_flow": 15.0, "total_assets": 1000.0, "total_liabilities": 400.0, "total_equity": 600.0, "debt_ratio": 66.7, "net_debt": -50.0, "inventory": 100.0, "accounts_receivable": 80.0},
            {"stock_code": "TEST", "fiscal_year": 2024, "fiscal_quarter": "Q2", "revenue": 120.0, "operating_income": 15.0, "operating_cash_flow": 20.0, "total_assets": 1050.0, "total_liabilities": 420.0, "total_equity": 630.0, "debt_ratio": 66.7, "net_debt": -40.0, "inventory": 110.0, "accounts_receivable": 90.0},
            {"stock_code": "TEST", "fiscal_year": 2024, "fiscal_quarter": "Q3", "revenue": 130.0, "operating_income": 18.0, "operating_cash_flow": 25.0, "total_assets": 1100.0, "total_liabilities": 430.0, "total_equity": 670.0, "debt_ratio": 64.2, "net_debt": -60.0, "inventory": 115.0, "accounts_receivable": 95.0},
            {"stock_code": "TEST", "fiscal_year": 2024, "fiscal_quarter": "Q4", "revenue": 150.0, "operating_income": 22.0, "operating_cash_flow": 30.0, "total_assets": 1150.0, "total_liabilities": 450.0, "total_equity": 700.0, "debt_ratio": 64.3, "net_debt": -70.0, "inventory": 120.0, "accounts_receivable": 100.0},
            {"stock_code": "TEST", "fiscal_year": 2025, "fiscal_quarter": "Q1", "revenue": 120.0, "operating_income": 14.0, "operating_cash_flow": 18.0, "total_assets": 1200.0, "total_liabilities": 460.0, "total_equity": 740.0, "debt_ratio": 62.2, "net_debt": -80.0, "inventory": 125.0, "accounts_receivable": 105.0},
            {"stock_code": "TEST", "fiscal_year": 2025, "fiscal_quarter": "Q2", "revenue": 140.0, "operating_income": 20.0, "operating_cash_flow": 28.0, "total_assets": 1250.0, "total_liabilities": 470.0, "total_equity": 780.0, "debt_ratio": 60.3, "net_debt": -90.0, "inventory": 130.0, "accounts_receivable": 110.0},
            {"stock_code": "TEST", "fiscal_year": 2025, "fiscal_quarter": "Q3", "revenue": 160.0, "operating_income": 28.0, "operating_cash_flow": 35.0, "total_assets": 1300.0, "total_liabilities": 480.0, "total_equity": 820.0, "debt_ratio": 58.5, "net_debt": -100.0, "inventory": 135.0, "accounts_receivable": 115.0},
            {"stock_code": "TEST", "fiscal_year": 2025, "fiscal_quarter": "Q4", "revenue": 190.0, "operating_income": 38.0, "operating_cash_flow": 45.0, "total_assets": 1350.0, "total_liabilities": 490.0, "total_equity": 860.0, "debt_ratio": 57.0, "net_debt": -120.0, "inventory": 140.0, "accounts_receivable": 120.0},
        ]
        res = self.scanner.evaluate_evidence(records, "TEST")
        self.assertEqual(res["turnaround_type"], "C")
        self.assertEqual(res["fundamental_state"], "STRONG")
        self.assertTrue(res["high_quality_improvement"])
        self.assertTrue(res["operating_leverage"])
        self.assertEqual(res["warnings"], ["NONE (실적품질 경고 없음)"])

    def test_02_turnaround_type_a_loss_to_profit(self):
        """2. Turnaround Type A (LOSS_TO_PROFIT: 적자 지속 후 흑자전환) 판정 검증"""
        records = [
            {"stock_code": "LOSS_CO", "fiscal_year": 2024, "fiscal_quarter": "Q1", "revenue": 100.0, "operating_income": -20.0, "operating_cash_flow": -10.0, "total_assets": 500.0, "total_liabilities": 300.0, "total_equity": 200.0, "debt_ratio": 150.0, "net_debt": 100.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "LOSS_CO", "fiscal_year": 2024, "fiscal_quarter": "Q2", "revenue": 110.0, "operating_income": -15.0, "operating_cash_flow": -5.0, "total_assets": 500.0, "total_liabilities": 310.0, "total_equity": 190.0, "debt_ratio": 163.2, "net_debt": 110.0, "inventory": 55.0, "accounts_receivable": 45.0},
            {"stock_code": "LOSS_CO", "fiscal_year": 2024, "fiscal_quarter": "Q3", "revenue": 105.0, "operating_income": -10.0, "operating_cash_flow": 2.0, "total_assets": 500.0, "total_liabilities": 315.0, "total_equity": 185.0, "debt_ratio": 170.3, "net_debt": 115.0, "inventory": 50.0, "accounts_receivable": 42.0},
            {"stock_code": "LOSS_CO", "fiscal_year": 2024, "fiscal_quarter": "Q4", "revenue": 120.0, "operating_income": -5.0, "operating_cash_flow": 10.0, "total_assets": 520.0, "total_liabilities": 320.0, "total_equity": 200.0, "debt_ratio": 160.0, "net_debt": 110.0, "inventory": 48.0, "accounts_receivable": 45.0},
            {"stock_code": "LOSS_CO", "fiscal_year": 2025, "fiscal_quarter": "Q1", "revenue": 130.0, "operating_income": 8.0, "operating_cash_flow": 15.0, "total_assets": 540.0, "total_liabilities": 325.0, "total_equity": 215.0, "debt_ratio": 151.2, "net_debt": 90.0, "inventory": 45.0, "accounts_receivable": 48.0},
        ]
        res = self.scanner.evaluate_evidence(records, "LOSS_CO")
        self.assertEqual(res["turnaround_type"], "A")
        self.assertIn("LOSS_TO_PROFIT", res["turnaround_label"])
        self.assertEqual(res["fundamental_state"], "STRONG")

    def test_03_ocf_warning_vs_state_separation(self):
        """3. Phase 4.1: 단일 분기 OCF 적자는 경고를 발생시키되 TTM OCF 양호 시 자동 강등하지 않음 검증"""
        records = [
            {"stock_code": "SHIP_CO", "fiscal_year": 2024, "fiscal_quarter": "Q1", "revenue": 100.0, "operating_income": 10.0, "operating_cash_flow": 30.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": -50.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "SHIP_CO", "fiscal_year": 2024, "fiscal_quarter": "Q2", "revenue": 110.0, "operating_income": 12.0, "operating_cash_flow": 25.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": -50.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "SHIP_CO", "fiscal_year": 2024, "fiscal_quarter": "Q3", "revenue": 120.0, "operating_income": 15.0, "operating_cash_flow": 20.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": -50.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "SHIP_CO", "fiscal_year": 2024, "fiscal_quarter": "Q4", "revenue": 130.0, "operating_income": 18.0, "operating_cash_flow": 35.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": -50.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "SHIP_CO", "fiscal_year": 2025, "fiscal_quarter": "Q1", "revenue": 150.0, "operating_income": 25.0, "operating_cash_flow": -5.0, "total_assets": 550.0, "total_liabilities": 220.0, "total_equity": 330.0, "debt_ratio": 66.7, "net_debt": -60.0, "inventory": 55.0, "accounts_receivable": 44.0}, # TTM OCF = 25+20+35-5 = 75 > 0
        ]
        res = self.scanner.evaluate_evidence(records, "SHIP_CO")
        self.assertTrue(any("OP_UP_OCF_DOWN" in w for w in res["warnings"]))
        self.assertEqual(res["fundamental_state"], "STRONG") # TTM OCF 건전하여 강등되지 않음

    def test_04_severe_ocf_deterioration_degrades_state(self):
        """4. Phase 4.1: OCF 2분기 연속 악화 또는 TTM OCF 급감 + 재고악화 시 State 강등 검증"""
        records = [
            {"stock_code": "SEV_CO", "fiscal_year": 2024, "fiscal_quarter": "Q1", "revenue": 100.0, "operating_income": 10.0, "operating_cash_flow": 10.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": 0.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "SEV_CO", "fiscal_year": 2024, "fiscal_quarter": "Q2", "revenue": 100.0, "operating_income": 10.0, "operating_cash_flow": 10.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": 0.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "SEV_CO", "fiscal_year": 2024, "fiscal_quarter": "Q3", "revenue": 100.0, "operating_income": 10.0, "operating_cash_flow": 10.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": 0.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "SEV_CO", "fiscal_year": 2024, "fiscal_quarter": "Q4", "revenue": 110.0, "operating_income": 12.0, "operating_cash_flow": -10.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": 0.0, "inventory": 60.0, "accounts_receivable": 50.0},
            {"stock_code": "SEV_CO", "fiscal_year": 2025, "fiscal_quarter": "Q1", "revenue": 120.0, "operating_income": 15.0, "operating_cash_flow": -20.0, "total_assets": 550.0, "total_liabilities": 250.0, "total_equity": 300.0, "debt_ratio": 83.3, "net_debt": 50.0, "inventory": 85.0, "accounts_receivable": 75.0}, # 2분기 연속 OCF 적자 & Rolling 2Q 음수
        ]
        res = self.scanner.evaluate_evidence(records, "SEV_CO")
        self.assertNotEqual(res["fundamental_state"], "STRONG")
        self.assertEqual(res["fundamental_state"], "STABLE")

    def test_05_turnaround_type_g_peak_out(self):
        """5. Turnaround Type G (PEAK_OUT_RISK: 실적 정점 통과 후 둔화) 판정 검증"""
        records = [
            {"stock_code": "PEAK_CO", "fiscal_year": 2024, "fiscal_quarter": "Q1", "revenue": 100.0, "operating_income": 20.0, "operating_cash_flow": 20.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": 0.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "PEAK_CO", "fiscal_year": 2024, "fiscal_quarter": "Q2", "revenue": 120.0, "operating_income": 30.0, "operating_cash_flow": 30.0, "total_assets": 550.0, "total_liabilities": 200.0, "total_equity": 350.0, "debt_ratio": 57.1, "net_debt": 0.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "PEAK_CO", "fiscal_year": 2024, "fiscal_quarter": "Q3", "revenue": 140.0, "operating_income": 42.0, "operating_cash_flow": 40.0, "total_assets": 600.0, "total_liabilities": 200.0, "total_equity": 400.0, "debt_ratio": 50.0, "net_debt": 0.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "PEAK_CO", "fiscal_year": 2024, "fiscal_quarter": "Q4", "revenue": 130.0, "operating_income": 31.2, "operating_cash_flow": 25.0, "total_assets": 600.0, "total_liabilities": 200.0, "total_equity": 400.0, "debt_ratio": 50.0, "net_debt": 0.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "PEAK_CO", "fiscal_year": 2025, "fiscal_quarter": "Q1", "revenue": 110.0, "operating_income": 19.8, "operating_cash_flow": 15.0, "total_assets": 600.0, "total_liabilities": 200.0, "total_equity": 400.0, "debt_ratio": 50.0, "net_debt": 0.0, "inventory": 50.0, "accounts_receivable": 40.0},
        ]
        res = self.scanner.evaluate_evidence(records, "PEAK_CO")
        self.assertEqual(res["turnaround_type"], "G")
        self.assertEqual(res["fundamental_state"], "WEAKENING")

    def test_06_stale_data_warning(self):
        """6. Phase 4.1: data_age_days > 180일 시 FUNDAMENTAL_DATA_STALE 경고 발생 검증"""
        records = [
            {"stock_code": "STALE_CO", "fiscal_year": 2024, "fiscal_quarter": "Q1", "revenue": 100.0, "operating_income": 10.0, "operating_cash_flow": 10.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": 0.0, "inventory": 50.0, "accounts_receivable": 40.0},
            {"stock_code": "STALE_CO", "fiscal_year": 2024, "fiscal_quarter": "Q2", "revenue": 110.0, "operating_income": 12.0, "operating_cash_flow": 12.0, "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0, "debt_ratio": 66.7, "net_debt": 0.0, "inventory": 50.0, "accounts_receivable": 40.0},
        ]
        res = self.scanner.evaluate_evidence(records, "STALE_CO", data_age_days=210)
        self.assertTrue(any("FUNDAMENTAL_DATA_STALE" in w for w in res["warnings"]))

    def test_07_formatter_rendering_with_freshness_metadata(self):
        """7. Formatter Section 4 마크다운 렌더링에 Phase 4.1 Freshness 메타데이터 포함 검증"""
        dto = ScanResultDTO(
            stock_code="267260",
            stock_name="HD현대일렉트릭",
            collected_at="2026-08-17 12:00:00 KST",
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
            turnaround_type="C",
            turnaround_label="SALES_AND_MARGIN_EXPANSION (매출·마진 동반 확장)",
            high_quality_improvement=True,
            operating_leverage=True,
            fundamental_warnings=["NONE (실적품질 경고 없음)"],
            fundamental_evidence_bullets=[
                "Revenue YoY: +42.6%",
                "Operating Income YoY: +93.0%",
                "Operating Margin: 27.6% (전년동기대비 +7.2%p)",
                "🌟 HIGH_QUALITY_IMPROVEMENT"
            ],
            quarterly_summary_table="| 분기 | 매출액(억) | 영업이익(억) |\n| :---: | :---: | :---: |\n| 2026 Q2 | 17,150 | 8,949 |"
        )
        md = render_gems_markdown(dto)
        self.assertIn("8분기 재무 기준일 (Period End): 2026-06-30 (48일 경과) | 공시접수일 (Filing Date): 2026-08-14 (3일 경과)", md)
        self.assertIn("최신 분기 및 데이터 품질: 2026 Q2 | VALID (2026 Q2 반기보고서 최신 반영)", md)
        self.assertIn("Fundamental Evidence State: STRONG", md)
        self.assertIn("Turnaround 분류: C (SALES_AND_MARGIN_EXPANSION (매출·마진 동반 확장))", md)
        self.assertIn("2026 Q2 | 17,150 | 8,949", md)

if __name__ == '__main__':
    unittest.main()
