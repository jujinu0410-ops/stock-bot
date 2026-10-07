import os
import unittest
from unittest.mock import patch

from services import youtube_signal_service as svc


class YouTubeSignalServiceTest(unittest.TestCase):
    def setUp(self):
        self.old_token = os.environ.get("YOUTUBE_SIGNAL_API_TOKEN")
        os.environ["YOUTUBE_SIGNAL_API_TOKEN"] = "test-token"
        self.client = svc.app.test_client()

    def tearDown(self):
        if self.old_token is None:
            os.environ.pop("YOUTUBE_SIGNAL_API_TOKEN", None)
        else:
            os.environ["YOUTUBE_SIGNAL_API_TOKEN"] = self.old_token

    def test_health(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["ok"])
        self.assertIn("/jev-gate", res.get_json()["endpoints"])

    def test_confirm_requires_shared_token(self):
        res = self.client.post("/confirm", json={"ticker": "007660", "tech_status": "BUY_CANDIDATE"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"], "UNAUTHORIZED")

    @patch.object(svc, "_confirm")
    def test_confirm_passes_only_authenticated_payload(self, mocked):
        mocked.return_value = {
            "ok": True,
            "ticker": "007660",
            "final_signal": "WATCH_ONLY",
            "mail_sent": True,
        }
        res = self.client.post(
            "/confirm",
            headers={"X-StockBot-Token": "test-token"},
            json={"ticker": "007660", "name": "이수페타시스", "tech_status": "BUY_CANDIDATE"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["ok"])
        mocked.assert_called_once()

    def test_ticker_validation(self):
        self.assertEqual(svc._clean_ticker("007660"), "007660")
        with self.assertRaises(svc.SignalServiceError):
            svc._clean_ticker("ABC")

    @patch.object(svc, "_fetch_45m_vwap_gate")
    @patch.object(svc, "_fetch_daily_obv_gate")
    def test_entry_timing_veto_when_daily_obv_and_45m_vwap_both_not_gold(self, daily, vwap):
        daily.return_value = {"available": True, "gold": False, "trend_down": True}
        vwap.return_value = {"available": True, "gold": False, "state": "VWAP_DEAD"}
        gate = svc._evaluate_entry_timing_veto("316140")
        self.assertEqual(gate["decision"], "VETO")
        self.assertEqual(gate["reason"], "DAILY_OBV_NOT_GOLD_AND_45M_VWAP_NOT_GOLD")

    @patch.object(svc, "_fetch_45m_vwap_gate")
    @patch.object(svc, "_fetch_daily_obv_gate")
    def test_entry_timing_gate_passes_if_either_signal_is_gold(self, daily, vwap):
        daily.return_value = {"available": True, "gold": False}
        vwap.return_value = {"available": True, "gold": True, "state": "VWAP_GOLD"}
        gate = svc._evaluate_entry_timing_veto("316140")
        self.assertEqual(gate["decision"], "PASS")


    def test_strong_confirmation_gate_requires_45m_uptrend(self):
        strong = svc.FinalSignalAssessment(
            svc.FINAL_BUY_ALERT_STRONG,
            svc.TECH_BUY_CANDIDATE,
            "POSITIVE",
            "UNAVAILABLE",
            "confirmed",
        )
        final, gate = svc._apply_strong_confirmation_gate(
            strong,
            {"fresh_price": 214000, "quote": {"open": 231000}},
            {"vwap_45m": {"available": True, "gold": False, "state": "VWAP_DEAD"}},
        )
        self.assertEqual(final.final_signal, svc.FINAL_BUY_ALERT)
        self.assertEqual(gate["decision"], "DOWNGRADE_TO_BUY_ALERT")
        self.assertIn("45M_VWAP_NOT_GOLD", gate["reason"])
        self.assertIn("INTRADAY_DROP_GE_5PCT", gate["reason"])

    def test_strong_confirmation_gate_passes_when_45m_uptrend_and_no_crash(self):
        strong = svc.FinalSignalAssessment(
            svc.FINAL_BUY_ALERT_STRONG,
            svc.TECH_BUY_CANDIDATE,
            "POSITIVE",
            "UNAVAILABLE",
            "confirmed",
        )
        final, gate = svc._apply_strong_confirmation_gate(
            strong,
            {"fresh_price": 105000, "quote": {"open": 104000}},
            {"vwap_45m": {"available": True, "gold": True, "state": "VWAP_GOLD"}},
        )
        self.assertEqual(final.final_signal, svc.FINAL_BUY_ALERT_STRONG)
        self.assertEqual(gate["decision"], "PASS_STRONG")

    def test_watch_only_is_never_mail_worthy(self):
        self.assertNotIn(svc.FINAL_WATCH_ONLY, svc.ALLOWED_FINAL_SIGNALS)
        self.assertEqual(
            svc.ALLOWED_FINAL_SIGNALS,
            {svc.FINAL_BUY_ALERT, svc.FINAL_BUY_ALERT_STRONG},
        )

    @patch.object(svc, "_fetch_kiwoom_minute_bars")
    @patch.object(svc, "_fetch_kiwoom_quote")
    def test_freshness_veto_when_price_drops_below_buy_trigger(self, quote, minute):
        quote.return_value = {"available": True, "current_price": 950.0, "source": "KIWOOM_KA10001"}
        minute.return_value = {
            "available": True,
            "source": "KIWOOM_KA10080",
            "bars": [
                {"time": "100000", "close": 1010.0, "high": 1012.0, "low": 1008.0, "volume": 100},
                {"time": "100100", "close": 1005.0, "high": 1007.0, "low": 1002.0, "volume": 100},
                {"time": "100200", "close": 990.0, "high": 1000.0, "low": 988.0, "volume": 100},
                {"time": "100300", "close": 970.0, "high": 990.0, "low": 968.0, "volume": 100},
                {"time": "100400", "close": 955.0, "high": 972.0, "low": 950.0, "volume": 100},
            ],
        }
        gate = svc._evaluate_kiwoom_freshness_gate(
            "009150",
            signal_price=1000.0,
            atr14=100.0,
            buy_trigger_05=980.0,
            session=object(),
            token="token",
        )
        self.assertEqual(gate["decision"], "VETO")
        self.assertEqual(gate["reason"], "FRESH_PRICE_BELOW_BUY_TRIGGER")

    @patch.object(svc, "_fetch_kiwoom_minute_bars")
    @patch.object(svc, "_fetch_kiwoom_quote")
    def test_freshness_passes_when_signal_is_still_alive(self, quote, minute):
        quote.return_value = {"available": True, "current_price": 1020.0, "source": "KIWOOM_KA10001"}
        minute.return_value = {
            "available": True,
            "source": "KIWOOM_KA10080",
            "bars": [
                {"time": "100000", "close": 995.0, "high": 998.0, "low": 992.0, "volume": 100},
                {"time": "100100", "close": 1000.0, "high": 1003.0, "low": 998.0, "volume": 120},
                {"time": "100200", "close": 1005.0, "high": 1008.0, "low": 1002.0, "volume": 130},
                {"time": "100300", "close": 1010.0, "high": 1013.0, "low": 1008.0, "volume": 150},
                {"time": "100400", "close": 1018.0, "high": 1020.0, "low": 1015.0, "volume": 170},
            ],
        }
        gate = svc._evaluate_kiwoom_freshness_gate(
            "009150",
            signal_price=1000.0,
            atr14=100.0,
            buy_trigger_05=980.0,
            session=object(),
            token="token",
        )
        self.assertEqual(gate["decision"], "PASS")
        self.assertEqual(gate["reason"], "FRESH_SIGNAL_ALIVE")
        self.assertEqual(gate["direction"], "RISING")

    @patch.object(svc.KiwoomInvestorFlowReader, "fetch_stock_flow")
    @patch.object(svc, "_evaluate_kiwoom_freshness_gate")
    @patch.object(svc, "_get_kiwoom_token")
    def test_confirm_watch_only_never_sends_mail(self, token, freshness, fetch_flow):
        token.return_value = "token"
        freshness.return_value = {
            "decision": "PASS",
            "reason": "FRESH_SIGNAL_ALIVE",
            "fresh_price": 1020.0,
        }
        fetch_flow.return_value = [
            {
                "dt": f"202609{day:02d}",
                "foreign_amount": -100.0,
                "institution_amount": -100.0,
                "turnover_amount": 1000.0,
            }
            for day in range(1, 21)
        ]
        result = svc._confirm(
            {
                "ticker": "009150",
                "name": "삼성전기",
                "tech_status": "BUY_CANDIDATE",
                "current_price": 1000.0,
                "atr14": 100.0,
                "buy_trigger_05": 980.0,
                "confirm_trigger_06": 990.0,
                "as_of_date": "2026-10-07",
            }
        )
        self.assertEqual(result["final_signal"], svc.FINAL_WATCH_ONLY)
        self.assertFalse(result["mail_sent"])
        self.assertTrue(result["suppressed_non_mail_signal"])

    @patch.object(svc.KiwoomInvestorFlowReader, "fetch_stock_flow")
    @patch.object(svc, "_evaluate_kiwoom_freshness_gate")
    @patch.object(svc, "_get_kiwoom_token")
    def test_confirm_freshness_veto_short_circuits_flow(self, token, freshness, fetch_flow):
        token.return_value = "token"
        freshness.return_value = {
            "decision": "VETO",
            "reason": "RECENT_1M_SHARP_FALL",
            "fresh_price": 950.0,
        }
        result = svc._confirm(
            {
                "ticker": "009150",
                "name": "삼성전기",
                "tech_status": "BUY_CANDIDATE",
                "current_price": 1000.0,
                "atr14": 100.0,
                "buy_trigger_05": 980.0,
                "as_of_date": "2026-10-07",
            }
        )
        self.assertFalse(result["mail_sent"])
        self.assertTrue(result["suppressed_by_freshness_gate"])
        fetch_flow.assert_not_called()

    @patch.object(svc, "_jev_gate")
    def test_jev_gate_uses_existing_shared_token(self, mocked):
        mocked.return_value = {"ok": True, "status": "NOT_CONFIGURED", "decision": "SEND"}
        res = self.client.post(
            "/jev-gate",
            headers={"X-StockBot-Token": "test-token"},
            json={"source": "ETF", "ticker": "069500", "name": "KODEX 200"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["decision"], "SEND")
        mocked.assert_called_once()

    def test_user_facing_signal_language_matches_held_monitor(self):
        self.assertEqual(svc.SIGNAL_LABELS[svc.FINAL_WATCH_ONLY], "· 관찰")
        self.assertEqual(svc.SIGNAL_LABELS[svc.FINAL_BUY_ALERT], "△ 매수조짐")
        self.assertEqual(svc.SIGNAL_LABELS[svc.FINAL_BUY_ALERT_STRONG], "▲ 매수확인")
        self.assertEqual(svc.SIGNAL_COLORS[svc.FINAL_WATCH_ONLY], "#64748B")
        self.assertEqual(svc.SIGNAL_COLORS[svc.FINAL_BUY_ALERT], "#DC2626")
        self.assertEqual(svc.SIGNAL_COLORS[svc.FINAL_BUY_ALERT_STRONG], "#DC2626")


if __name__ == "__main__":
    unittest.main()
