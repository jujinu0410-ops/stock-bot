# -*- coding: utf-8 -*-
import os
import unittest

from src.analysis.jev_alert_gate import JevGateSettings, evaluate_jev_alert_gate


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class JevAlertGateTest(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "source": "HELD",
            "ticker": "058470",
            "name": "리노공업",
            "as_of_date": "2026-10-03",
            "event_keys": ["SIGNAL_EARLY_UP"],
            "event_level": "WATCH",
            "current_price": 100000,
            "change_pct": 0.02,
            "regime": "EARLY_IMPROVEMENT",
        }
        self.body = {
            "model": "jev-latest",
            "answers": {
                "signal_direction": {"type": "choice", "choice": "UP", "confidence": 0.9, "probabilities": {"UP": 0.9}},
                "signal_stage": {"type": "choice", "choice": "BUY_WATCH", "confidence": 0.8, "probabilities": {"BUY_WATCH": 0.8}},
                "alert_urgency": {"type": "score", "score": 3.1, "confidence": 0.8, "probabilities": {"3": 0.9}},
                "send_alert_now": {"type": "noul", "noul": 0.85},
                "signal_deteriorating": {"type": "noul", "noul": 0.1},
            },
            "usage": {"input_tokens": 100, "output_tokens": 10},
        }

    def test_shadow_records_but_never_suppresses(self):
        session = FakeSession(FakeResponse(200, self.body))
        result = evaluate_jev_alert_gate(
            self.payload,
            settings=JevGateSettings(api_key="test", mode="SHADOW"),
            session=session,
        )
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["decision"], "SEND")
        self.assertEqual(result["reason"], "SHADOW_RECORD_ONLY")
        args, kwargs = session.calls[0]
        self.assertEqual(args[0], "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(kwargs["json"]["model"], "jev-latest")
        self.assertIn("send_alert_now", kwargs["json"]["questions"])
        self.assertNotIn("test", str(kwargs["json"]["state"]))

    def test_active_holds_low_probability_candidate(self):
        self.body["answers"]["alert_urgency"]["score"] = 1.0
        self.body["answers"]["send_alert_now"]["noul"] = 0.3
        result = evaluate_jev_alert_gate(
            self.payload,
            settings=JevGateSettings(api_key="test", mode="ACTIVE"),
            session=FakeSession(FakeResponse(200, self.body)),
        )
        self.assertEqual(result["decision"], "HOLD")
        self.assertEqual(result["reason"], "ACTIVE_THRESHOLD_NOT_MET")

    def test_stop_loss_bypasses_active_suppression(self):
        self.payload["event_keys"] = ["STOP_BREACH"]
        self.body["answers"]["alert_urgency"]["score"] = 0.0
        self.body["answers"]["send_alert_now"]["noul"] = 0.0
        result = evaluate_jev_alert_gate(
            self.payload,
            settings=JevGateSettings(api_key="test", mode="ACTIVE"),
            session=FakeSession(FakeResponse(200, self.body)),
        )
        self.assertEqual(result["decision"], "SEND")
        self.assertEqual(result["reason"], "HARD_RISK_BYPASS")

    def test_missing_key_and_bad_response_fail_open(self):
        missing = evaluate_jev_alert_gate(self.payload, settings=JevGateSettings(api_key="", mode="ACTIVE"))
        self.assertEqual((missing["status"], missing["decision"]), ("NOT_CONFIGURED", "SEND"))
        broken = evaluate_jev_alert_gate(
            self.payload,
            settings=JevGateSettings(api_key="test", mode="ACTIVE"),
            session=FakeSession(FakeResponse(500, {})),
        )
        self.assertEqual((broken["status"], broken["decision"]), ("ERROR", "SEND"))

    def test_youtube_can_be_active_while_other_sources_stay_shadow(self):
        old = dict(os.environ)
        try:
            os.environ["JEV_ALERT_GATE_MODE"] = "SHADOW"
            os.environ["JEV_ALERT_GATE_YOUTUBE_MODE"] = "ACTIVE"
            self.assertEqual(JevGateSettings.from_env("YOUTUBE").mode, "ACTIVE")
            self.assertEqual(JevGateSettings.from_env("ETF").mode, "SHADOW")
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
