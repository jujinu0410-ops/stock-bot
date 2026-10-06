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
