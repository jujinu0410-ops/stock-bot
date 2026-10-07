import unittest

from src.analysis.youtube_alert_outcome import evaluate_youtube_alert_outcome


class YouTubeAlertOutcomeTest(unittest.TestCase):
    def test_bitzro_like_move_is_s_grade(self):
        result = evaluate_youtube_alert_outcome(
            alert_price=11910,
            atr14=900,
            bars=[
                {"minutes_after_alert": 5, "high": 12050, "low": 11880, "close": 12020},
                {"minutes_after_alert": 20, "high": 12350, "low": 11950, "close": 12280},
                {"minutes_after_alert": 40, "high": 12520, "low": 12100, "close": 12480},
                {"minutes_after_alert": 60, "high": 12620, "low": 12200, "close": 12380},
            ],
        )
        self.assertEqual(result["grade"], "S")
        self.assertEqual(result["label"], "매우 좋은 알림")
        self.assertGreaterEqual(result["horizons"]["60m"]["mfe_pct"], 5.0)
        self.assertFalse(result["stop_before_tradeable_target"])

    def test_lg_like_weak_followthrough_is_c_grade(self):
        result = evaluate_youtube_alert_outcome(
            alert_price=214000,
            atr14=10460,
            bars=[
                {"minutes_after_alert": 5, "high": 214500, "low": 212000, "close": 212500},
                {"minutes_after_alert": 20, "high": 213000, "low": 210500, "close": 211000},
                {"minutes_after_alert": 40, "high": 212000, "low": 209000, "close": 210000},
                {"minutes_after_alert": 60, "high": 211000, "low": 208500, "close": 209500},
            ],
        )
        self.assertEqual(result["grade"], "C")
        self.assertEqual(result["label"], "나쁜 알림")

    def test_stop_before_target_caps_at_b(self):
        result = evaluate_youtube_alert_outcome(
            alert_price=10000,
            atr14=500,
            bars=[
                {"minutes_after_alert": 5, "high": 10050, "low": 9500, "close": 9600},
                {"minutes_after_alert": 30, "high": 10400, "low": 9550, "close": 10300},
                {"minutes_after_alert": 60, "high": 10600, "low": 10200, "close": 10500},
            ],
        )
        self.assertEqual(result["grade"], "B")
        self.assertTrue(result["stop_before_tradeable_target"])

    def test_a_grade_for_tradeable_three_percent_move(self):
        result = evaluate_youtube_alert_outcome(
            alert_price=10000,
            atr14=1000,
            bars=[
                {"minutes_after_alert": 10, "high": 10100, "low": 9950, "close": 10050},
                {"minutes_after_alert": 30, "high": 10350, "low": 10000, "close": 10300},
                {"minutes_after_alert": 60, "high": 10400, "low": 10100, "close": 10250},
            ],
        )
        self.assertEqual(result["grade"], "A")
        self.assertEqual(result["first_hit_min"]["plus_3pct_or_0_6atr"], 30)

    def test_b_grade_for_small_but_usable_move(self):
        result = evaluate_youtube_alert_outcome(
            alert_price=10000,
            atr14=1000,
            bars=[
                {"minutes_after_alert": 10, "high": 10080, "low": 9950, "close": 10020},
                {"minutes_after_alert": 30, "high": 10180, "low": 9970, "close": 10120},
                {"minutes_after_alert": 60, "high": 10200, "low": 9980, "close": 10100},
            ],
        )
        self.assertEqual(result["grade"], "B")

    def test_pending_without_bars(self):
        result = evaluate_youtube_alert_outcome(alert_price=10000, atr14=500, bars=[])
        self.assertEqual(result["status"], "PENDING")
        self.assertIsNone(result["grade"])

    def test_partial_window_stays_in_progress(self):
        result = evaluate_youtube_alert_outcome(
            alert_price=10000,
            atr14=500,
            bars=[
                {"minutes_after_alert": 5, "high": 10100, "low": 9950, "close": 10050},
                {"minutes_after_alert": 30, "high": 10400, "low": 9900, "close": 10300},
            ],
        )
        self.assertEqual(result["status"], "IN_PROGRESS_30M")
        self.assertIsNone(result["grade"])
        self.assertIsNotNone(result["horizons"]["10m"])
        self.assertIsNotNone(result["horizons"]["30m"])
        self.assertIsNone(result["horizons"]["60m"])

    def test_invalid_price_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_youtube_alert_outcome(alert_price=0, atr14=500, bars=[])

    def test_horizon_metrics_are_separate(self):
        result = evaluate_youtube_alert_outcome(
            alert_price=10000,
            atr14=500,
            bars=[
                {"minutes_after_alert": 10, "high": 10100, "low": 9950, "close": 10050},
                {"minutes_after_alert": 20, "high": 10200, "low": 9940, "close": 10150},
                {"minutes_after_alert": 45, "high": 10400, "low": 10000, "close": 10300},
            ],
        )
        self.assertEqual(result["horizons"]["10m"]["max_high"], 10100)
        self.assertEqual(result["horizons"]["30m"]["max_high"], 10200)
        self.assertIsNone(result["horizons"]["60m"])


if __name__ == "__main__":
    unittest.main()
