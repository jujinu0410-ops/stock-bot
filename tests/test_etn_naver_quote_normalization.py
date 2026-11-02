"""Regression: Kiwoom Q-prefixed ETN identifiers use Naver's 6-digit lookup keys."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from src.api.market_quote_client import MarketQuoteClient


class NoHoldingsKiwoom:
    def is_valid_key(self):
        return False


class Response:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.payload = payload or {}

    def json(self):
        return self.payload


class TestEtnNaverCode(unittest.TestCase):
    def setUp(self):
        self.client = MarketQuoteClient(kiwoom_client=NoHoldingsKiwoom())

    def test_q_etn_mobile_success_preserves_original_identifier(self):
        today = datetime.now().strftime("%Y-%m-%d")
        urls = []

        def get(url, **kwargs):
            urls.append(url)
            if url.endswith("/530107/basic"):
                return Response(200, {
                    "stockEndType": "etn",
                    "itemCode": "530107",
                    "closePrice": "2,100",
                    "localTradedAt": today + "T12:42:00+09:00",
                    "marketStatus": "OPEN",
                })
            return Response(409, {"code": "StockConflict"})

        with patch("src.api.market_quote_client.requests.get", side_effect=get):
            result = self.client.fetch_live_quote("Q530107")
        self.assertTrue(result["success"])
        self.assertEqual(result["stock_code"], "Q530107")
        self.assertEqual(result["price"], 2100)
        self.assertEqual(result["quote_source"], "NAVER_MOBILE_LIVE_API")
        self.assertEqual(urls, ["https://m.stock.naver.com/api/stock/530107/basic"])

    def test_etn_polling_fallback_normalized(self):
        urls = []

        def get(url, **kwargs):
            urls.append(url)
            if "m.stock.naver.com" in url:
                return Response(409)
            return Response(200, {
                "datas": [{"closePrice": "2105", "marketStatus": "OPEN"}]
            })

        with patch("src.api.market_quote_client.requests.get", side_effect=get):
            result = self.client.fetch_live_quote("Q530107")
        self.assertTrue(result["success"])
        self.assertEqual(result["stock_code"], "Q530107")
        self.assertEqual(result["quote_source"], "NAVER_POLLING_LIVE_API")
        self.assertTrue(urls[0].endswith("/stock/530107/basic"))
        self.assertTrue(urls[1].endswith("/stock/530107"))

    def test_regular_stock_code_unchanged(self):
        with patch(
            "src.api.market_quote_client.requests.get",
            return_value=Response(200, {
                "closePrice": "77000",
                "localTradedAt": datetime.now().strftime("%Y-%m-%d") + "T12:00:00+09:00",
            }),
        ) as get:
            result = self.client.fetch_live_quote("064350")
        self.assertTrue(result["success"])
        self.assertEqual(result["stock_code"], "064350")
        self.assertIn("/064350/basic", get.call_args.args[0])

    def test_stale_quote_still_rejected(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        with patch(
            "src.api.market_quote_client.requests.get",
            return_value=Response(200, {
                "closePrice": "2100",
                "localTradedAt": yesterday + "T15:30:00+09:00",
            }),
        ):
            result = self.client.fetch_live_quote("Q530107")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "LIVE_QUOTE_STALE")
        self.assertEqual(result["stock_code"], "Q530107")

    def test_no_source_still_data_hold(self):
        with patch("src.api.market_quote_client.requests.get",
                   return_value=Response(409, {"code": "StockConflict"})):
            result = self.client.fetch_live_quote("Q530107")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "DATA_HOLD")
        self.assertIsNone(result["price"])
        self.assertEqual(result["stock_code"], "Q530107")


if __name__ == "__main__":
    unittest.main()
