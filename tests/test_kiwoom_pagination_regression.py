import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.api.kiwoom_api import KiwoomAPIClient

class TestKiwoomPaginationRegression(unittest.TestCase):
    def setUp(self):
        self.client = KiwoomAPIClient(
            app_key="TEST_APP_KEY",
            app_secret="TEST_APP_SECRET",
            account_no="12345678-01",
            use_mock=False
        )
        self.client.access_token = "TEST_TOKEN"

    def test_01_pagination_10_plus_1_daedong_success(self):
        """테스트 01: 10개(1페이지, cont-yn=Y) + 대동 000490 1개(2페이지, cont-yn=N) 총 11개 수집 성공 검증"""
        page1_items = [
            {"stk_cd": "A004960", "stk_nm": "한신공영", "rmnd_qty": "1739", "pur_pric": "11092.0", "cur_prc": "11050", "pur_amt": "19289339.0", "prft_rt": "-0.61"},
            {"stk_cd": "A055490", "stk_nm": "테이팩스", "rmnd_qty": "4283", "pur_pric": "23364.0", "cur_prc": "14150", "pur_amt": "100071170.0", "prft_rt": "-39.58"},
            {"stk_cd": "A140670", "stk_nm": "알에스오토메이션", "rmnd_qty": "531", "pur_pric": "18899.0", "cur_prc": "10100", "pur_amt": "10035560.0", "prft_rt": "-46.69"},
            {"stk_cd": "A161510", "stk_nm": "PLUS 고배당주", "rmnd_qty": "250", "pur_pric": "28186.0", "cur_prc": "25335", "pur_amt": "7046560.0", "prft_rt": "-10.14"},
            {"stk_cd": "A206650", "stk_nm": "유바이오로직스", "rmnd_qty": "293", "pur_pric": "15716.0", "cur_prc": "9150", "pur_amt": "4604937.0", "prft_rt": "-41.92"},
            {"stk_cd": "A234920", "stk_nm": "자이글", "rmnd_qty": "9314", "pur_pric": "7517.0", "cur_prc": "5310", "pur_amt": "70021890.0", "prft_rt": "-29.54"},
            {"stk_cd": "A241520", "stk_nm": "DSC인베스트먼트", "rmnd_qty": "115", "pur_pric": "17995.0", "cur_prc": "8200", "pur_amt": "2069460.0", "prft_rt": "-54.55"},
            {"stk_cd": "A267260", "stk_nm": "HD현대일렉트릭", "rmnd_qty": "4", "pur_pric": "745000.0", "cur_prc": "785000", "pur_amt": "2980000.0", "prft_rt": "5.13"},
            {"stk_cd": "A348340", "stk_nm": "뉴로메카", "rmnd_qty": "124", "pur_pric": "80329.0", "cur_prc": "21300", "pur_amt": "9960800.0", "prft_rt": "-73.56"},
            {"stk_cd": "A490590", "stk_nm": "RISE 미국AI밸류체인데일리고정커버드콜", "rmnd_qty": "9114", "pur_pric": "16458.0", "cur_prc": "14635", "pur_amt": "150001860.0", "prft_rt": "-11.11"}
        ]
        page2_items = [
            {"stk_cd": "A000490", "stk_nm": "대동", "rmnd_qty": "2475", "pur_pric": "8116.0", "cur_prc": "8050", "pur_amt": "20088100.0", "prft_rt": "-1.05"}
        ]

        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {"acnt_evlt_remn_indv_tot": page1_items}
        resp1.headers = {"cont-yn": "Y", "next-key": "NEXT_PAGE_KEY_123"}

        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {"acnt_evlt_remn_indv_tot": page2_items}
        resp2.headers = {"cont-yn": "N", "next-key": ""}

        with patch("requests.post", side_effect=[resp1, resp2]) as mock_post:
            positions = self.client.get_account_positions()

            self.assertEqual(mock_post.call_count, 2)
            self.assertEqual(len(positions), 11)

            codes = [p["stock_code"] for p in positions]
            self.assertIn("000490", codes)
            self.assertIn("004960", codes)
            self.assertIn("490590", codes)

            # 2번째 호출 시 전달된 헤더 검증
            second_call_headers = mock_post.call_args_list[1][1]["headers"]
            self.assertEqual(second_call_headers.get("cont-yn"), "Y")
            self.assertEqual(second_call_headers.get("next-key"), "NEXT_PAGE_KEY_123")

    def test_02_pagination_deduplication(self):
        """테스트 02: 페이지 간 중복 종목 발생 시 중복 제거 검증"""
        item_a = {"stk_cd": "A000490", "stk_nm": "대동", "rmnd_qty": "100", "pur_pric": "8000.0", "cur_prc": "8100", "pur_amt": "800000.0", "prft_rt": "1.25"}
        item_b = {"stk_cd": "A004960", "stk_nm": "한신공영", "rmnd_qty": "200", "pur_pric": "11000.0", "cur_prc": "11200", "pur_amt": "2200000.0", "prft_rt": "1.8"}

        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {"acnt_evlt_remn_indv_tot": [item_a, item_b]}
        resp1.headers = {"cont-yn": "Y", "next-key": "KEY_1"}

        resp2 = MagicMock()
        resp2.status_code = 200
        # 중복 종목 item_a 포함
        resp2.json.return_value = {"acnt_evlt_remn_indv_tot": [item_a]}
        resp2.headers = {"cont-yn": "N", "next-key": ""}

        with patch("requests.post", side_effect=[resp1, resp2]):
            positions = self.client.get_account_positions()
            self.assertEqual(len(positions), 2)
            codes = [p["stock_code"] for p in positions]
            self.assertEqual(set(codes), {"000490", "004960"})

    def test_03_pagination_missing_next_key_raises(self):
        """테스트 03: cont-yn='Y'이나 next-key 누락 시 RuntimeError 발생 검증"""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"acnt_evlt_remn_indv_tot": []}
        resp.headers = {"cont-yn": "Y", "next-key": ""}

        with patch("requests.post", return_value=resp):
            with self.assertRaises(RuntimeError) as cm:
                self.client.get_account_positions()
            self.assertIn("next-key가 누락", str(cm.exception))

    def test_04_pagination_duplicate_next_key_raises(self):
        """테스트 04: 동일한 next-key 반복(무한루프) 감지 시 RuntimeError 발생 검증"""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"acnt_evlt_remn_indv_tot": []}
        resp.headers = {"cont-yn": "Y", "next-key": "SAME_KEY"}

        with patch("requests.post", return_value=resp):
            with self.assertRaises(RuntimeError) as cm:
                self.client.get_account_positions()
            self.assertIn("무한루프", str(cm.exception))

    def test_05_pagination_http_error_raises(self):
        """테스트 05: 연속조회 중 HTTP 상태코드 오류 시 RuntimeError 발생 검증"""
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "Internal Server Error"

        with patch("requests.post", return_value=resp):
            with self.assertRaises(RuntimeError) as cm:
                self.client.get_account_positions()
            self.assertIn("상태코드 500", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
