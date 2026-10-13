import unittest
from unittest.mock import patch
from services import youtube_signal_service as service

class UnifiedSignalEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client=service.app.test_client()

    @patch.object(service, "_get_kiwoom_token", return_value="test-token")
    @patch.object(service, "_fetch_kiwoom_quote")
    def test_three_watchlist_sources_share_stage_and_live_market(self, quote, token):
        def mock_quote(session, auth, ticker):
            changes={"069500":0.019,"373220":0.037,"091160":0.034}
            return {"available":True, "current_price":390000.0 if ticker=="373220" else 100000.0,
                    "change_pct":changes[ticker],"source":"KIWOOM_KA10001"}
        quote.side_effect=mock_quote
        cases=[("HELD","SIGNAL_EARLY_UP"),("ETF","△ 상승조짐"),("YOUTUBE","BUY_CANDIDATE")]
        for source,stage in cases:
            r=service._unified_evaluate({"ticker":"373220","source":source,"stage":stage,
                "market":"KOSPI","sector_etf_code":"091160","technical_verified":True})
            self.assertEqual(r["stage"],"BUY_SETUP")
            self.assertEqual(r["confidence_adjustment"],1)
            self.assertEqual(r["live_price_source"],"KIWOOM_KA10001")
            self.assertTrue(r["send_allowed"])
            self.assertEqual(quote.call_count % 3,0)

    @patch.object(service, "_get_kiwoom_token", return_value="token")
    @patch.object(service, "_fetch_kiwoom_quote")
    def test_market_fall_confidence_reduces_but_risk_stays(self, quote, token):
        quote.side_effect=lambda sess, tok, ticker: {
            "available":True,"current_price":90000.0,"change_pct":-0.067,
            "source":"KIWOOM_KA10001"}
        r=service._unified_evaluate({"source":"ETF","ticker":"069500",
            "market":"KOSPI","stage":"▼","technical_verified":True})
        self.assertEqual(r["stage"],"DOWN_CONFIRMED")
        self.assertEqual(r["confidence_adjustment"],0)
        self.assertTrue(r["send_allowed"])

    @patch.object(service, "_get_kiwoom_token", return_value="token")
    @patch.object(service, "_fetch_kiwoom_quote")
    def test_missing_quote_fail_closed_buy(self, quote, token):
        quote.return_value={"available":False, "reason":"KIWOOM_DOWN"}
        r=service._unified_evaluate({"source":"YOUTUBE","ticker":"373220",
            "market":"KOSPI","stage":"BUY_CANDIDATE"})
        self.assertFalse(r["send_allowed"])
        self.assertIsNone(r["live_price"])
        self.assertEqual(r["confidence_adjustment"],0)

    @patch.object(service, "_secret_ok", return_value=True)
    @patch.object(service, "_unified_evaluate", return_value={"ok":True,"stage":"BUY_SETUP"})
    def test_http_route_only_returns_json(self, evaluate, auth):
        resp=self.client.post("/unified-signal", json={
            "source":"HELD","ticker":"069500","stage":"SIGNAL_EARLY_UP"})
        self.assertEqual(resp.status_code,200)
        self.assertEqual(resp.json["stage"],"BUY_SETUP")
        evaluate.assert_called_once()

    @patch.object(service, "_secret_ok", return_value=False)
    def test_http_requires_token(self, auth):
        resp=self.client.post("/unified-signal",json={"ticker":"069500"})
        self.assertEqual(resp.status_code,401)

if __name__=="__main__":unittest.main()
