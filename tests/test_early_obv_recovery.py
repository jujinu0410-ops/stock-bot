import unittest
from datetime import date

from src.analysis.early_obv_recovery import evaluate_early_obv_recovery


class EarlyObvRecoveryTest(unittest.TestCase):
    def history(self, *, bullish=False):
        rows = []
        for day in ("2026-10-06", "2026-10-07", "2026-10-08"):
            for i in range(8):
                n = len(rows)
                rows.append({
                    "date": day,
                    "time": f"{9+i//2:02d}:{45 if i%2 else 0:02d}",
                    "close": float(230000 - 500*n),
                    "volume": 1000.0,
                })
        if bullish:
            for i in range(8):
                rows[-8+i]["close"] = float(220000 + 1500*i)
        return rows

    def live(self, *, volume=100.0, rise=10.0, start=218000.0):
        return [
            {"time": f"2026101209{i:02d}00", "close": start+rise*j, "volume": volume}
            for j, i in enumerate(range(10, 20))
        ]

    def test_0922_previous_day_obv_and_signal_line_falling_blocks_microscopic_bounce(self):
        history = self.history()
        out = evaluate_early_obv_recovery(
            history, self.live(),
            today=date(2026, 10, 12),
            fresh_price=218090.0, atr14=12000.0,
        )
        self.assertEqual(out["decision"], "VETO")
        self.assertEqual(out["reason"], "PERSISTENT_45M_OBV_DOWN_WITHOUT_MEANINGFUL_RECOVERY")
        self.assertTrue(out["prior_no_gold_4"])
        self.assertTrue(out["prior_obv_down_4"])
        self.assertTrue(out["prior_obv9_down_4"])
        self.assertLess(out["rebound_atr_10m"], 0.4)

    def test_0919_live_significant_volume_reversal_passes_without_closed_today_45m(self):
        history = self.history()
        out = evaluate_early_obv_recovery(
            history, self.live(volume=4000.0, rise=800.0, start=219000.0),
            today=date(2026, 10, 12),
            fresh_price=226200.0, atr14=12000.0,
        )
        self.assertEqual(out["decision"], "PASS")
        self.assertIn(out["reason"], {
            "LIVE_PARTIAL_OBV_GOLD_WITH_VOLUME",
            "MEANINGFUL_PRICE_AND_VOLUME_OBV_RECOVERY",
        })

    def test_price_only_jump_with_low_volume_is_not_enough(self):
        history = self.history()
        out = evaluate_early_obv_recovery(
            history, self.live(volume=10.0, rise=800.0, start=219000.0),
            today=date(2026, 10, 12),
            fresh_price=226200.0, atr14=12000.0,
        )
        self.assertEqual(out["decision"], "VETO")
        self.assertLess(out["volume_ratio_vs_prior_45m"], 1.1)

    def test_old_45m_obv_not_bearish_does_not_block_early_alerts(self):
        out = evaluate_early_obv_recovery(
            self.history(bullish=True), self.live(),
            today=date(2026, 10, 12),
            fresh_price=218090.0, atr14=12000.0,
        )
        self.assertEqual(out["decision"], "PASS")
        self.assertEqual(out["reason"], "PRIOR_45M_OBV_NOT_PERSISTENTLY_WEAK")

    def test_missing_history_not_mistaken_for_proven_bearish_weakness(self):
        out = evaluate_early_obv_recovery(
            [], self.live(), today=date(2026, 10, 12),
            fresh_price=218090.0, atr14=12000.0,
        )
        self.assertEqual(out["decision"], "UNAVAILABLE")

    def test_previous_45m_weak_and_insufficient_live_minutes_vetoes(self):
        out = evaluate_early_obv_recovery(
            self.history(), self.live()[:3], today=date(2026, 10, 12),
            fresh_price=218090.0, atr14=12000.0,
        )
        self.assertEqual(out["decision"], "VETO")
        self.assertIn("MINUTES_INSUFFICIENT", out["reason"])


if __name__ == "__main__":
    unittest.main()
