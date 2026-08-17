import unittest
import math
from src.engine.risk_engine import ATRRiskEngine
from src.analysis.technical_analysis import adjust_krx_tick_size
from src.database.db_manager import DatabaseManager
from src.engine.portfolio_manager import PortfolioManager

class TestPhase2RiskEngine(unittest.TestCase):
    """
    Phase 2: ATR Risk Engine Single Source of Truth & Position Lifecycle Unit Tests
    """

    def setUp(self):
        self.db = DatabaseManager()
        self.pm = PortfolioManager(db_manager=self.db)
        self.pm.clear_all_holdings()

    def tearDown(self):
        self.pm.clear_all_holdings()

    def test_01_candidate_preview_isolation(self):
        """1. 매수 전 감시 후보선 분리 검증 (PREVIEW_ONLY 상태 및 P0/A0 비영속)"""
        current_price = 10000.0
        atr_14 = 500.0

        preview = ATRRiskEngine.calculate_candidate_preview(current_price, atr_14, is_etf=False)

        self.assertEqual(preview["lifecycle_status"], "PREVIEW_ONLY")
        self.assertEqual(preview["candidate_reference_price"], 10000)
        self.assertEqual(preview["candidate_reference_atr"], 500.0)
        # 10000 - 1.5 * 500 = 9250
        self.assertEqual(preview["candidate_buy_price"], 9250)
        # 10000 - 1.5 * 500 = 9250 (1.5 ATR 초기 손절)
        self.assertEqual(preview["candidate_stop_price"], 9250)
        # 10000 + 3.0 * 500 = 11500 (3.0 ATR 목표가)
        self.assertEqual(preview["candidate_target_price"], 11500)
        # 반등폭 0.5 ATR = 250, 하락폭 0.8 ATR = 400
        self.assertEqual(preview["buy_rebound_delta"], 250)
        self.assertEqual(preview["sell_drop_delta"], 400)

    def test_02_initial_stop_1_5_atr(self):
        """2. 운영 초기손절 검증: Entry - 1.5 * A0 단일 표준 규칙"""
        p0 = 20000.0
        a0 = 1000.0
        at = 1000.0
        current_price = 20000.0
        highest_close = 20000.0
        highest_high = 20000.0

        risk = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=at,
            current_price=current_price,
            highest_close=highest_close,
            highest_high=highest_high,
            trade_mode="NORMAL"
        )

        # S0 = 20000 - 1.5 * 1000 = 18500
        self.assertEqual(risk["raw_initial_stop"], 18500.0)
        self.assertEqual(risk["ratchet_stop"], 18500.0)
        self.assertEqual(risk["kiwoom_stop_tick"], 18500)

    def test_03_ratchet_stop_progression_and_non_decreasing(self):
        """3. 장마감 후 손절 래칫 검증: S_new = H_close - 1.5 * At ➔ 절대 하향 금지 max(S_prev, S0, S_new)"""
        p0 = 10000.0
        a0 = 500.0
        at = 500.0

        # Day 1: 진입가 10,000원 -> S0 = 10000 - 750 = 9,250원
        risk_day1 = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=at, current_price=10000.0,
            highest_close=10000.0, highest_high=10000.0,
            prev_confirmed_stop=0.0
        )
        self.assertEqual(risk_day1["ratchet_stop"], 9250.0)

        # Day 2: 종가 10,800원 상승 -> S_new = 10800 - 750 = 10,050원 (상향 갱신)
        risk_day2 = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=at, current_price=10800.0,
            highest_close=10800.0, highest_high=10900.0,
            prev_confirmed_stop=9250.0
        )
        self.assertEqual(risk_day2["ratchet_stop"], 10050.0)

        # Day 3: 종가 10,200원으로 조정 (최고종가 10,800 유지, 변동성 At=600으로 확대 -> candidate_stop = 10800 - 900 = 9900원)
        # 그러나 기존 확정손절가 10,050원이 보존되어 10,050원 유지
        risk_day3 = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=600.0, current_price=10200.0,
            highest_close=10800.0, highest_high=10900.0,
            prev_confirmed_stop=10050.0
        )
        self.assertEqual(risk_day3["ratchet_stop"], 10050.0)

    def test_04_normal_profit_trail_activation_and_tracking(self):
        """4. 트레일링 익절 검증: P0 + 3.0 * A0 도달 시 ACTIVE ➔ H_high - 0.8 * At 추적"""
        p0 = 10000.0
        a0 = 500.0
        at = 500.0
        activation_target = 10000 + 3.0 * 500 # 11,500원

        # 1) 활성화 미도달 (최고가 11,400 < 11,500) -> INACTIVE
        risk_inact = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=at, current_price=11400.0,
            highest_close=11300.0, highest_high=11400.0,
            prev_activation_status="INACTIVE"
        )
        self.assertEqual(risk_inact["profit_activation_status"], "INACTIVE")
        self.assertEqual(risk_inact["profit_trail"], 0.0)
        self.assertEqual(risk_inact["kiwoom_target_tick"], 11500)

        # 2) 활성화 도달 (최고가 12,000 >= 11,500) -> ACTIVE ➔ TrailLine = 12000 - 0.8 * 500 = 11,600원
        risk_act = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=at, current_price=11800.0,
            highest_close=11800.0, highest_high=12000.0,
            prev_activation_status="INACTIVE"
        )
        self.assertEqual(risk_act["profit_activation_status"], "ACTIVE")
        self.assertEqual(risk_act["profit_trail"], 11600.0)
        self.assertEqual(risk_act["kiwoom_target_tick"], 11600)
        # 유효 청산선 = max(손절가 11800-750=11050, 익절선 11600) = 11600원
        self.assertEqual(risk_act["effective_exit_line"], 11600.0)

    def test_05_recovery_mode_explicit_trigger(self):
        """5. RECOVERY 모드 검증: 평가손실만으로 자동 진입하지 않고 명시적 전략 모드 시 1.2 ATR / 0.3 ATR 적용"""
        p0 = 10000.0
        a0 = 500.0
        at = 500.0
        current_price = 8400.0 # -16% 평가손실

        # 1) NORMAL 모드: 자동 RECOVERY 되지 않고 정상 파라미터(3.0 ATR 익절선 11,500원) 유지
        risk_normal = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=at, current_price=current_price,
            highest_close=current_price, highest_high=current_price,
            trade_mode="NORMAL"
        )
        self.assertEqual(risk_normal["trade_mode"], "NORMAL")
        self.assertEqual(risk_normal["raw_profit_activation"], 11500.0)

        # 2) 명시적 RECOVERY 모드 지정 시: 1.2 ATR 활성선(10,000 + 600 = 10,600원) 및 0.3 ATR 트레일폭(150원) 적용
        risk_recovery = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=at, current_price=current_price,
            highest_close=current_price, highest_high=current_price,
            trade_mode="RECOVERY"
        )
        self.assertEqual(risk_recovery["trade_mode"], "RECOVERY")
        self.assertEqual(risk_recovery["raw_profit_activation"], 10600.0)
        self.assertEqual(risk_recovery["profit_trail_delta"], 150)

    def test_06_position_lifecycle_db_persistence(self):
        """6. 포지션 라이프사이클 및 분할매수 3단계(50%->30%->20%->청산) DB 영속화 검증"""
        code = "000490"
        name = "대동"

        # 1) 1차 최초진입 (50%, Stage 1, POSITION_OPEN)
        self.pm.add_holding(code, name, 100, 10000, entry_stage=1, lifecycle_status="POSITION_OPEN")
        rows = self.db.execute_query("SELECT entry_stage, lifecycle_status, anchor_price_p0, quantity FROM portfolio_positions WHERE stock_code = ?", (code,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_stage"], 1)
        self.assertEqual(rows[0]["lifecycle_status"], "POSITION_OPEN")
        self.assertEqual(rows[0]["anchor_price_p0"], 10000.0)
        self.assertEqual(rows[0]["quantity"], 100)

        # 2) 2차 추가매수 (30%, Stage 2, 누적 160주)
        self.pm.add_holding(code, name, 160, 9800, entry_stage=2, lifecycle_status="POSITION_OPEN")
        rows2 = self.db.execute_query("SELECT entry_stage, lifecycle_status, anchor_price_p0, quantity FROM portfolio_positions WHERE stock_code = ?", (code,))
        self.assertEqual(rows2[0]["entry_stage"], 2)
        self.assertEqual(rows2[0]["anchor_price_p0"], 10000.0) # 최초 P0 불변 영속
        self.assertEqual(rows2[0]["quantity"], 160)

        # 3) 3차 불타기매수 (20%, Stage 3, 누적 200주)
        self.pm.add_holding(code, name, 200, 10100, entry_stage=3, lifecycle_status="POSITION_OPEN")
        rows3 = self.db.execute_query("SELECT entry_stage, lifecycle_status, anchor_price_p0, quantity FROM portfolio_positions WHERE stock_code = ?", (code,))
        self.assertEqual(rows3[0]["entry_stage"], 3)
        self.assertEqual(rows3[0]["anchor_price_p0"], 10000.0) # 최초 P0 불변 영속
        self.assertEqual(rows3[0]["quantity"], 200)

        # 4) 전량 청산 (수량 0 -> CLOSED)
        self.pm.add_holding(code, name, 0, 10100, entry_stage=3)
        rows4 = self.db.execute_query("SELECT lifecycle_status, quantity FROM portfolio_positions WHERE stock_code = ?", (code,))
        self.assertEqual(rows4[0]["lifecycle_status"], "CLOSED")
        self.assertEqual(rows4[0]["quantity"], 0)

    def test_07_position_sizing_and_weight_cap(self):
        """7. 포지션 사이징 위험예산(0.5%) 및 단일종목 20% 한도 산출 검증"""
        total_equity = 100000000.0 # 1억원
        current_price = 10000.0
        p0 = 10000.0
        a0 = 500.0 # S0 = 10000 - 750 = 9250원, Slippage = 50원 -> RiskPerShare = 750 + 50 = 800원
        # RiskBudget = 1억 * 0.5% = 500,000원 -> TargetQty = floor(500000 / 800) = 625주
        # WeightCap = 1억 * 20% / 10000 = 2,000주
        # FinalRiskTargetQty = min(625, 2000) = 625주

        risk = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=500.0, current_price=current_price,
            highest_close=current_price, highest_high=current_price,
            total_equity=total_equity, current_qty=1000
        )

        self.assertEqual(risk["risk_target_qty"], 625)
        self.assertEqual(risk["weight_cap_qty"], 2000)
        self.assertEqual(risk["final_risk_target_qty"], 625)
        self.assertEqual(risk["excess_qty"], 375) # 1000 - 625 = 375주 초과
        self.assertEqual(risk["weight_excess_qty"], 0)

    def test_08_partial_entry_weighted_p0_and_immutable_a0(self):
        """8. 1차 진입 부분체결 여러 건에 대한 P0 체결가중평균과 A0 불변 검증"""
        code = "005930"
        name = "삼성전자"
        
        # 1차 분할체결 1: 30주 @ 70,000원
        lot1_qty, lot1_p = 30, 70000.0
        # 1차 분할체결 2: 20주 @ 71,000원
        lot2_qty, lot2_p = 20, 71000.0
        total_qty = lot1_qty + lot2_qty # 50주
        weighted_avg_p0 = ((lot1_qty * lot1_p) + (lot2_qty * lot2_p)) / total_qty # 70,400원
        fixed_a0 = 1500.0 # 진입 당시 1D_COMPLETED ATR14

        # 1차 완성 후 포지션 등록
        self.pm.add_holding(code, name, total_qty, weighted_avg_p0, entry_stage=1, lifecycle_status="POSITION_OPEN")
        
        # A0 영속 등록
        self.db.execute_non_query("UPDATE portfolio_positions SET anchor_atr_a0 = ? WHERE stock_code = ?", (fixed_a0, code))
        
        # 2차 추매(30주 @ 68,000원) 후에도 P0와 A0가 변경되지 않고 유지되는지 검증
        self.pm.add_holding(code, name, 80, 69500.0, entry_stage=2, lifecycle_status="POSITION_OPEN")
        
        row = self.db.execute_query("SELECT anchor_price_p0, anchor_atr_a0, entry_stage FROM portfolio_positions WHERE stock_code = ?", (code,))[0]
        self.assertEqual(row["anchor_price_p0"], 70400.0) # 1차 가중평균 체결가 불변
        self.assertEqual(row["anchor_atr_a0"], 1500.0)   # 최초 A0 불변
        self.assertEqual(row["entry_stage"], 2)

    def test_09_hhigh_persistence_and_tprev_non_decreasing(self):
        """9. Hhigh 활성 후 최고가 상향 승계 및 Tprev 후퇴 방지 DB 시나리오 검증"""
        p0 = 10000.0
        a0 = 500.0
        activation_target = 10000.0 + 3.0 * 500.0 # 11,500원

        # Day 1: 최고가 12,000원 도달 (ACTIVE 전환, At=500 -> TrailLine = 12000 - 0.8*500 = 11,600원)
        risk_day1 = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=500.0, current_price=11800.0,
            highest_close=11800.0, highest_high=12000.0,
            prev_profit_trail=0.0, prev_activation_status="INACTIVE"
        )
        self.assertEqual(risk_day1["profit_activation_status"], "ACTIVE")
        self.assertEqual(risk_day1["profit_trail"], 11600.0)

        # Day 2: 주가 11,400원으로 하락 및 변동성 At=600으로 급증 (최고가 12,000원 보존)
        # 신규 계산값: 12000 - 0.8*600 = 11,520원
        # 그러나 직전 확정 익절선(prev_profit_trail = 11,600원)이 보존되어 11,600원 유지
        risk_day2 = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=600.0, current_price=11400.0,
            highest_close=11800.0, highest_high=12000.0,
            prev_profit_trail=11600.0, prev_activation_status="ACTIVE"
        )
        self.assertEqual(risk_day2["profit_activation_status"], "ACTIVE")
        self.assertEqual(risk_day2["profit_trail"], 11600.0) # 후퇴 금지

        # Day 3: 주가 12,500원으로 신고가 갱신 (H_high = 12,500원, At=500)
        # 신규 계산값: 12500 - 0.8*500 = 12,100원
        # 상향 갱신: max(11600, 12100) = 12,100원
        risk_day3 = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=500.0, current_price=12400.0,
            highest_close=12400.0, highest_high=12500.0,
            prev_profit_trail=11600.0, prev_activation_status="ACTIVE"
        )
        self.assertEqual(risk_day3["profit_activation_status"], "ACTIVE")
        self.assertEqual(risk_day3["profit_trail"], 12100.0) # 상향 승계 성공

    def test_10_output_safety_patch_off_stock(self):
        """10. Phase 2.2 Output Safety Patch: OFF 종목 주문금지 및 Gemini 질문 자동 변경 검증"""
        from src.core.dto import ScanResultDTO
        from src.formatters.gems_formatter import render_gems_markdown

        dto_off = ScanResultDTO(
            stock_code="000490",
            stock_name="대동",
            collected_at="2026-08-17 12:00:00 KST",
            current_price=8300,
            daily_change_pct=-2.12,
            atr_14=703.0,
            atr_pct=8.47,
            t_score=78.0,
            candidate_reference_price=8300,
            candidate_reference_atr=703.0,
            candidate_buy_price=7240,
            candidate_target_price=10420,
            candidate_stop_price=7240,
            buy_rebound_delta=352,
            sell_drop_delta=563,
            intraday_data_quality="🟡 PARTIAL (일부 지표 계산용 분봉 데이터 부족)",
            technical_state="UNKNOWN",
            technical_action="BUY_BLOCKED",
            buy_approval="🔴 OFF (매수 금지/관망)"
        )

        md = render_gems_markdown(dto_off)
        # 1) 현재 실행 가능 주문 = NONE (BUY_BLOCKED)
        self.assertIn("• 현재 실행 가능 주문: 🔴 NONE (현재 매수 금지 / 주문 입력 불가 - BUY_BLOCKED)", md)
        # 2) REFERENCE ONLY / 실제 주문 입력 금지
        self.assertIn("REFERENCE ONLY / 실제 주문 입력 금지", md)
        self.assertIn("[3단계 분할매수 가이드 (참고용)]", md)
        # 3) 3차 상승확인 추매 명칭
        self.assertIn("3차 상승확인 추매", md)
        # 4) 45분봉 Technical Gate & State
        self.assertIn("• 45분봉 Technical State: UNKNOWN", md)
        self.assertIn("• 45분봉 Technical Gate: BUY_BLOCKED", md)
        self.assertIn("• 45분봉 데이터 품질: 🟡 PARTIAL (일부 지표 계산용 분봉 데이터 부족)", md)
        # 5) Gemini 사용 질문 자동 변경 (매수 차단 사유 및 재평가 조건 분석)
        self.assertIn("이 종목이 매수 차단(BUY_BLOCKED)된 구체적 사유와 향후 매수 승인 전환을 위한 재평가 조건을 분석해 줘", md)
        self.assertNotIn("키움 트레일링 매수/매도 설정가와 수량 비중 가이드를 요약해 줘", md)

    def test_11_output_safety_patch_on_stock(self):
        """11. Phase 2.2 Output Safety Patch: ON 종목 주문승인 및 Gemini 설정 요약 요청 검증"""
        from src.core.dto import ScanResultDTO
        from src.formatters.gems_formatter import render_gems_markdown

        dto_on = ScanResultDTO(
            stock_code="005930",
            stock_name="삼성전자",
            collected_at="2026-08-17 12:00:00 KST",
            current_price=75000,
            daily_change_pct=1.5,
            atr_14=1500.0,
            atr_pct=2.0,
            t_score=85.0,
            f_score=80.0,
            final_score=82.0,
            candidate_reference_price=75000,
            candidate_reference_atr=1500.0,
            candidate_buy_price=72750,
            candidate_target_price=79500,
            candidate_stop_price=72750,
            buy_rebound_delta=750,
            sell_drop_delta=1200,
            intraday_data_quality="🟢 VALID (45분봉 전 지표 정상 산출)",
            technical_state="STRONG",
            technical_action="BUY_ALLOWED",
            technical_gate_summary="+DI 우위 / OBV 매집 / CHO 유입",
            buy_approval="🔵 ON (트레일링/눌림목 분할매수 승인)"
        )

        md = render_gems_markdown(dto_on)
        # 1) 현재 실행 가능 주문 = 1차 진입(50%) 승인
        self.assertIn("• 현재 실행 가능 주문: 🟢 1차 진입(50%) 주문 승인 (Technical Gate: BUY_ALLOWED 통과)", md)
        self.assertIn("[3단계 분할매수 가이드 (50% / 30% / 20%)]", md)
        # 2) 3차 상승확인 추매 명칭
        self.assertIn("3차 상승확인 추매 (20%)", md)
        # 3) 45분봉 Technical Gate & State
        self.assertIn("• 45분봉 Technical State: STRONG (+DI 우위 / OBV 매집 / CHO 유입)", md)
        self.assertIn("• 45분봉 Technical Gate: BUY_ALLOWED", md)
        # 4) Gemini 사용 질문 (키움 트레일링 매수/매도 설정가 요약)
        self.assertIn("이 종목의 키움 트레일링 매수/매도 설정가와 수량 비중 가이드를 요약해 줘", md)
        self.assertNotIn("매수 비승인(OFF)된 구체적 사유", md)

if __name__ == "__main__":
    unittest.main()
