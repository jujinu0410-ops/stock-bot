import unittest
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.database.db_manager import DatabaseManager
from src.engine.portfolio_manager import PortfolioManager
import main

class TestPortfolioManagerRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db_path = BASE_DIR / "data" / "test_stock_regression.db"
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

    def test_01_normal_holding_account_risk_pct_saved_and_returned(self):
        """회귀검증 1: NORMAL 보유종목 1개 평가 시 account_risk_pct 정상 연산, DB 저장 및 리스트 반환 검증"""
        code = "005930"
        name = "삼성전자"
        qty = 10
        avg_price = 70000.0

        # DB 초기화 및 보유종목 / 시세 데이터 주입
        self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (code, name))
        self.pm.add_holding(code, name, qty, avg_price)

        # 10일치 일봉 데이터 주입 (ATR 계산용)
        for i in range(10):
            d_str = f"202608{10+i:02d}"
            c_p = 70000.0 + (i * 500)
            h_p = c_p + 1000.0
            l_p = c_p - 1000.0
            o_p = c_p
            self.db.execute_non_query("""
                INSERT OR REPLACE INTO kiwoom_daily (stock_code, date, open_price, high_price, low_price, close_price, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (code, d_str, o_p, h_p, l_p, c_p, 100000))

        # 평가 수행
        held_status = self.pm.get_held_portfolio_status()

        # 1. get_held_portfolio_status가 1개 이상 정상 반환하는지 단언
        self.assertIsInstance(held_status, list)
        self.assertEqual(len(held_status), 1)

        stock_eval = held_status[0]
        self.assertEqual(stock_eval["stock_code"], code)

        # 2. account_risk_pct가 딕셔너리에 정상 포함되어 있는지 단언
        self.assertIn("account_risk_pct", stock_eval)
        self.assertIsInstance(stock_eval["account_risk_pct"], (int, float))
        self.assertGreater(stock_eval["account_risk_pct"], 0.0)

        # 3. DB portfolio_positions 테이블의 account_risk_pct 컬럼에 정상 저장되었는지 단언
        db_rows = self.db.execute_query("SELECT account_risk_pct FROM portfolio_positions WHERE stock_code = ?", (code,))
        self.assertTrue(len(db_rows) > 0)
        saved_risk_pct = db_rows[0]["account_risk_pct"]
        self.assertIsNotNone(saved_risk_pct)
        self.assertGreater(saved_risk_pct, 0.0)

    def test_02_held_evaluation_exception_re_raised_in_main(self):
        """회귀검증 2: 보유종목 평가 실패 시 main.py가 예외를 삼키지 않고 상위로 raise하는지 검증"""
        with patch.object(PortfolioManager, 'get_held_portfolio_status', side_effect=ValueError("인위적 평가 장애")):
            with self.assertRaises(ValueError):
                main.run_post_market_analysis()

    def test_03_db_holdings_exist_but_zero_evaluated_raises_runtime_error(self):
        """회귀검증 3: DB상 보유종목이 존재하나 평가 결과가 0개이면 RuntimeError 발생 검증"""
        code = "005930"
        name = "삼성전자"
        self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (code, name))
        self.pm.add_holding(code, name, 10, 70000.0)

        with patch.object(PortfolioManager, 'get_held_portfolio_status', return_value=[]):
            with patch('main.DatabaseManager', return_value=self.db):
                with patch('main.PortfolioManager', return_value=self.pm):
                    with self.assertRaises(RuntimeError) as cm:
                        main.run_post_market_analysis()
                    self.assertIn("보유종목 평가 실패", str(cm.exception))

    def test_04_email_send_failure_raises_runtime_error(self):
        """회귀검증 4: 지메일 발송 실패 시 RuntimeError 발생 검증 (GitHub Actions RED 보장)"""
        code = "005930"
        name = "삼성전자"
        self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (code, name))
        self.pm.add_holding(code, name, 10, 70000.0)

        # 10일치 일봉 데이터
        for i in range(10):
            d_str = f"202608{10+i:02d}"
            c_p = 70000.0
            self.db.execute_non_query("""
                INSERT OR REPLACE INTO kiwoom_daily (stock_code, date, open_price, high_price, low_price, close_price, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (code, d_str, c_p, c_p+100, c_p-100, c_p, 100000))

        with patch('main.DatabaseManager', return_value=self.db):
            with patch('main.PortfolioManager', return_value=self.pm):
                with patch('src.notifications.gmail_notifier.GmailNotifier.send_email', return_value=False):
                    with self.assertRaises(RuntimeError) as cm:
                        main.run_post_market_analysis(force=True)
                    self.assertIn("지메일 발송 실패", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
