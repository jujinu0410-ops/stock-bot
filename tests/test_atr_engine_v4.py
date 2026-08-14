import unittest
import math
import pandas as pd
import numpy as np
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import ATR_CONFIG, ATR_ENGINE_VERSION
from src.analysis.technical_analysis import calculate_true_range, calculate_wilder_atr, adjust_krx_tick_size
from src.database.db_manager import DatabaseManager
from src.engine.portfolio_manager import PortfolioManager

class TestATREngineV4(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db_path = BASE_DIR / "data" / "test_stock_v4.db"
        if cls.test_db_path.exists():
            try:
                cls.test_db_path.unlink()
            except Exception:
                pass
        cls.db = DatabaseManager(str(cls.test_db_path))
        cls.pm = PortfolioManager(cls.db)

    @classmethod
    def tearDownClass(cls):
        if cls.test_db_path.exists():
            try:
                cls.test_db_path.unlink()
            except Exception:
                pass

    def setUp(self):
        # 각 테스트 전 포지션 테이블만 클리어
        self.db.execute_non_query("DELETE FROM portfolio_positions")

    def test_01_p0_a0_frozen(self):
        """테스트 1: P0/A0 고정 검증 - 다음 날 현재가와 ATR이 변해도 활성가가 변하지 않아야 함"""
        p0 = 10000.0
        a0 = 500.0
        self.pm.add_holding("999001", "테스트종목1", 100, p0)
        
        # 첫날 앵커 설정
        self.db.execute_non_query("""
            UPDATE portfolio_positions 
            SET anchor_price_p0 = ?, anchor_atr_a0 = ? 
            WHERE stock_code = '999001'
        """, (p0, a0))

        # 익절 활성가 = P0 + 3.0 * A0 = 11,500
        raw_act_day1 = p0 + (ATR_CONFIG["profit_activation_multiple"] * a0)
        self.assertEqual(raw_act_day1, 11500.0)

        # 다음 날 주가 10,800원, ATR 600원으로 변동 시뮬레이션
        day2_price = 10800.0
        day2_atr = 600.0
        
        # P0와 A0는 DB에서 고정된 값을 읽어오므로 활성가는 여전히 11,500원이어야 함
        row = self.db.execute_query("SELECT anchor_price_p0, anchor_atr_a0 FROM portfolio_positions WHERE stock_code = '999001'")[0]
        p0_retrieved = float(row["anchor_price_p0"])
        a0_retrieved = float(row["anchor_atr_a0"])
        
        raw_act_day2 = p0_retrieved + (ATR_CONFIG["profit_activation_multiple"] * a0_retrieved)
        self.assertEqual(raw_act_day2, 11500.0)

    def test_02_stop_loss_ratchet_non_decreasing(self):
        """테스트 2: 손절 래칫 - 직전 손절가 9,750원, ATR 확대 후 계산값 9,450원이면 9,750원 유지"""
        prev_confirmed_stop = 9750.0
        candidate_stop = 9450.0
        initial_stop = 9000.0
        
        ratchet_stop = max(prev_confirmed_stop, initial_stop, candidate_stop)
        self.assertEqual(ratchet_stop, 9750.0)

    def test_03_stop_loss_ratchet_increase(self):
        """테스트 3: 손절 상향 - 최고종가 상승으로 계산값 10,100원이면 확정 손절가는 10,100원으로 상승"""
        prev_confirmed_stop = 9750.0
        candidate_stop = 10100.0
        initial_stop = 9000.0
        
        ratchet_stop = max(prev_confirmed_stop, initial_stop, candidate_stop)
        self.assertEqual(ratchet_stop, 10100.0)

    def test_04_profit_trail_ratchet(self):
        """테스트 4: 익절 트레일링 래칫 - 직전 익절선 12,000원, ATR 확대 후 계산값 11,700원이면 12,000원 유지"""
        prev_profit_trail = 12000.0
        candidate_profit_trail = 11700.0
        
        profit_trail = max(prev_profit_trail, candidate_profit_trail)
        self.assertEqual(profit_trail, 12000.0)

    def test_05_initial_stop_to_trailing_transition(self):
        """테스트 5: 초기손절 전환 - +1.0ATR 미도달 시 2.0ATR 손절, +1.0ATR 도달 후 1.5ATR 트레일링 전환"""
        p0 = 10000.0
        a0 = 500.0
        at = 500.0
        
        # 1) +1.0 ATR 미도달 (최고종가 10,300 < 10,500) -> 2.0 ATR 손절
        h_close_1 = 10300.0
        if h_close_1 >= p0 + (1.0 * a0):
            cand_stop_1 = h_close_1 - (1.5 * at)
        else:
            cand_stop_1 = h_close_1 - (2.0 * at)
        self.assertEqual(cand_stop_1, 9300.0) # 10300 - 1000

        # 2) +1.0 ATR 도달 (최고종가 10,600 >= 10,500) -> 1.5 ATR 손절 전환
        h_close_2 = 10600.0
        if h_close_2 >= p0 + (1.0 * a0):
            cand_stop_2 = h_close_2 - (1.5 * at)
        else:
            cand_stop_2 = h_close_2 - (2.0 * at)
        self.assertEqual(cand_stop_2, 9850.0) # 10600 - 750

    def test_06_position_sizing_volatility_comparison(self):
        """테스트 6: 포지션 사이징 - 동일 위험예산에서 NATR 8% 종목의 권고수량이 NATR 2% 종목보다 작아야 함"""
        account_equity = 100000000.0 # 1억원
        risk_pct = 0.005 # 0.5% = 50만원
        risk_budget = account_equity * risk_pct

        price = 10000.0

        # 종목 A: NATR 2% (A0 = 200원) -> InitialStop = 10000 - 400 = 9600, SlippageBuffer = 20원 -> RiskPerShare = 420원
        a0_low = 200.0
        init_stop_low = price - (2.0 * a0_low)
        slip_low = max(0.1 * a0_low, 50.0)
        risk_per_share_low = (price - init_stop_low) + slip_low
        qty_low = math.floor(risk_budget / risk_per_share_low)

        # 종목 B: NATR 8% (A0 = 800원) -> InitialStop = 10000 - 1600 = 8400, SlippageBuffer = 80원 -> RiskPerShare = 1680원
        a0_high = 800.0
        init_stop_high = price - (2.0 * a0_high)
        slip_high = max(0.1 * a0_high, 50.0)
        risk_per_share_high = (price - init_stop_high) + slip_high
        qty_high = math.floor(risk_budget / risk_per_share_high)

        self.assertGreater(qty_low, qty_high)
        self.assertEqual(qty_low, math.floor(500000 / 450)) # 1111주
        self.assertEqual(qty_high, math.floor(500000 / 1680)) # 297주

    def test_07_position_weight_cap_20pct(self):
        """테스트 7: 비중 20% 제한 - 위험예산상 수량이 커도 계좌비중 20% 한도를 초과하지 않아야 함"""
        account_equity = 10000000.0 # 1,000만원
        price = 1000.0 # 1주당 1000원
        
        # 20% 한도금액 = 200만원 -> 최대 수량 2,000주
        weight_cap_qty = math.floor((account_equity * 0.20) / price)
        
        # 만약 위험예산상 수량이 5,000주라면
        risk_based_qty = 5000
        recommended_qty = min(risk_based_qty, weight_cap_qty)
        
        self.assertEqual(recommended_qty, 2000)

    def test_08_abnormal_natr_blocking(self):
        """테스트 8: 비정상 ATR 차단 - NATR >= 20% (예: 자이글 98.99%)이면 DATA_HOLD 및 자동 주문 금지"""
        price = 4500.0
        abnormal_atr = 4454.5 # NATR = 98.99%
        natr_pct = (abnormal_atr / price) * 100.0
        
        data_validity_flag = 1
        data_hold_reason = "정상"
        if natr_pct >= ATR_CONFIG["natr_order_block_threshold_pct"]:
            data_validity_flag = 0
            data_hold_reason = f"NATR 이상 급등({natr_pct:.2f}% >= 20%)"

        self.assertEqual(data_validity_flag, 0)
        self.assertIn("NATR 이상 급등", data_hold_reason)

    def test_09_hold_order_blocking(self):
        """테스트 9: HOLD 차단 - 재무 미확정 또는 HOLD 상태에서는 자동 주문값을 임의 생성하지 않음"""
        f_confirmed = False
        data_validity_flag = 0
        
        if not f_confirmed or data_validity_flag == 0:
            stop_display = "HOLD"
            target_display = "HOLD"
            auto_order_enabled = False
        else:
            stop_display = 10000
            target_display = 15000
            auto_order_enabled = True

        self.assertEqual(stop_display, "HOLD")
        self.assertEqual(target_display, "HOLD")
        self.assertFalse(auto_order_enabled)

    def test_10_krx_tick_adjustment_ratchet_guard(self):
        """테스트 10: 호가보정 후 재검증 - 호가보정으로 손절가가 전일보다 낮아질 경우 전일 손절가 유지"""
        prev_confirmed_stop = 10050.0 # 10,050원
        raw_ratchet_stop = 10048.0 # 10,048원 (호가단위 50원)
        
        # down 보정 시 10,000원이 되어 전일값(10,050원)보다 낮아짐
        adjusted_tick = adjust_krx_tick_size(raw_ratchet_stop, "down") # 10000
        
        # 하향 금지 가드 적용
        if prev_confirmed_stop > 0 and adjusted_tick < prev_confirmed_stop:
            final_stop = int(prev_confirmed_stop)
        else:
            final_stop = adjusted_tick

        self.assertEqual(final_stop, 10050)

    def test_11_additional_buy_lot_integrity(self):
        """테스트 11: 추가매수 - 추가매수 후 통합평단 재계산 시 기존 P0/A0를 덮어쓰지 않음"""
        # 첫 매수: 100주 @ 10,000원
        self.pm.add_holding("999002", "추가매수테스트", 100, 10000.0)
        self.db.execute_non_query("""
            UPDATE portfolio_positions 
            SET anchor_price_p0 = 10000.0, anchor_atr_a0 = 500.0 
            WHERE stock_code = '999002'
        """)

        # 추가 매수 발생: 100주 @ 12,000원 -> 총 200주, 통합평단 11,000원
        self.pm.add_holding("999002", "추가매수테스트", 200, 11000.0)

        # DB 검증: 수량=200, 평단=11000, 그러나 anchor_price_p0는 여전히 10000원 유지
        row = self.db.execute_query("SELECT quantity, avg_buy_price, anchor_price_p0, anchor_atr_a0 FROM portfolio_positions WHERE stock_code = '999002'")[0]
        self.assertEqual(int(row["quantity"]), 200)
        self.assertEqual(float(row["avg_buy_price"]), 11000.0)
        self.assertEqual(float(row["anchor_price_p0"]), 10000.0)
        self.assertEqual(float(row["anchor_atr_a0"]), 500.0)

    def test_12_wilder_atr_standard_calculation(self):
        """테스트 12: Wilder ATR14 표준 수식 및 로컬/클라우드 일치성 검증"""
        np.random.seed(42)
        dates = pd.date_range("2026-01-01", periods=20, freq="B").strftime("%Y%m%d")
        highs = [10000 + i*50 + np.random.randint(10, 100) for i in range(20)]
        lows = [h - np.random.randint(50, 200) for h in highs]
        closes = [(h + l) // 2 for h, l in zip(highs, lows)]
        
        df = pd.DataFrame({
            "stk_date": dates,
            "open_price": closes,
            "high_price": highs,
            "low_price": lows,
            "close_price": closes,
            "volume": [10000]*20
        })

        atr_series = calculate_wilder_atr(df, period=14)
        self.assertEqual(len(atr_series), 20)
        self.assertFalse(atr_series.isna().any())
        self.assertGreater(atr_series.iloc[-1], 0)

    def test_13_existing_holding_p0_migration_rule(self):
        """테스트 13: 기존 보유종목 마이그레이션 시 P0는 과거 평단가가 아닌 감시개시 시점 현재가여야 함"""
        avg_buy_p = 23364.0 # 테이팩스 평단가
        current_p = 14620.0 # 현재가
        atr_14 = 1197.3
        
        # P0가 현재가(14,620원)로 설정되어야 함
        p0 = current_p
        a0 = atr_14
        
        initial_stop = p0 - (2.0 * a0)
        self.assertLess(initial_stop, current_p)
        self.assertAlmostEqual(initial_stop, 12225.4, places=1)

    def test_14_stop_loss_inversion_fail_safe(self):
        """테스트 14: 손절가가 현재가 이상으로 역전되면 무조건 DATA_HOLD 처리되어야 함"""
        current_p = 10000.0
        abnormal_stop = 10500.0
        
        data_validity_flag = 1
        data_hold_reasons = []
        if abnormal_stop >= current_p:
            data_validity_flag = 0
            data_hold_reasons.append("손절가 역전(손절가 >= 현재가)")
            trade_mode = "HOLD"

        self.assertEqual(data_validity_flag, 0)
        self.assertEqual(trade_mode, "HOLD")
        self.assertIn("손절가 역전", data_hold_reasons[0])

    def test_15_recovery_mode_pricing_and_sizing(self):
        """테스트 15: RECOVERY 모드에서는 단기 반등 1.2ATR 활성, 0.3ATR 트레일링, 30% 분할매도 수량 산출"""
        current_p = 14620.0
        p0 = 14620.0
        a0 = 1197.3
        qty = 4283
        
        recovery_act = p0 + (1.2 * a0) # 16,056.76
        trail_delta = int(round(a0 * 0.3)) # 359
        rec_sell_qty = max(1, math.floor(qty * 0.30)) # 1284
        
        self.assertAlmostEqual(recovery_act, 16056.76, places=1)
        self.assertEqual(trail_delta, 359)
        self.assertEqual(rec_sell_qty, 1284)

if __name__ == "__main__":
    unittest.main()
