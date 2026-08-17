import unittest
import os
import shutil
from src.analysis.technical_gate import TechnicalGate
from src.core.dto import ScanResultDTO
from src.formatters.gems_formatter import render_gems_markdown
from src.engine.risk_engine import ATRRiskEngine
from src.engine.portfolio_manager import PortfolioManager
from src.database.db_manager import DatabaseManager

class TestPhase3TechnicalGate(unittest.TestCase):
    """
    Phase 3: 45분봉 Technical Gate 검증 테스트 스위트
    - F/T 판정 검증 및 강등 (승격 불가)
    - 1차 신규매수, 2차 눌림목 추매, 3차 고점 돌파 추매, 보유종목 위험관리 게이트
    - Data Quality와 TechnicalState의 완전 분리
    - ATR Risk Engine 핵심 파라미터(P0/A0/Sfinal/TrailLine) 불변 검증
    """

    def setUp(self):
        self.db = DatabaseManager()
        self.pm = PortfolioManager(db_manager=self.db)
        self.pm.clear_all_holdings()

    def tearDown(self):
        self.pm.clear_all_holdings()

    def test_01_ft_off_plus_technical_strong_remains_buy_blocked(self):
        """1. F/T OFF + Technical STRONG ➔ 절대 ON 승격 불가, 여전히 BUY_BLOCKED"""
        state_res = TechnicalGate.evaluate_technical_state(
            data_quality="🟢 VALID",
            adx_14=28.0,
            plus_di=32.0,
            minus_di=14.0,
            obv_trend_str="📈 OBV(9) 매집 유지 (상승)",
            chaikin_val=15000,
            intraday_cho_recent2=[10000, 15000]
        )
        self.assertEqual(state_res["technical_state"], "STRONG")

        gate_res = TechnicalGate.evaluate_new_buy_gate(
            is_ft_approved=False, # F/T OFF
            data_quality="🟢 VALID",
            tech_state=state_res["technical_state"]
        )
        self.assertEqual(gate_res["technical_action"], "BUY_BLOCKED")
        self.assertFalse(gate_res["gate_passed"])

    def test_02_on_plus_strong_results_in_buy_allowed(self):
        """2. ON + STRONG ➔ BUY_ALLOWED (1차 50% 진입 승인)"""
        gate_res = TechnicalGate.evaluate_new_buy_gate(
            is_ft_approved=True,
            data_quality="🟢 VALID",
            tech_state="STRONG"
        )
        self.assertEqual(gate_res["technical_action"], "BUY_ALLOWED")
        self.assertTrue(gate_res["gate_passed"])

    def test_03_on_plus_weak_results_in_buy_wait(self):
        """3. ON + WEAK ➔ BUY_WAIT (45m 수급 약세로 신규진입 보류)"""
        # WEAK 복합조건: DMI Bear + OBV Dead + CHO Outflow
        state_res = TechnicalGate.evaluate_technical_state(
            data_quality="🟢 VALID",
            adx_14=25.0,
            plus_di=12.0,
            minus_di=30.0,
            obv_trend_str="📉 OBV(9) 데드크로스 (이탈)",
            is_obv_dead=True,
            chaikin_val=-8000,
            intraday_cho_recent2=[-5000, -8000],
            is_cho_outflow=True
        )
        self.assertEqual(state_res["technical_state"], "WEAK")

        gate_res = TechnicalGate.evaluate_new_buy_gate(
            is_ft_approved=True,
            data_quality="🟢 VALID",
            tech_state=state_res["technical_state"]
        )
        self.assertEqual(gate_res["technical_action"], "BUY_WAIT")
        self.assertFalse(gate_res["gate_passed"])

    def test_04_on_plus_damaged_results_in_buy_blocked(self):
        """4. ON + DAMAGED ➔ BUY_BLOCKED (복합악화 + 일목 구름 하단 이탈 시 전면 차단)"""
        state_res = TechnicalGate.evaluate_technical_state(
            data_quality="🟢 VALID",
            adx_14=30.0,
            plus_di=10.0,
            minus_di=35.0,
            obv_trend_str="📉 OBV(9) 데드크로스 (이탈)",
            is_obv_dead=True,
            chaikin_val=-12000,
            intraday_cho_recent2=[-6000, -12000],
            is_cho_outflow=True,
            is_cloud_breakdown=True # 구름 하단 이탈
        )
        self.assertEqual(state_res["technical_state"], "DAMAGED")

        gate_res = TechnicalGate.evaluate_new_buy_gate(
            is_ft_approved=True,
            data_quality="🟢 VALID",
            tech_state=state_res["technical_state"]
        )
        self.assertEqual(gate_res["technical_action"], "BUY_BLOCKED")
        self.assertFalse(gate_res["gate_passed"])

    def test_05_on_plus_invalid_data_results_in_buy_wait_data(self):
        """5. ON + INVALID ➔ BUY_WAIT_DATA (데이터 수집 대기)"""
        gate_res = TechnicalGate.evaluate_new_buy_gate(
            is_ft_approved=True,
            data_quality="🔴 INVALID (45분봉 데이터 미수집/오류)",
            tech_state="UNKNOWN"
        )
        self.assertEqual(gate_res["technical_action"], "BUY_WAIT_DATA")
        self.assertFalse(gate_res["gate_passed"])

    def test_06_second_buy_atr_rebound_with_weak_is_add_blocked(self):
        """6. 2차 ATR 반등조건 충족 + WEAK ➔ ADD_BLOCKED (수급 훼손 시 추매 차단)"""
        gate_res = TechnicalGate.evaluate_second_buy_gate(
            is_rebound_triggered=True,
            data_quality="🟢 VALID",
            tech_state="WEAK"
        )
        self.assertEqual(gate_res["technical_action"], "ADD_BLOCKED")
        self.assertFalse(gate_res["gate_passed"])

    def test_07_second_buy_atr_rebound_with_strong_is_add_allowed(self):
        """7. 2차 ATR 반등조건 충족 + STRONG ➔ ADD_ALLOWED (2차 30% 추매 승인)"""
        gate_res = TechnicalGate.evaluate_second_buy_gate(
            is_rebound_triggered=True,
            data_quality="🟢 VALID",
            tech_state="STRONG"
        )
        self.assertEqual(gate_res["technical_action"], "ADD_ALLOWED")
        self.assertTrue(gate_res["gate_passed"])

    def test_08_normal_holding_with_weak_is_hold_not_sell(self):
        """8. NORMAL 보유 + WEAK ➔ 자동 손절 아님, 추매 금지 + HOLD (관망)"""
        holding_gate = TechnicalGate.evaluate_holding_gate(
            trade_mode="NORMAL",
            data_quality="🟢 VALID",
            tech_state="WEAK",
            is_cloud_breakdown=False,
            consecutive_weak_bars=1
        )
        self.assertEqual(holding_gate["technical_action"], "HOLD")
        self.assertFalse(holding_gate["preemptive_warning"])

    def test_09_composite_deterioration_with_cloud_breakdown_is_reduce_consider(self):
        """9. 복합악화 + 일목 구름 하향이탈 ➔ REDUCE_CONSIDER / 지속 악화 시 PREEMPTIVE_REDUCE_WARNING"""
        # (1) 복합악화 지속 2봉 이상
        holding_warn = TechnicalGate.evaluate_holding_gate(
            trade_mode="NORMAL",
            data_quality="🟢 VALID",
            tech_state="WEAK",
            is_cloud_breakdown=False,
            consecutive_weak_bars=2
        )
        self.assertEqual(holding_warn["technical_action"], "PREEMPTIVE_REDUCE_WARNING")
        self.assertTrue(holding_warn["preemptive_warning"])

        # (2) 복합악화 + 일목 이탈
        holding_reduce = TechnicalGate.evaluate_holding_gate(
            trade_mode="NORMAL",
            data_quality="🟢 VALID",
            tech_state="DAMAGED",
            is_cloud_breakdown=True
        )
        self.assertEqual(holding_reduce["technical_action"], "REDUCE_CONSIDER")
        self.assertTrue(holding_reduce["preemptive_warning"])

    def test_10_technical_gate_does_not_modify_risk_engine_parameters(self):
        """10. Technical Gate가 P0, A0, Sfinal, TrailLine을 절대 변경하지 않음을 검증"""
        p0 = 10000.0
        a0 = 500.0
        at = 500.0
        current_price = 11600.0
        highest_close = 11600.0
        highest_high = 11800.0
        s_initial = 10000.0 - 1.5 * 500.0 # 9,250원

        # 정상 ATR Risk 연산
        risk_before = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=at, current_price=current_price,
            highest_close=highest_close, highest_high=highest_high,
            prev_confirmed_stop=9250.0, prev_profit_trail=0.0
        )
        
        # Technical Gate의 REDUCE_CONSIDER 판정 호출
        holding_gate = TechnicalGate.evaluate_holding_gate(
            trade_mode="NORMAL",
            data_quality="🟢 VALID",
            tech_state="DAMAGED",
            is_cloud_breakdown=True
        )
        self.assertEqual(holding_gate["technical_action"], "REDUCE_CONSIDER")

        # Technical Gate 호출 후에도 ATR Risk Engine의 Stop 선과 계산값은 완벽히 동일
        risk_after = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=at, current_price=current_price,
            highest_close=highest_close, highest_high=highest_high,
            prev_confirmed_stop=9250.0, prev_profit_trail=0.0
        )
        self.assertEqual(risk_before["ratchet_stop"], risk_after["ratchet_stop"])
        self.assertEqual(risk_before["profit_trail"], risk_after["profit_trail"])
        self.assertEqual(risk_before["profit_activation_status"], risk_after["profit_activation_status"])
        self.assertEqual(risk_before["effective_exit_line"], risk_after["effective_exit_line"])

    def test_11_separation_of_data_quality_and_technical_state(self):
        """11. Data Quality(VALID/PARTIAL/INVALID)와 TechnicalState(STRONG/WEAK/...)가 엄격히 분리됨을 검증"""
        dto = ScanResultDTO(
            stock_code="005930",
            stock_name="삼성전자",
            collected_at="2026-08-17 12:00:00 KST",
            intraday_data_quality="🟡 PARTIAL (일부 지표 계산용 분봉 데이터 부족)",
            technical_state="UNKNOWN",
            technical_action="BUY_WAIT_DATA",
            buy_approval="🔵 ON (트레일링/눌림목 분할매수 승인)"
        )
        d = dto.to_dict()
        self.assertIn("intraday_data_quality", d)
        self.assertIn("technical_state", d)
        self.assertIn("technical_action", d)
        self.assertNotEqual(d["intraday_data_quality"], d["technical_state"])

if __name__ == "__main__":
    unittest.main()
