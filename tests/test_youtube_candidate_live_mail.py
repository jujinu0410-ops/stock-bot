# -*- coding: utf-8 -*-
import unittest

from scripts.youtube_candidate_live_mail import LIVE_MAIL_SIGNALS, decide_live_send


class YouTubeCandidateLiveMailTests(unittest.TestCase):
    def test_watch_only_is_live_mail_state(self):
        self.assertIn("WATCH_ONLY", LIVE_MAIL_SIGNALS)
        allowed, reason = decide_live_send("WATCH_ONLY", "007660", "2026-09-30", {})
        self.assertTrue(allowed)
        self.assertIn("first live mail", reason)

    def test_data_gap_is_not_live_mail_state(self):
        allowed, reason = decide_live_send("WATCH_ONLY_DATA_GAP", "007660", "2026-09-30", {})
        self.assertFalse(allowed)
        self.assertIn("not in LIVE mail states", reason)

    def test_same_ticker_signal_date_is_suppressed(self):
        state = {"007660": {"signal": "WATCH_ONLY", "as_of_date": "2026-09-30"}}
        allowed, reason = decide_live_send("WATCH_ONLY", "007660", "2026-09-30", state)
        self.assertFalse(allowed)
        self.assertIn("already sent", reason)

    def test_signal_change_same_date_sends_again(self):
        state = {"007660": {"signal": "WATCH_ONLY", "as_of_date": "2026-09-30"}}
        allowed, reason = decide_live_send("BUY_ALERT", "007660", "2026-09-30", state)
        self.assertTrue(allowed)
        self.assertIn("signal changed", reason)

    def test_same_signal_new_date_sends_daily_update(self):
        state = {"007660": {"signal": "WATCH_ONLY", "as_of_date": "2026-09-30"}}
        allowed, reason = decide_live_send("WATCH_ONLY", "007660", "2026-10-01", state)
        self.assertTrue(allowed)
        self.assertIn("new as-of date", reason)

    def test_buy_alert_states_are_live(self):
        for signal in ("BUY_ALERT", "BUY_ALERT_STRONG"):
            allowed, _ = decide_live_send(signal, "000660", "2026-09-30", {})
            self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
