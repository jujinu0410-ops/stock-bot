import unittest
from unittest.mock import patch
from pathlib import Path
import sys
from datetime import datetime
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.database.db_manager import DatabaseManager
from src.engine.portfolio_manager import PortfolioManager
from src.engine.risk_engine import ATRRiskEngine
from src.notifications.mobile_renderer_v2 import (
    generate_mobile_html_report_v2,
    is_meaningful_action_item
)
import main


def _normal_intraday_response(base_price: float):
    """외부 I/O 없이 정상 15분봉 분석 경로를 유지하는 결정적 OHLCV fixture."""
    index = pd.date_range("2026-08-18 09:00:00", periods=72, freq="15min")
    closes = [base_price + (i * 10.0) for i in range(len(index))]
    return (
        pd.DataFrame(
            {
                "Open": closes,
                "High": [price + 30.0 for price in closes],
                "Low": [price - 30.0 for price in closes],
                "Close": closes,
                "Volume": [1000 + i for i in range(len(index))],
            },
            index=index,
        ),
        "TEST_FIXTURE_15M",
        "NONE",
    )


class TestV4AnchorRatchetIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db_path = BASE_DIR / "data" / "test_v4_anchor_ratchet.db"
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
        self.db.execute_non_query("DELETE FROM portfolio_positions")
        self.db.execute_non_query("DELETE FROM stock_info")
        self.db.execute_non_query("DELETE FROM kiwoom_daily")

    def _insert_daily_bars(self, code: str, base_price: float, count: int = 15, daily_step: float = 0.0):
        for i in range(count):
            d_str = f"202608{i+1:02d}"
            c_p = base_price + (i * daily_step)
            h_p = c_p + 200.0
            l_p = c_p - 200.0
            o_p = c_p
            self.db.execute_non_query("""
                INSERT OR REPLACE INTO kiwoom_daily (stock_code, stk_date, open_price, high_price, low_price, close_price, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (code, d_str, o_p, h_p, l_p, c_p, 100000))

    def test_01_hanshin_and_plus_anchor_continuity(self):
        """테스트 1: 한신공영(004960) 및 PLUS고배당주(105840)의 8월 18일 -> 19일 P0/A0/cycle_id 영속 승계 및 손절선 불변성 검증"""
        # 1. 한신공영 초기 8월 18일 상태 주입
        hs_code = "004960"
        hs_name = "한신공영"
        hs_p0 = 8430.0
        hs_a0 = 306.7
        hs_cycle_id = f"{hs_code}_20260818_V4"
        hs_created_at = "2026-08-18 09:00:00"
        hs_prev_stop = 7810.0
        
        self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (hs_code, hs_name))
        self.pm.add_holding(hs_code, hs_name, 100, 8430.0)
        self.db.execute_non_query("""
            UPDATE portfolio_positions SET
                anchor_price_p0 = ?, anchor_atr_a0 = ?, position_cycle_id = ?,
                anchor_created_at = ?, previous_confirmed_stop = ?, confirmed_stop_price = ?,
                highest_close = 8430.0, highest_intraday = 8500.0
            WHERE stock_code = ?
        """, (hs_p0, hs_a0, hs_cycle_id, hs_created_at, hs_prev_stop, hs_prev_stop, hs_code))
        
        self._insert_daily_bars(hs_code, base_price=8400.0, count=15)

        # 2. PLUS고배당주 초기 8월 18일 상태 주입
        plus_code = "105840"
        plus_name = "PLUS고배당주"
        plus_p0 = 15200.0
        plus_a0 = 250.0
        plus_cycle_id = f"{plus_code}_20260818_V4"
        plus_created_at = "2026-08-18 09:00:00"
        plus_prev_stop = 14700.0

        self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (plus_code, plus_name))
        self.pm.add_holding(plus_code, plus_name, 50, 15200.0)
        self.db.execute_non_query("""
            UPDATE portfolio_positions SET
                anchor_price_p0 = ?, anchor_atr_a0 = ?, position_cycle_id = ?,
                anchor_created_at = ?, previous_confirmed_stop = ?, confirmed_stop_price = ?,
                highest_close = 15200.0, highest_intraday = 15300.0
            WHERE stock_code = ?
        """, (plus_p0, plus_a0, plus_cycle_id, plus_created_at, plus_prev_stop, plus_prev_stop, plus_code))

        self._insert_daily_bars(plus_code, base_price=15200.0, count=15)

        # 3. 8월 19일 평가 수행
        with patch(
            "src.analysis.intraday_analysis.Intraday45mAnalyzer.fetch_canonical_15m_data",
            return_value=_normal_intraday_response(10000.0),
        ):
            eval_list = self.pm.get_held_portfolio_status()
        self.assertEqual(len(eval_list), 2)

        eval_map = {x["stock_code"]: x for x in eval_list}
        
        # 한신공영 단언
        hs_eval = eval_map[hs_code]
        self.assertEqual(hs_eval["anchor_price_p0"], int(round(hs_p0)))
        self.assertEqual(hs_eval["anchor_atr_a0"], round(hs_a0, 1))
        self.assertEqual(hs_eval["position_cycle_id"], hs_cycle_id)
        self.assertFalse(hs_eval["is_migrated_anchor"])
        self.assertGreaterEqual(hs_eval["confirmed_stop_price"], hs_prev_stop)
        # 익절 활성가: P0 + 3.0 * A0 = 8430 + 3 * 306.7 = 9350원
        self.assertEqual(hs_eval["profit_activation_price"], int(round(hs_p0 + 3.0 * hs_a0)))

        # PLUS고배당주 단언
        plus_eval = eval_map[plus_code]
        self.assertEqual(plus_eval["anchor_price_p0"], int(round(plus_p0)))
        self.assertEqual(plus_eval["anchor_atr_a0"], round(plus_a0, 1))
        self.assertEqual(plus_eval["position_cycle_id"], plus_cycle_id)
        self.assertFalse(plus_eval["is_migrated_anchor"])
        self.assertGreaterEqual(plus_eval["confirmed_stop_price"], plus_prev_stop)

    def test_02_stop_breach_preservation_and_status(self):
        """테스트 2: 손절선 침범 시 (기존 손절가 10,000원 -> 현재가 9,500원) 손절가 보존 및 STOP_BREACHED 상태 단언"""
        code = "012340"
        name = "침범시험주"
        p0 = 10000.0
        a0 = 500.0
        cycle_id = f"{code}_20260810_V4"
        prev_stop = 10000.0 # 기존 확정 손절가 10,000원
        current_price = 9500.0 # 현재가 9,500원으로 손절선 침범

        self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (code, name))
        self.pm.add_holding(code, name, 100, 10000.0)
        self.db.execute_non_query("""
            UPDATE portfolio_positions SET
                anchor_price_p0 = ?, anchor_atr_a0 = ?, position_cycle_id = ?,
                anchor_created_at = '2026-08-10 09:00:00', previous_confirmed_stop = ?, confirmed_stop_price = ?,
                highest_close = 10000.0, highest_intraday = 10200.0
            WHERE stock_code = ?
        """, (p0, a0, cycle_id, prev_stop, prev_stop, code))

        # 현재가 9,500원 일봉 주입
        self._insert_daily_bars(code, base_price=current_price, count=15)

        with patch(
            "src.analysis.intraday_analysis.Intraday45mAnalyzer.fetch_canonical_15m_data",
            return_value=_normal_intraday_response(current_price),
        ):
            eval_list = self.pm.get_held_portfolio_status()
        self.assertEqual(len(eval_list), 1)
        item = eval_list[0]

        # 손절가 보존 및 침범 상태 단언
        self.assertEqual(item["confirmed_stop_price"], prev_stop, "손절선 침범 시에도 기존 확정 손절가 10,000원이 보존되어야 함")
        self.assertTrue(item["is_stop_breached"], "is_stop_breached 플래그가 True여야 함")
        self.assertEqual(item["action_status"], "🚨 손절선 침범 [손실축소 판단 필요]")
        self.assertEqual(item["smartphone_action"], "손절선 침범 — 즉시 확인")
        self.assertEqual(item["stop_update_status"], "🚨 손절선 침범 — 즉시 확인")

    def test_03_sixteen_fields_persistence_across_sync_and_restart(self):
        """테스트 3: 16개 핵심 감시 상태 필드가 키움 동기화, PM 재시작, DB 재연결 후에도 100% 보존되는지 검증"""
        code = "005930"
        name = "삼성전자"
        p0 = 75000.0
        a0 = 1500.0
        cycle_id = f"{code}_202608010900_V4"
        created_at = "2026-08-01 09:00:00"

        self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (code, name))
        self.pm.add_holding(code, name, 10, 75000.0)
        self.db.execute_non_query("""
            UPDATE portfolio_positions SET
                position_cycle_id = ?, anchor_price_p0 = ?, anchor_atr_a0 = ?, anchor_created_at = ?,
                reanchor_flag = 0, highest_close = 78000.0, highest_close_price = 78000.0,
                highest_intraday = 79000.0, highest_after_activation = 79000.0,
                previous_confirmed_stop = 72000.0, confirmed_stop_price = 72000.0, ratchet_stop = 72000.0,
                profit_activation_status = 'INACTIVE', previous_profit_trail = 0.0,
                profit_trail = 0.0, effective_exit_line = 72000.0,
                profit_activation_raw = 79500.0, profit_activation_effective = 79500.0
            WHERE stock_code = ?
        """, (cycle_id, p0, a0, created_at, code))

        self._insert_daily_bars(code, base_price=76000.0, count=15)

        # 1. 첫 번째 평가 수행
        with patch(
            "src.analysis.intraday_analysis.Intraday45mAnalyzer.fetch_canonical_15m_data",
            return_value=_normal_intraday_response(76000.0),
        ):
            self.pm.get_held_portfolio_status()

        # 2. 키움 잔고 동기화 (단일 트랜잭션 동기화로 10주 유지)
        kiwoom_positions = [{
            "stock_code": code,
            "stock_name": name,
            "quantity": 10,
            "avg_buy_price": 75000.0,
            "current_price": 76000
        }]
        self.pm._update_portfolio_in_single_transaction(kiwoom_positions)

        # 3. 새로운 PM 인스턴스 및 DB 연결로 재생성
        new_db = DatabaseManager(str(self.test_db_path))
        new_pm = PortfolioManager(new_db)

        rows = new_db.execute_query("SELECT * FROM portfolio_positions WHERE stock_code = ?", (code,))
        self.assertEqual(len(rows), 1)
        r = rows[0]

        # 16개 감시 필드 승계 검증
        self.assertEqual(r["position_cycle_id"], cycle_id)
        self.assertEqual(float(r["anchor_price_p0"]), p0)
        self.assertEqual(float(r["anchor_atr_a0"]), a0)
        self.assertEqual(r["anchor_created_at"], created_at)
        self.assertEqual(int(r["reanchor_flag"]), 0)
        self.assertGreaterEqual(float(r["highest_close"]), 78000.0)
        self.assertGreaterEqual(float(r["highest_intraday"]), 79000.0)
        self.assertGreaterEqual(float(r["previous_confirmed_stop"]), 72000.0)
        self.assertGreaterEqual(float(r["confirmed_stop_price"]), 72000.0)
        self.assertGreaterEqual(float(r["ratchet_stop"]), 72000.0)
        self.assertIn(r["profit_activation_status"], ("INACTIVE", "ACTIVE"))
        self.assertGreaterEqual(float(r["effective_exit_line"]), 72000.0)

    def test_04_email_strategy_text_and_smartphone_action_rendering(self):
        """테스트 4: 이메일 렌더러에서 전략 문구, 스마트폰 조치 뱃지, 목표가/익절선 분리 표시 검증"""
        held_status = [
            {
                "stock_code": "004960",
                "stock_name": "한신공영",
                "quantity": 100,
                "current_price": 8700,
                "daily_change_pct": 3.2,
                "pnl_pct": 3.2,
                "pnl_amount": 27000,
                "trade_mode": "NORMAL",
                "action_status": "🟢 계속 보유/홀딩",
                "smartphone_action": "상향 수정",
                "profit_activation_status": "INACTIVE",
                "profit_activation_price": 9350,
                "profit_trail_price": 0,
                "kiwoom_stop_tick_price": 8100,
                "kiwoom_target_tick_price": 9350,
                "profit_trail_delta": 460,
                "recommended_order_qty": 0,
                "order_direction": "보유 (관망/홀딩)",
                "atr_pct": 3.6,
                "is_stop_breached": False,
                "is_etf": False,
                "f_score_confirmed": True,
                "f_score": 75.0,
                "t_score": 60.0,
                "final_score": 67.5,
                "data_completeness": 100.0,
                "risk_target_qty": 100,
                "excess_qty": 0
            },
            {
                "stock_code": "012340",
                "stock_name": "침범시험주",
                "quantity": 100,
                "current_price": 9500,
                "daily_change_pct": -3.5,
                "pnl_pct": -13.6,
                "pnl_amount": -150000,
                "trade_mode": "NORMAL",
                "action_status": "🚨 손절선 침범 [손실축소 판단 필요]",
                "smartphone_action": "손절선 침범 — 즉시 확인",
                "profit_activation_status": "INACTIVE",
                "profit_activation_price": 12500,
                "profit_trail_price": 0,
                "kiwoom_stop_tick_price": 10000,
                "kiwoom_target_tick_price": 12500,
                "profit_trail_delta": 750,
                "recommended_order_qty": 0,
                "order_direction": "보유 (손절선 침범)",
                "atr_pct": 5.2,
                "is_stop_breached": True,
                "is_etf": False,
                "f_score_confirmed": True,
                "f_score": 50.0,
                "t_score": 40.0,
                "final_score": 45.0,
                "data_completeness": 100.0,
                "risk_target_qty": 100,
                "excess_qty": 0
            }
        ]

        html_out = generate_mobile_html_report_v2(
            date_str="2026-08-19",
            total_count=2,
            caught_signals=[],
            all_results=[],
            held_portfolio=held_status,
            disclosures=[]
        )

        # 단언 1: 침범시험주 카드에 현재 행동과 손절가가 단일 보유카드로 렌더링
        self.assertIn("🚨 손절선 침범", html_out)
        self.assertIn("손절가 10,000원", html_out)

        # 단언 2: 한신공영 카드에 현재 행동과 익절 목표가 표시
        self.assertIn("상승추세 보유", html_out)
        self.assertIn("익절 목표가 9,350원", html_out)

        # 단언 3: 보유 0주인데 45m 눌림목 분할매수가 나오지 않는지 검증
        self.assertNotIn("45m 눌림목 분할매수", html_out)

    def test_05_fail_closed_ratchet_decrease_rejection(self):
        """테스트 5: 동일 사이클 내 손절 래칫 하향 발생 시 Fail-Closed 차단 단언"""
        code = "005930"
        name = "삼성전자"
        p0 = 75000.0
        a0 = 1500.0
        cycle_id = f"{code}_20260801_V4"
        prev_stop = 73000.0 # 전일 확정 손절가 73,000원

        self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (code, name))
        self.pm.add_holding(code, name, 10, 75000.0)
        self.db.execute_non_query("""
            UPDATE portfolio_positions SET
                position_cycle_id = ?, anchor_price_p0 = ?, anchor_atr_a0 = ?,
                anchor_created_at = '2026-08-01 09:00:00', previous_confirmed_stop = ?, confirmed_stop_price = ?,
                highest_close = 75000.0, highest_intraday = 75000.0
            WHERE stock_code = ?
        """, (cycle_id, p0, a0, prev_stop, prev_stop, code))

        self._insert_daily_bars(code, base_price=74000.0, count=15)

        # ATRRiskEngine을 직접 호출했을 때도 kiwoom_stop_tick이 prev_stop 이상이어야 함
        pos_risk = ATRRiskEngine.calculate_position_risk(
            p0=p0, a0=a0, at=1500.0, current_price=74000.0,
            highest_close=75000.0, highest_high=75000.0,
            prev_confirmed_stop=prev_stop,
            trade_mode="NORMAL", entry_stage=1, lifecycle_status="POSITION_OPEN",
            total_equity=10000000.0, current_qty=10, is_etf=False,
            is_top_confirmed=True, is_suspended=False, data_validity_flag=1
        )
        self.assertGreaterEqual(pos_risk["kiwoom_stop_tick"], prev_stop, "Risk Engine에서 kiwoom_stop_tick은 절대 prev_confirmed_stop 밑으로 내려가지 않아야 함")

        # main.py의 verify_pipeline_stock_code_consistency에서 손절가 하향 위반 시 RuntimeError 발생 단언
        bad_held_status = [{
            "stock_code": code,
            "stock_name": name,
            "confirmed_stop_price": 70000.0, # 73,000원 -> 70,000원으로 불법 하향 시뮬레이션
            "prev_confirmed_stop": 73000.0,
            "is_migrated_anchor": False,
            "profit_activation_status": "INACTIVE"
        }]

        # 더미 xlsx 파일 생성
        test_xlsx_path = BASE_DIR / "data" / "test_ratchet_gate.xlsx"
        df_dummy = pd.DataFrame([{"종목코드": code, "종목명": name}])
        with pd.ExcelWriter(test_xlsx_path) as writer:
            df_dummy.to_excel(writer, sheet_name="보유종목 모니터링", index=False)

        try:
            with self.assertRaises(RuntimeError) as ctx:
                main.verify_pipeline_stock_code_consistency(
                    raw_kiwoom_positions=[{"stock_code": code, "quantity": 10}],
                    db=self.db,
                    held_status=bad_held_status,
                    excel_path=test_xlsx_path,
                    html_report=f'<div data-held-stock-codes="{code}"></div>'
                )
            self.assertIn("손절선 하향 무결성 위반", str(ctx.exception))
        finally:
            if test_xlsx_path.exists():
                try:
                    test_xlsx_path.unlink()
                except Exception:
                    pass

if __name__ == "__main__":
    unittest.main()
