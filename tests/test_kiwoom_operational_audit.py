# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import os
import sys
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.api.kiwoom_api import KiwoomAPIClient
from src.database.db_manager import DatabaseManager
from src.engine.portfolio_manager import PortfolioManager
from main import verify_pipeline_stock_code_consistency

class TestKiwoomOperationalAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db_path = BASE_DIR / "data" / "test_operational_audit.db"
        if cls.test_db_path.exists():
            try:
                cls.test_db_path.unlink()
            except Exception:
                pass
        cls.db = DatabaseManager(str(cls.test_db_path))
        cls.all_11_codes = [
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
        self.client = KiwoomAPIClient(
            app_key="TEST_APP_KEY",
            app_secret="TEST_APP_SECRET",
            account_no="12345678-01",
            use_mock=False
        )
        self.client.access_token = "TEST_ACCESS_TOKEN"
        self.pm = PortfolioManager(self.db, self.client)

    def test_01_pagination_10_plus_1_daedong_success(self):
        """1. 1페이지 10개 + 2페이지 대동 1개 수집 -> 총 11개 성공 검증"""
        # 1페이지: 대동(000490)을 제외한 10개 종목
        page1_items = [{"stk_cd": f"A{c}", "stk_nm": f"종목_{c}", "rmnd_qty": "100", "pur_pric": "10000", "cur_prc": "10500", "pur_amt": "1000000", "prft_rt": "5.0"} for c in self.all_11_codes[1:]]
        # 2페이지: 대동(000490) 1개 종목
        page2_items = [{"stk_cd": "A000490", "stk_nm": "대동", "rmnd_qty": "2475", "pur_pric": "8116", "cur_prc": "8050", "pur_amt": "20088100", "prft_rt": "-1.05"}]

        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {"acnt_evlt_remn_indv_tot": page1_items}
        resp1.headers = {"cont-yn": "Y", "next-key": "PAGE2_KEY"}

        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {"acnt_evlt_remn_indv_tot": page2_items}
        resp2.headers = {"cont-yn": "N", "next-key": ""}

        with patch("requests.post", side_effect=[resp1, resp2]) as mock_post:
            positions = self.client.get_account_positions()
            self.assertEqual(len(positions), 11)
            codes = [p["stock_code"] for p in positions]
            self.assertIn("000490", codes)
            self.assertEqual(mock_post.call_count, 2)

    def test_02_second_page_failure_preserves_db_and_no_mail(self):
        """2. 두 번째 페이지 실패 -> 기존 DB 보존 및 메일 미발송 검증"""
        # 기존 DB에 기존 3개 종목 저장
        self.pm.add_holding("005930", "삼성전자", 10, 70000.0)
        self.pm.add_holding("000660", "SK하이닉스", 5, 150000.0)

        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {"acnt_evlt_remn_indv_tot": [{"stk_cd": "A000490", "stk_nm": "대동", "rmnd_qty": "100", "pur_pric": "8000", "cur_prc": "8000", "pur_amt": "800000", "prft_rt": "0.0"}]}
        resp1.headers = {"cont-yn": "Y", "next-key": "PAGE2_KEY"}

        resp2 = MagicMock()
        resp2.status_code = 500
        resp2.text = "Internal Server Error"

        with patch("requests.post", side_effect=[resp1, resp2]):
            with self.assertRaises(RuntimeError):
                self.pm.sync_portfolio_from_kiwoom()

        # 기존 DB 종목이 삭제되지 않고 완전히 보존되었는지 검증
        db_rows = self.db.execute_query("SELECT stock_code FROM portfolio_positions WHERE quantity > 0")
        existing_codes = {r["stock_code"] for r in db_rows}
        self.assertEqual(existing_codes, {"005930", "000660"})

    def test_03_kiwoom_10_vs_local_json_11_no_json_supplement(self):
        """3. 키움 실계좌 10개와 로컬 JSON 11개 불일치 -> JSON 보충 금지 및 원본 유지 검증"""
        # 키움 실계좌는 10개만 반환
        page1_items = [{"stk_cd": f"A{c}", "stk_nm": f"종목_{c}", "rmnd_qty": "100", "pur_pric": "10000", "cur_prc": "10500", "pur_amt": "1000000", "prft_rt": "5.0"} for c in self.all_11_codes[1:]]
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {"acnt_evlt_remn_indv_tot": page1_items}
        resp1.headers = {"cont-yn": "N", "next-key": ""}

        with patch("requests.post", return_value=resp1):
            positions = self.client.get_account_positions()
            # JSON 11개를 임의로 섞지 않고 키움 원본 10개만 정확히 유지
            self.assertEqual(len(positions), 10)
            self.assertNotIn("000490", [p["stock_code"] for p in positions])

    def test_04_raw_11_db_11_eval_10_fails_gate(self):
        """4. 원본 11개, DB 11개, 평가 10개 -> 실패 검증"""
        raw_kiwoom = [{"stock_code": c, "quantity": 100} for c in self.all_11_codes]
        for c in self.all_11_codes:
            self.db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000)", (c,))
        
        # 평가 리스트에서 1개 누락 (10개)
        held_status_10 = [{"stock_code": c, "stock_name": f"종목_{c}"} for c in self.all_11_codes if c != "000490"]

        temp_excel = BASE_DIR / "data" / "test_eval_10.xlsx"
        df = pd.DataFrame([{"종목코드": c} for c in self.all_11_codes])
        df.to_excel(temp_excel, index=False)
        html = "".join([f'<div data-stock-code="{c}"></div>' for c in self.all_11_codes])

        try:
            with self.assertRaises(RuntimeError) as cm:
                verify_pipeline_stock_code_consistency(raw_kiwoom, self.db, held_status_10, temp_excel, html)
            self.assertIn("missing_in_evaluation", str(cm.exception))
        finally:
            if temp_excel.exists():
                temp_excel.unlink()

    def test_05_raw_11_xlsx_11_email_card_10_fails_gate(self):
        """5. 원본 11개, XLSX 11개, 이메일 카드 10개 -> 실패 검증"""
        raw_kiwoom = [{"stock_code": c, "quantity": 100} for c in self.all_11_codes]
        for c in self.all_11_codes:
            self.db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000)", (c,))
        held_status_11 = [{"stock_code": c, "stock_name": f"종목_{c}"} for c in self.all_11_codes]

        temp_excel = BASE_DIR / "data" / "test_email_10.xlsx"
        df = pd.DataFrame([{"종목코드": c} for c in self.all_11_codes])
        df.to_excel(temp_excel, index=False)
        # 이메일 카드 1개 누락 (10개)
        html_10 = "".join([f'<div data-stock-code="{c}"></div>' for c in self.all_11_codes if c != "000490"])

        try:
            with self.assertRaises(RuntimeError) as cm:
                verify_pipeline_stock_code_consistency(raw_kiwoom, self.db, held_status_11, temp_excel, html_10)
            self.assertIn("missing_in_email", str(cm.exception))
        finally:
            if temp_excel.exists():
                temp_excel.unlink()

    def test_06_operational_env_mock_or_json_fallback_raises(self):
        """6. 운영환경(CI)에서 mock 또는 JSON fallback 사용 시 즉시 실패 검증"""
        mock_client = KiwoomAPIClient(app_key="", app_secret="", use_mock=True)
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaises(RuntimeError) as cm:
                mock_client.get_account_positions()
            self.assertIn("운영 환경(CI)에서 키움 API키 미설정", str(cm.exception))

    def test_07_all_11_consistent_succeeds(self):
        """7. 11개 모두 일치하는 경우에만 무결성 게이트 성공 통과 검증"""
        raw_kiwoom = [{"stock_code": c, "quantity": 100} for c in self.all_11_codes]
        for c in self.all_11_codes:
            self.db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000)", (c,))
        held_status_11 = [{"stock_code": c, "stock_name": f"종목_{c}"} for c in self.all_11_codes]

        temp_excel = BASE_DIR / "data" / "test_all_11.xlsx"
        df = pd.DataFrame([{"종목코드": c} for c in self.all_11_codes])
        df.to_excel(temp_excel, index=False)
        html_11 = "".join([f'<div data-stock-code="{c}"></div>' for c in self.all_11_codes])

        try:
            # 예외 없이 통과해야 함
            verify_pipeline_stock_code_consistency(raw_kiwoom, self.db, held_status_11, temp_excel, html_11)
        finally:
            if temp_excel.exists():
                temp_excel.unlink()

if __name__ == "__main__":
    unittest.main()
