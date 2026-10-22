"""Offline regression checks for the three market-wide watch exceptions."""
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
import pandas as pd

from services import unified_scan as us
from services import youtube_signal_service as svc


class MarketInverseEarlyTests(unittest.TestCase):
    def setUp(self):
        us._CACHE.clear()
        self.now = datetime(2026, 10, 22, 14, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        self.frame = pd.DataFrame({
            "Open": [100.0] * 30, "High": [101.0] * 30,
            "Low": [99.0] * 30, "Close": [100.0] * 30,
            "Volume": [100000] * 30,
        }, index=pd.date_range("2026-09-23", periods=30, freq="D", tz="Asia/Seoul"))

    def run_scan(self, code, own_pct, proxy_pct=-0.034, *, source="ETF",
                 rising=True, available=True):
        class Session:
            retry_count = 0
            def __init__(self, max_attempts=3): pass
        own_proxy = us.INDEX_INVERSE_PROXIES.get(code, "069500")
        def quote(session, token, ticker):
            pct = own_pct if ticker == code else proxy_pct
            return {"available": available, "current_price": 150.0 if ticker == code else 90.0,
                    "change_pct": pct, "source": "FAKE_LIVE_KIWOOM"}
        daily = {"stage": "BUY_SETUP", "regime": "EARLY_IMPROVEMENT",
                 "vwap_rel": "GOLD", "vwap9_dir": "RISING" if rising else "FALLING"}
        with patch.object(svc, "RetryingKiwoomSession", Session), \
             patch.object(svc, "_get_kiwoom_token", return_value="tok"), \
             patch.object(svc, "_fetch_kiwoom_quote", side_effect=quote), \
             patch.object(svc, "_fetch_kiwoom_minute_bars",
                          return_value={"available": False, "bars": []}), \
             patch.object(svc, "get_strictly_sliced_45m_df",
                          return_value=(None, "NONE", "MISSING")), \
             patch.object(us, "daily_frame", return_value=self.frame), \
             patch.object(us, "atr_and_anchor", return_value=(10.0, 105.0, 100.0)), \
             patch.object(us, "indicators", return_value=daily), \
             patch.object(us.KRXCalendar, "is_krx_trading_day", return_value=True):
            return us.scan({
                "ticker": code, "sources": [source],
                "market": "KOSDAQ" if code == "251340" else "KOSPI",
                "inverse": code in us.INDEX_INVERSE_PROXIES,
                "evaluate_jev": True,
            }, svc, now=self.now)

    def test_kospi_inverse_early_overheat_and_45m_unavailable(self):
        r = self.run_scan("114800", .0358)
        self.assertEqual(r["stage"], "BUY_SETUP")
        self.assertTrue(r["send_allowed"])
        self.assertIn("ABOVE_OVERHEAT_LIMIT", r["reasons"])
        self.assertIn("45M_UNAVAILABLE", r["reasons"])
        self.assertEqual(r["jev_gate"]["status"], "EARLY_MARKET_INFO")
        self.assertEqual(r["events"][0]["key"], "BUY_SETUP")
        self.assertIn("매수확인 아님", r["events"][0]["reason"])

    def test_kosdaq_inverse_early(self):
        r = self.run_scan("251340", .0341)
        self.assertEqual(r["stage"], "BUY_SETUP")
        self.assertTrue(r["send_allowed"])

    def test_inverse_requires_market_fall_and_live_price(self):
        self.assertEqual(self.run_scan("114800", .0358, proxy_pct=.015)["stage"], "WATCH")
        self.assertEqual(self.run_scan("114800", .005, proxy_pct=-.03)["stage"], "WATCH")
        self.assertEqual(self.run_scan("114800", .0358, rising=False)["stage"], "WATCH")
        r = self.run_scan("114800", .0358, available=False)
        self.assertFalse(r["send_allowed"])

    def test_ordinary_etf_no_exception(self):
        r = self.run_scan("123456", .0358)
        self.assertEqual(r["stage"], "WATCH")
        self.assertFalse(r["send_allowed"])

    def test_market_crash_and_early(self):
        r = self.run_scan("069500", -.034)
        self.assertEqual(r["stage"], "RISK")
        self.assertTrue(r["send_allowed"])
        self.assertIn("MARKET_CRASH", [x["key"] for x in r["events"]])
        r = self.run_scan("069500", -.018)
        self.assertIn("MARKET_WEAK_EARLY", [x["key"] for x in r["events"]])
        self.assertTrue(r["send_allowed"])
        r = self.run_scan("069500", -.014)
        self.assertNotIn("MARKET_WEAK_EARLY", [x["key"] for x in r["events"]])


if __name__ == "__main__":
    unittest.main()
