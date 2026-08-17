import unittest
import json
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.analysis.technical_analysis import adjust_krx_tick_size
from src.core.dto import ScanResultDTO
from src.formatters.gems_formatter import render_gems_markdown, render_multi_gems_markdown, render_multi_gems_json
from scan_stock_for_gems import resolve_stock_code, scan_stock_dto, scan_stock_for_gems
from run_gems_scanner import process_stocks_to_dtos, process_stocks

class TestPhase0Phase1(unittest.TestCase):
    """
    Phase 0: KRX 호가단위 버그 수정 및 ETF 5원 호가단위 단위테스트
    Phase 1: ScanResultDTO, JSON 직렬화, Markdown Formatter 렌더링 파이프라인 단위테스트
    """

    # --- Phase 0 Tests: adjust_krx_tick_size ---

    def test_01_krx_tick_size_stock_boundaries(self):
        """Phase 0: 일반 주식 KRX 호가단위 구간별 정확성 검증 (경계값 및 조건 순서 버그 수정 확인)"""
        # < 2,000원: 1원 단위
        self.assertEqual(adjust_krx_tick_size(1500, "down"), 1500)
        self.assertEqual(adjust_krx_tick_size(1999.4, "down"), 1999)
        self.assertEqual(adjust_krx_tick_size(1999.4, "up"), 2000)

        # 2,000 ~ < 5,000원: 5원 단위
        self.assertEqual(adjust_krx_tick_size(2000, "down"), 2000)
        self.assertEqual(adjust_krx_tick_size(2003, "down"), 2000)
        self.assertEqual(adjust_krx_tick_size(2003, "up"), 2005)
        self.assertEqual(adjust_krx_tick_size(4999, "down"), 4995)

        # 5,000 ~ < 20,000원: 10원 단위
        self.assertEqual(adjust_krx_tick_size(5000, "down"), 5000)
        self.assertEqual(adjust_krx_tick_size(12345, "down"), 12340)
        self.assertEqual(adjust_krx_tick_size(12345, "up"), 12350)
        self.assertEqual(adjust_krx_tick_size(19999, "down"), 19990)

        # 20,000 ~ < 50,000원: 50원 단위 (기존 버그에서 20,000 미만 조건과 충돌했던 구간)
        self.assertEqual(adjust_krx_tick_size(20000, "down"), 20000)
        self.assertEqual(adjust_krx_tick_size(24450, "down"), 24450)
        self.assertEqual(adjust_krx_tick_size(24480, "down"), 24450)
        self.assertEqual(adjust_krx_tick_size(24480, "up"), 24500)
        self.assertEqual(adjust_krx_tick_size(49990, "down"), 49950)

        # 50,000 ~ < 200,000원: 100원 단위
        self.assertEqual(adjust_krx_tick_size(50000, "down"), 50000)
        self.assertEqual(adjust_krx_tick_size(78950, "down"), 78900)
        self.assertEqual(adjust_krx_tick_size(78950, "up"), 79000)
        self.assertEqual(adjust_krx_tick_size(199900, "down"), 199900)

        # 200,000 ~ < 500,000원: 500원 단위
        self.assertEqual(adjust_krx_tick_size(200000, "down"), 200000)
        self.assertEqual(adjust_krx_tick_size(250300, "down"), 250000)
        self.assertEqual(adjust_krx_tick_size(250300, "up"), 250500)

        # >= 500,000원: 1,000원 단위
        self.assertEqual(adjust_krx_tick_size(500000, "down"), 500000)
        self.assertEqual(adjust_krx_tick_size(684500, "down"), 684000)
        self.assertEqual(adjust_krx_tick_size(684500, "up"), 685000)

    def test_02_krx_tick_size_etf_5won(self):
        """Phase 0: ETF/ETN 전 가격대 5원 균일 호가단위 검증"""
        # 2,000원 미만 ETF
        self.assertEqual(adjust_krx_tick_size(1503, "down", is_etf=True), 1500)
        self.assertEqual(adjust_krx_tick_size(1503, "up", is_etf=True), 1505)

        # 20,000원 이상 ETF (일반 주식 50원 대신 5원 유지)
        self.assertEqual(adjust_krx_tick_size(24453, "down", is_etf=True), 24450)
        self.assertEqual(adjust_krx_tick_size(24453, "up", is_etf=True), 24455)

        # 100,000원 이상 ETF (일반 주식 100원 대신 5원 유지)
        self.assertEqual(adjust_krx_tick_size(105008, "down", is_etf=True), 105005)
        self.assertEqual(adjust_krx_tick_size(105008, "up", is_etf=True), 105010)

    def test_03_krx_tick_size_edge_cases(self):
        """Phase 0: 0 이하 가격 및 기본값 검증"""
        self.assertEqual(adjust_krx_tick_size(0, "down"), 0)
        self.assertEqual(adjust_krx_tick_size(-100, "down"), 0)

    # --- Phase 1 Tests: ScanResultDTO & Pipeline ---

    def test_04_scan_result_dto_serialization(self):
        """Phase 1: ScanResultDTO to_dict, to_json, from_dict, from_json 직렬화/역직렬화 검증"""
        dto = ScanResultDTO(
            stock_code="000490",
            stock_name="대동",
            collected_at="2026-08-17 11:30:00 KST",
            is_etf=False,
            current_price=13200,
            daily_change_pct=1.25,
            atr_14=650.0,
            atr_pct=4.92,
            t_score=75.0,
            t_raw=75.0,
            candidate_reference_price=13200,
            candidate_reference_atr=650.0,
            candidate_buy_price=12200,
            candidate_target_price=15150,
            candidate_stop_price=12200,
            buy_rebound_delta=325,
            sell_drop_delta=520,
            f_score=80.0,
            final_score=78.0,
            buy_approval="🔵 ON (트레일링/눌림목 분할매수 승인)",
            action_strategy="테스트 대응전략"
        )

        # to_dict 검증
        d = dto.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["stock_code"], "000490")
        self.assertEqual(d["candidate_reference_price"], 13200)

        # to_json 검증
        j_str = dto.to_json()
        self.assertIsInstance(j_str, str)
        parsed_j = json.loads(j_str)
        self.assertEqual(parsed_j["stock_name"], "대동")

        # from_dict & from_json 검증
        restored_from_dict = ScanResultDTO.from_dict(d)
        self.assertEqual(restored_from_dict.stock_code, dto.stock_code)
        self.assertEqual(restored_from_dict.candidate_buy_price, 12200)

        restored_from_json = ScanResultDTO.from_json(j_str)
        self.assertEqual(restored_from_json.stock_name, dto.stock_name)
        self.assertEqual(restored_from_json.final_score, 78.0)

    def test_05_gems_formatter_fidelity(self):
        """Phase 1: render_gems_markdown 렌더링 포맷 및 5대 섹션 무결성 검증"""
        dto = ScanResultDTO(
            stock_code="000490",
            stock_name="대동",
            collected_at="2026-08-17 11:30:00 KST",
            current_price=13200,
            daily_change_pct=1.20,
            atr_14=650.0,
            atr_pct=4.92,
            t_score=75.0,
            candidate_reference_price=13200,
            candidate_reference_atr=650.0,
            candidate_buy_price=12200,
            candidate_target_price=15150,
            candidate_stop_price=12200,
            buy_rebound_delta=325,
            sell_drop_delta=520,
            revenue=1400000000000.0,
            prev_revenue=1300000000000.0,
            operating_profit=65000000000.0,
            prev_operating_profit=50000000000.0,
            operating_cash_flow=45000000000.0,
            prev_operating_cash_flow=30000000000.0,
            debt_ratio=110.5,
            prev_debt_ratio=120.0,
            f_score=80.0,
            final_score=78.0,
            buy_approval="🔵 ON (트레일링/눌림목 분할매수 승인)",
            technical_action="BUY_ALLOWED",
            action_strategy="우수한 펀더멘탈과 기술추세를 갖춘 종목"
        )

        md = render_gems_markdown(dto)
        self.assertIn("🤖 [Gemini Gems 전용 정밀 진단 프롬프트 데이터]", md)
        self.assertIn("1. 📈 키움 REST & 실시간 시세", md)
        self.assertIn("2. ⏱️ 5단계 매매 대응전략 원자값 연동 지표", md)
        self.assertIn("3. ⚙️ ATR V4 트레일링 및 3단계 분할매매 시뮬레이션 파라미터 (PREVIEW ONLY)", md)
        self.assertIn("4. 🏢 OpenDART 2025년 공시 재무", md)
        self.assertIn("5. 🔭 Forward Order / Disclosure Evidence", md)
        self.assertIn("6. ⚖️ 100점 만점 가중 종합점수 & 5단계 매수 승인 최종 판정", md)
        self.assertIn("대동 (000490)", md)
        self.assertIn("13,200원", md)
        self.assertIn("12,200원", md)
        self.assertIn("15,150원", md)

    def test_06_multi_gems_pipeline(self):
        """Phase 1: DTO -> JSON -> Multi Markdown 렌더링 파이프라인 검증"""
        dtos = [
            ScanResultDTO(stock_code="000490", stock_name="대동", collected_at="2026-08-17", current_price=13200),
            ScanResultDTO(stock_code="004960", stock_name="한신공영", collected_at="2026-08-17", current_price=9500)
        ]

        # JSON 렌더링
        json_output = render_multi_gems_json(dtos)
        parsed = json.loads(json_output)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["stock_name"], "대동")
        self.assertEqual(parsed[1]["stock_name"], "한신공영")

        # Multi Markdown 렌더링
        multi_md = render_multi_gems_markdown(dtos)
        self.assertIn("관심 종목 2개 정밀 진단 통합 리포트", multi_md)
        self.assertIn("[종목 1/2: 대동 (000490)]", multi_md)
        self.assertIn("[종목 2/2: 한신공영 (004960)]", multi_md)

    def test_07_resolve_stock_code(self):
        """종목명/종목코드 변환 헬퍼 검증"""
        code, name = resolve_stock_code("삼성전자")
        self.assertEqual(code, "005930")
        self.assertEqual(name, "삼성전자")

        code_direct, name_direct = resolve_stock_code("000660")
        self.assertEqual(code_direct, "000660")

if __name__ == "__main__":
    unittest.main()
