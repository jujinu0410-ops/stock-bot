# -*- coding: utf-8 -*-
import unittest

from src.analysis.youtube_candidate_review import (
    KiwoomAfterCloseMarketRegimeReader,
    decide_notification,
    describe_flow,
    market_code_from_market_type,
    render_candidate_preview,
)


class _Resp:
    def __init__(self, payload, headers=None, status=200):
        self._payload = payload
        self.headers = headers or {}
        self.status_code = status

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers, json, timeout))
        return self.responses.pop(0)


class CandidateReviewTests(unittest.TestCase):
    def test_describe_negative_mixed_flow_is_not_called_broad_selling(self):
        text = describe_flow({
            "status": "NEGATIVE",
            "foreign_5d": -40746,
            "institution_5d": 37495,
            "combined_5d": -3251,
            "combined_20d": -41594,
        })
        self.assertIn("대부분 흡수", text)
        self.assertNotIn("광범위", text)
        self.assertNotIn("동반 순매도", text)

    def test_describe_negative_both_sellers(self):
        text = describe_flow({
            "status": "NEGATIVE",
            "foreign_5d": -10,
            "institution_5d": -20,
            "combined_5d": -30,
            "combined_20d": -100,
        })
        self.assertIn("동반 순매도", text)

    def test_notification_first_buy_state_sends(self):
        d = decide_notification("BUY_ALERT", previous_signal="WATCH_ONLY")
        self.assertTrue(d.would_send)

    def test_notification_repeat_same_cycle_suppressed(self):
        d = decide_notification("BUY_ALERT_STRONG", previous_signal="BUY_ALERT", new_ready_cycle=False)
        self.assertFalse(d.would_send)
        self.assertIn("duplicate", d.reason)

    def test_notification_new_cycle_requires_cooldown(self):
        self.assertFalse(decide_notification(
            "BUY_ALERT", previous_signal="BUY_ALERT", new_ready_cycle=True,
            trading_days_since_last=4, cooldown_trading_days=5,
        ).would_send)
        self.assertTrue(decide_notification(
            "BUY_ALERT", previous_signal="BUY_ALERT", new_ready_cycle=True,
            trading_days_since_last=5, cooldown_trading_days=5,
        ).would_send)

    def test_watch_only_never_mail_worthy(self):
        d = decide_notification("WATCH_ONLY")
        self.assertFalse(d.would_send)

    def test_market_type_mapping(self):
        self.assertEqual(market_code_from_market_type("KOSPI"), ("KOSPI", "001"))
        self.assertEqual(market_code_from_market_type("KOSDAQ"), ("KOSDAQ", "101"))
        self.assertIsNone(market_code_from_market_type("ETF"))

    def test_market_reader_aggregates_all_pages(self):
        session = _Session([
            _Resp(
                {"opaf_invsr_trde": [
                    {"frgnr_invsr": "+100", "orgn": "-20"},
                    {"frgnr_invsr": "-10", "orgn": "+5"},
                ], "return_code": 0},
                {"cont-yn": "Y", "next-key": "abc"},
            ),
            _Resp(
                {"opaf_invsr_trde": [
                    {"frgnr_invsr": "+30", "orgn": "+40"},
                ], "return_code": 0},
                {"cont-yn": "N", "next-key": ""},
            ),
        ])
        r = KiwoomAfterCloseMarketRegimeReader("token", session).fetch(
            "KOSPI", "001", page_delay=0,
        )
        self.assertTrue(r.complete)
        self.assertEqual(r.row_count, 3)
        self.assertEqual(r.foreign_amount, 120)
        self.assertEqual(r.institution_amount, 25)
        self.assertEqual(r.status, "POSITIVE")
        self.assertEqual(session.calls[1][1]["next-key"], "abc")

    def test_preview_watch_only_contains_refined_flow(self):
        candidate = {
            "ticker": "007660", "name": "이수페타시스", "last_seen_date": "2026-09-23",
            "mention_count_30d": 1,
            "technical": {"status": "BUY_CANDIDATE", "rebound_atr": 0.54045,
                          "current_price": 115400, "atr14": 7401.22,
                          "buy_trigger_05": 115100.61, "confirm_trigger_06": 115840.73},
            "flow": {"status": "NEGATIVE", "foreign_5d": -40746,
                     "institution_5d": 37495, "combined_5d": -3251,
                     "combined_20d": -41594},
            "final": {"signal": "WATCH_ONLY", "market_regime": "UNAVAILABLE"},
        }
        text = render_candidate_preview(candidate)
        self.assertIn("WATCH_ONLY", text)
        self.assertIn("대부분 흡수", text)
        self.assertIn("매수 검토용 preview", text)


if __name__ == "__main__":
    unittest.main()
