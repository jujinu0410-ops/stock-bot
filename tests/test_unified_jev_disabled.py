import unittest
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import Mock, patch

import pandas as pd

from services import unified_scan_m30 as m30
from src.analysis import jev_alert_gate


class UnifiedJevDisabledTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.now = datetime.fromisoformat("2026-11-19T13:35:00+09:00")
        self.payload = {"ticker": "000660", "sources": ["HELD"], "evaluate_jev": True}
        self.service = Mock()
        self.service._clean_ticker.side_effect = str
        self.service.RetryingKiwoomSession.return_value.retry_count = 0
        self.service._fetch_kiwoom_quote.return_value = {
            "available": True, "current_price": 110, "source": "KIWOOM_KA10001", "change_pct": .01,
        }
        self.service._fetch_kiwoom_minute_bars.return_value = {
            "available": True, "bars": [{"time": "20261119133500", "close": 110}],
        }
        self.service._analyze_30m_exit_risk_df.return_value = {}
        self.official = {"stage": "BUY_CONFIRMED", "basis": "COMPLETED_DAILY",
                         "as_of": "2026-11-18 15:30:00", "vwap9": 105}
        daily = pd.DataFrame({"High": [111]}, index=pd.to_datetime(["2026-11-18"]))

        def cache(key, ttl, loader, **kwargs):
            if isinstance(key, tuple) and key[0] == "30m-kiwoom":
                cutoff = pd.Timestamp(key[2]).tz_localize(None)
                frame = pd.DataFrame(index=pd.date_range(end=cutoff, periods=28, freq="30min"))
                return frame, "KIWOOM_15M_BAR_START", "VALID"
            return loader()

        for name, options in {
            "cached": {"side_effect": cache}, "daily_frame": {"return_value": daily},
            "daily_states": {"return_value": (self.official, {})},
            "atr_and_anchor": {"return_value": (10., 103., 108.)},
            "indicators": {"return_value": {"vwap_rel": "GOLD", "vwap9": 108., "vwap26": 102.}},
            "analyze_obv15_sell_warning": {"return_value": {}},
        }.items():
            stack.enter_context(patch.object(m30, name, **options))
        stack.enter_context(patch.dict("os.environ", {"JEV_API_KEY": "offline", "JEV_ALERT_GATE_UNIFIED_MODE": "ACTIVE"}))
        self.post = stack.enter_context(patch.object(jev_alert_gate.requests, "post"))

    def scan(self):
        return m30.scan(self.payload, self.service, self.now)

    def test_old_hold_and_outage_do_not_call_provider(self):
        self.post.return_value.status_code = 200
        self.post.return_value.json.return_value = {"answers": {}}
        for failure in (None, ConnectionError("offline")):
            with self.subTest(failure=failure):
                self.post.side_effect = failure
                result = self.scan()
                self.assertEqual(result["stage"], "BUY_CONFIRMED")
                self.assertTrue(result["send_allowed"])
                self.assertEqual(result["jev_gate"], {"status": "JEV_GATE_DISABLED", "decision": "NOT_EVALUATED"})
                self.post.assert_not_called()

    def test_technical_denial_survives(self):
        original = m30.evaluate_signal
        with patch.object(m30, "evaluate_signal", side_effect=lambda p: {**original(p), "send_allowed": False}):
            result = self.scan()
        self.assertEqual(result["stage"], "BUY_CONFIRMED")
        self.assertFalse(result["send_allowed"])
        self.post.assert_not_called()

    def test_unqualified_and_missing_daily_vwap9(self):
        for value in (110, None):
            self.official["vwap9"] = value
            result = self.scan()
            self.assertFalse(result["send_allowed"])
            self.assertEqual(result["stage"], "WATCH")
        self.post.assert_not_called()

    def test_stop_preempts_buy(self):
        self.payload["stop_price"] = 112
        result = self.scan()
        self.assertEqual(result["stage"], "RISK")
        self.assertTrue(result["send_allowed"])
        self.assertEqual([e["key"] for e in result["events"]], ["STOP_BREACH"])
        self.post.assert_not_called()

    def test_recovery_uses_timestamped_kiwoom_price_and_does_not_call_jev(self):
        self.payload["stop_notified"] = True
        minute = self.service._fetch_kiwoom_minute_bars.return_value
        minute["source"] = "KIWOOM_KA10080"
        minute["bars"] = [{"time": "20261119133400", "close": 111}]
        self.assertEqual(self.scan()["breakeven_observation"],
                         {"price": 111, "as_of": "2026-11-19T13:34:00+09:00"})
        for timestamp, price in (("20261119133100", 111), ("20261119133600", 111),
                                 ("20261118133400", 111), ("20261119133400", True),
                                 ("20261119133400", "111"), ("20261119133400", float("inf"))):
            with self.subTest(timestamp=timestamp, price=price):
                minute["bars"] = [{"time": timestamp, "close": price}]
                self.assertIsNone(self.scan()["breakeven_observation"])
        self.post.assert_not_called()
