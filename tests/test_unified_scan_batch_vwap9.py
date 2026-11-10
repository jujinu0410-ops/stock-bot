import unittest
from unittest.mock import patch

from services import unified_scan_m30 as scan_module
from services import youtube_signal_service_m30 as service_module


class UnifiedScanBatchVwap9Tests(unittest.TestCase):
    def test_batch_transports_official_daily_vwap9_to_apps_script(self):
        scan_result = {
            "ok": True,
            "engine_version": "three-lists-v3-m30",
            "stage": "WATCH",
            "send_allowed": False,
            "events": [],
            "reasons": [],
            "live_price": 110.0,
            "live_change_pct": 0.01,
            "live_price_source": "KIWOOM_KA10001",
            "atr14": 2.0,
            "session": "KRX_SESSION",
            "confidence_adjustment": 0,
            "bar_interval_minutes": 30,
            "contract_version": "KRX_M30_V1",
            "technical": {
                "basis": "COMPLETED_DAILY",
                "as_of": "2026-11-09 15:30:00",
                "vwap9": 105.0,
                "regime": "BULL_CONFIRMED",
                "stage": "WATCH",
                "provisional": {"available": True, "basis": "INCOMPLETE_DAILY_NOT_CONFIRMED"},
            },
            "m30": {"available": True, "gold": True, "vwap9": 108.0, "vwap26": 100.0},
        }
        with patch.object(service_module, "_secret_ok", return_value=True), \
             patch.object(scan_module, "scan", return_value=scan_result):
            response = service_module.app.test_client().post(
                "/unified-scan-batch", json={"items": [{"ticker": "000660"}]}
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        wire = body["results"][0]["result"]
        self.assertEqual(wire["technical"]["basis"], "COMPLETED_DAILY")
        self.assertEqual(wire["technical"]["as_of"], "2026-11-09 15:30:00")
        self.assertEqual(wire["technical"]["vwap9"], 105.0)
        self.assertEqual(wire["m30"]["vwap9"], 108.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
