import unittest
from pathlib import Path
import sys
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.database.db_manager import DatabaseManager
from main import verify_pipeline_stock_code_consistency

class TestPipelineConsistencyGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db_path = BASE_DIR / "data" / "test_consistency_gate.db"
        if cls.test_db_path.exists():
            try:
                cls.test_db_path.unlink()
            except Exception:
                pass
        cls.db = DatabaseManager(str(cls.test_db_path))
        cls.sample_codes = [
            "000490", "004960", "055490", "140670", "161510",
            "206650", "234920", "241520", "267260", "348340", "490590"
        ]

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
        for code in self.sample_codes:
            self.db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (code, f"종목_{code}"))
            self.db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000.0)", (code,))

        self.temp_excel = BASE_DIR / "data" / "test_consistency.xlsx"
        df = pd.DataFrame([{"종목코드": c, "종목명": f"종목_{c}"} for c in self.sample_codes])
        df.to_excel(self.temp_excel, sheet_name="보유종목 모니터링", index=False)

        self.html_cards = "".join([f'<div data-stock-code="{c}">Card {c}</div>' for c in self.sample_codes])

    def tearDown(self):
        if self.temp_excel.exists():
            try:
                self.temp_excel.unlink()
            except Exception:
                pass

    def test_01_all_layers_consistent_passes(self):
        """테스트 01: 키움-DB-held_status-XLSX-이메일 전 계층 11개 완전 일치 시 통과 검증"""
        raw_kiwoom = [{"stock_code": c, "quantity": 100} for c in self.sample_codes]
        held_status = [{"stock_code": c, "stock_name": f"종목_{c}"} for c in self.sample_codes]

        # 예외 없이 정상 통과해야 함
        verify_pipeline_stock_code_consistency(
            raw_kiwoom_positions=raw_kiwoom,
            db_manager=self.db,
            held_status=held_status,
            excel_path=self.temp_excel,
            html_report=self.html_cards
        )

    def test_02_kiwoom_vs_db_mismatch_raises(self):
        """테스트 02: 키움 API 원본(11개) vs DB(10개) 불일치 시 RuntimeError 검증"""
        raw_kiwoom = [{"stock_code": c, "quantity": 100} for c in self.sample_codes]
        # DB에서 대동(000490) 제거
        self.db.execute_non_query("DELETE FROM portfolio_positions WHERE stock_code = '000490'")
        held_status = [{"stock_code": c, "stock_name": f"종목_{c}"} for c in self.sample_codes if c != "000490"]
        df = pd.DataFrame([{"종목코드": c} for c in self.sample_codes if c != "000490"])
        df.to_excel(self.temp_excel, index=False)
        html = "".join([f'<div data-stock-code="{c}"></div>' for c in self.sample_codes if c != "000490"])

        with self.assertRaises(RuntimeError) as cm:
            verify_pipeline_stock_code_consistency(
                raw_kiwoom_positions=raw_kiwoom,
                db_manager=self.db,
                held_status=held_status,
                excel_path=self.temp_excel,
                html_report=html
            )
        self.assertIn("missing_in_db", str(cm.exception))

    def test_03_held_status_vs_xlsx_mismatch_raises(self):
        """테스트 03: held_status(11개) vs XLSX(10개) 불일치 시 RuntimeError 검증"""
        raw_kiwoom = [{"stock_code": c, "quantity": 100} for c in self.sample_codes]
        held_status = [{"stock_code": c, "stock_name": f"종목_{c}"} for c in self.sample_codes]
        # XLSX에서 1개 누락
        df = pd.DataFrame([{"종목코드": c} for c in self.sample_codes if c != "000490"])
        df.to_excel(self.temp_excel, index=False)

        with self.assertRaises(RuntimeError) as cm:
            verify_pipeline_stock_code_consistency(
                raw_kiwoom_positions=raw_kiwoom,
                db_manager=self.db,
                held_status=held_status,
                excel_path=self.temp_excel,
                html_report=self.html_cards
            )
        self.assertIn("missing_in_xlsx", str(cm.exception))

    def test_04_held_status_vs_email_card_mismatch_raises(self):
        """테스트 04: held_status(11개) vs 이메일 카드(10개) 불일치 시 RuntimeError 검증"""
        raw_kiwoom = [{"stock_code": c, "quantity": 100} for c in self.sample_codes]
        held_status = [{"stock_code": c, "stock_name": f"종목_{c}"} for c in self.sample_codes]
        # 이메일 카드에서 1개 누락
        html_missing = "".join([f'<div data-stock-code="{c}"></div>' for c in self.sample_codes if c != "000490"])

        with self.assertRaises(RuntimeError) as cm:
            verify_pipeline_stock_code_consistency(
                raw_kiwoom_positions=raw_kiwoom,
                db_manager=self.db,
                held_status=held_status,
                excel_path=self.temp_excel,
                html_report=html_missing
            )
        self.assertIn("missing_in_email", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
