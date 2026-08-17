import unittest
import pandas as pd
import numpy as np
from src.analysis.intraday_analysis import Intraday45mAnalyzer
from src.analysis.technical_gate import TechnicalGate
from src.core.dto import ScanResultDTO
from src.formatters.gems_formatter import render_gems_markdown

class TestPhase31DataLineage(unittest.TestCase):
    """
    Phase 3.1 Data Lineage & Intraday Reliability 단위 테스트 스위트
    """

    def setUp(self):
        self.analyzer = Intraday45mAnalyzer()

    def test_01_intraday_missing_data_explicit_error_code(self):
        """1. 결측값 정책: 데이터 부재 시 0이나 [0,0]으로 위장하지 않고 None 및 명시적 에러 코드 반환"""
        res = self.analyzer.analyze_45m_indicators("999999")  # 존재하지 않는 임의의 코드
        self.assertFalse(res["has_45m_data"])
        self.assertIn("INVALID", res["intraday_quality"])
        self.assertEqual(res["intraday_error_code"], "NO_INTRADAY_DATA")
        self.assertIsNone(res["adx_14_45m"])
        self.assertIsNone(res["intraday_cho_recent2"])

    def test_02_prompt_branching_buy_allowed(self):
        """2. Gemini 프롬프트 분기: BUY_ALLOWED ➔ 주문설정 요약 요청"""
        dto = ScanResultDTO(
            stock_code="005930",
            stock_name="삼성전자",
            collected_at="2026-08-17 12:00:00 KST",
            buy_approval="🔵 ON (트레일링/눌림목 분할매수 승인)",
            technical_action="BUY_ALLOWED",
            technical_state="STRONG"
        )
        md = render_gems_markdown(dto)
        self.assertIn("🟢 1차 진입(50%) 주문 승인", md)
        self.assertIn("이 종목의 키움 트레일링 매수/매도 설정가와 수량 비중 가이드를 요약해 줘", md)

    def test_03_prompt_branching_buy_allowed_conditional(self):
        """3. Gemini 프롬프트 분기: BUY_ALLOWED_CONDITIONAL ➔ 조건부 승인조건 확인 요청"""
        dto = ScanResultDTO(
            stock_code="000490",
            stock_name="대동",
            collected_at="2026-08-17 12:00:00 KST",
            buy_approval="🔵 ON (트레일링/눌림목 분할매수 승인)",
            technical_action="BUY_ALLOWED_CONDITIONAL",
            technical_state="NEUTRAL"
        )
        md = render_gems_markdown(dto)
        self.assertIn("🟡 1차 진입(50%) 조건부 승인", md)
        self.assertIn("이 종목의 조건부 매수 승인 조건과 키움 분할매수/트레일링 설정 가이드를 요약해 줘", md)

    def test_04_prompt_branching_buy_wait_and_wait_data(self):
        """4. Gemini 프롬프트 분기: BUY_WAIT / BUY_WAIT_DATA ➔ 대기 사유 및 재확인 조건 요청, 주문설정 요청 금지"""
        dto_wait = ScanResultDTO(
            stock_code="000490",
            stock_name="대동",
            collected_at="2026-08-17 12:00:00 KST",
            buy_approval="🔵 ON (트레일링/눌림목 분할매수 승인)",
            technical_action="BUY_WAIT",
            technical_state="WEAK"
        )
        md_wait = render_gems_markdown(dto_wait)
        self.assertIn("⏸️ 1차 진입 보류/대기", md_wait)
        self.assertIn("이 종목의 1차 진입 대기(BUY_WAIT) 사유와 향후 진입 승인 전환을 위한 분봉/수급 재확인 조건을 분석해 줘", md_wait)
        self.assertNotIn("키움 트레일링 매수/매도 설정가와 수량 비중 가이드를 요약해 줘", md_wait)

        dto_data = ScanResultDTO(
            stock_code="000490",
            stock_name="대동",
            collected_at="2026-08-17 12:00:00 KST",
            buy_approval="🔵 ON (트레일링/눌림목 분할매수 승인)",
            technical_action="BUY_WAIT_DATA",
            technical_state="UNKNOWN"
        )
        md_data = render_gems_markdown(dto_data)
        self.assertIn("• 현재 실행 가능 주문: 🔴 NONE (45분봉 데이터 확인 필요 / 현재 주문 금지 - BUY_WAIT_DATA)", md_data)
        self.assertIn("이 종목의 1차 진입 대기(BUY_WAIT) 사유와 향후 진입 승인 전환을 위한 분봉/수급 재확인 조건을 분석해 줘", md_data)

    def test_05_prompt_branching_buy_blocked(self):
        """5. Gemini 프롬프트 분기: BUY_BLOCKED ➔ 매수차단 사유 및 재평가 조건 요청"""
        dto_blocked = ScanResultDTO(
            stock_code="000490",
            stock_name="대동",
            collected_at="2026-08-17 12:00:00 KST",
            buy_approval="🔴 OFF (매수 금지/관망)",
            technical_action="BUY_BLOCKED",
            technical_state="DAMAGED"
        )
        md = render_gems_markdown(dto_blocked)
        self.assertIn("• 현재 실행 가능 주문: 🔴 NONE (현재 매수 금지 / 주문 입력 불가 - BUY_BLOCKED)", md)
        self.assertIn("이 종목이 매수 차단(BUY_BLOCKED)된 구체적 사유와 향후 매수 승인 전환을 위한 재평가 조건을 분석해 줘", md)

    def test_06_data_provenance_fields_in_markdown(self):
        """6. Data Provenance 필드(출처, 봉 수, 품질) 마크다운 출력 검증"""
        dto = ScanResultDTO(
            stock_code="005930",
            stock_name="삼성전자",
            collected_at="2026-08-17 12:00:00 KST",
            daily_cho_recent2=[-1000, -2000],
            daily_adx_di_dominance="+DI: 30.0 / -DI: 15.0 (+DI우세)",
            intraday_cho_recent2=[5000, 8000],
            intraday_adx_di_dominance="+DI: 35.0 / -DI: 10.0 (ADX 45.0, +DI우세)",
            intraday_data_quality="🟢 VALID (40봉 정상 산출)",
            intraday_source="YFINANCE_15M (005930.KS)",
            intraday_row_count=40,
            intraday_last_timestamp="2026-08-14 15:30:00",
            technical_state="STRONG",
            technical_action="BUY_ALLOWED"
        )
        md = render_gems_markdown(dto)
        self.assertIn("• 일봉 Chaikin(13,26) 최근 2봉: [-1000, -2000]", md)
        self.assertIn("• 일봉 ADX +DI/-DI 우세방향: +DI: 30.0 / -DI: 15.0 (+DI우세)", md)
        self.assertIn("• 45m Chaikin(13,26) 최근 2봉: [5000, 8000]", md)
        self.assertIn("• 45m ADX +DI/-DI: +DI: 35.0 / -DI: 10.0 (ADX 45.0, +DI우세)", md)
        self.assertIn("• 45분봉 데이터 품질: 🟢 VALID (40봉 정상 산출) [출처: YFINANCE_15M (005930.KS) | 40봉]", md)

if __name__ == '__main__':
    unittest.main()
