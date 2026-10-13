import unittest
from services.unified_signal_engine import clean_code, normalize_sources, normalize_stage, evaluate_signal

class UnifiedSignalEngineTests(unittest.TestCase):
    def test_three_sources_are_same_stock_and_same_buy_setup(self):
        for origin, stage in [
            ("HELD", "SIGNAL_EARLY_UP"),
            ("ETF", "△ 상승조짐"),
            ("YOUTUBE", "BUY_CANDIDATE"),
        ]:
            out = evaluate_signal({"ticker":"69500", "source":origin, "stage":stage,
                "market":"KOSPI", "market_pct":0.02, "sector_etf_pct":0.01})
            self.assertEqual(out["ticker"],"069500")
            self.assertEqual(out["stage"],"BUY_SETUP")
            self.assertEqual(out["confidence_adjustment"],1)
            self.assertEqual(out["signal_label"],"매수조짐")
            self.assertTrue(out["send_allowed"])

    def test_multiple_sources_dedupe_identity(self):
        r=evaluate_signal({"ticker":"069500","sources":["ETF","HELD","YOUTUBE","ETF"],"stage":"▲"})
        self.assertEqual(r["sources"],["ETF","HELD","YOUTUBE"])
        self.assertEqual(r["stage"],"BUY_CONFIRMED")

    def test_market_down_sector_down_holds_buy_confidence(self):
        r=evaluate_signal({"ticker":"373220","source":"YOUTUBE","stage":"BUY_CANDIDATE",
                           "market":"KOSPI","market_pct":-0.069,"sector_etf_pct":-0.055})
        self.assertEqual(r["confidence_adjustment"],-1)
        self.assertEqual(r["stage"],"BUY_SETUP")

    def test_up_market_down_sector_is_not_aggressive(self):
        r=evaluate_signal({"stage":"▲","market":"KOSPI","market_pct":0.04,"sector_etf_pct":-0.05})
        self.assertEqual(r["confidence_adjustment"],0)

    def test_down_market_up_sector_lets_stock_prove_itself(self):
        r=evaluate_signal({"stage":"BUY_CANDIDATE","market":"KOSDAQ",
                           "market_pct":-0.03,"sector_etf_pct":0.04})
        self.assertEqual(r["confidence_adjustment"],0)

    def test_inverse_is_positive_in_falling_market(self):
        r=evaluate_signal({"source":"ETF","ticker":"114800","stage":"△","market":"KOSPI",
                           "market_pct":-0.069, "sector_etf_pct":0.074,"inverse":True})
        self.assertEqual(r["confidence_adjustment"],1)

    def test_risk_never_suppressed_by_market(self):
        for stage in ["▼","OBV15_WARNING","STOP_BREACH","ETF_SIGNAL_▼"]:
            r=evaluate_signal({"stage":stage,"market":"KOSPI","market_pct":0.08,
                               "sector_etf_pct":0.02})
            # A risk/downstage may not be suppressed because the index is rising.
            self.assertIn(r["stage"],("DOWN_CONFIRMED","RISK"))
            self.assertEqual(r["confidence_adjustment"],0)
            self.assertTrue(r["send_allowed"])

    def test_no_fake_technical_confirmation(self):
        r=evaluate_signal({"stage":"△","market":"KOSPI","market_pct":0.03,
                           "sector_etf_pct":0.03,"technical_verified":False})
        self.assertEqual(r["stage"],"BUY_SETUP")
        self.assertEqual(r["confidence_adjustment"],1)
        self.assertFalse(r["send_allowed"])

    def test_live_quote_missing_cannot_send(self):
        r=evaluate_signal({"stage":"BUY_CANDIDATE","market":"KOSPI",
                           "market_pct":0.03,"live_verified":False})
        self.assertFalse(r["send_allowed"])

    def test_unknown_market_neutral(self):
        r=evaluate_signal({"stage":"△","market":"NASDAQ","market_pct":0.07})
        self.assertEqual(r["confidence_adjustment"],0)

    def test_small_move_not_considered_trend(self):
        r=evaluate_signal({"stage":"△","market":"KOSPI","market_pct":0.003})
        self.assertEqual(r["confidence_adjustment"],0)

    def test_source_ticker_normalizers(self):
        self.assertEqual(clean_code("KRX:069500"),"069500")
        self.assertEqual(normalize_stage("▼ 하락확인"),"DOWN_CONFIRMED")
        self.assertEqual(normalize_sources(["held","ETF","held"]),["ETF","HELD"])

if __name__=="__main__": unittest.main()
