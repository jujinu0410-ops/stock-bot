import unittest
from datetime import datetime

from services.unified_scan_m30 import (
    daily_vwap9_gate,
    apply_daily_vwap9_gate,
    m30_bullish_qualification,
)

NOW = datetime.fromisoformat("2026-11-10T13:35:00+09:00")
OFFICIAL = {
    "basis": "COMPLETED_DAILY",
    "as_of": "2026-11-09 15:30:00",
    "vwap9": 105.0,
    "prev_high": 120.0,
}


class DailyVwap9GateTests(unittest.TestCase):
    def test_strictly_above_only(self):
        self.assertTrue(daily_vwap9_gate(106, OFFICIAL, NOW))
        self.assertFalse(daily_vwap9_gate(105, OFFICIAL, NOW))
        self.assertFalse(daily_vwap9_gate(104, OFFICIAL, NOW))

    def test_missing_or_uncompleted_daily_data_fails_closed(self):
        self.assertFalse(daily_vwap9_gate(110, {}, NOW))
        self.assertFalse(daily_vwap9_gate(110, {**OFFICIAL, "basis": "INCOMPLETE_DAILY_NOT_CONFIRMED"}, NOW))
        self.assertFalse(daily_vwap9_gate(110, {**OFFICIAL, "as_of": "2026-11-10 15:30:00"}, NOW))
        self.assertFalse(daily_vwap9_gate(110, {**OFFICIAL, "as_of": "2026-11-11 15:30:00"}, NOW))
        self.assertFalse(daily_vwap9_gate(110, {**OFFICIAL, "as_of": "bad-date"}, NOW))
        self.assertFalse(daily_vwap9_gate(110, {**OFFICIAL, "vwap9": 0}, NOW))
        self.assertFalse(daily_vwap9_gate(None, OFFICIAL, NOW))

    def test_only_bullish_stages_are_blocked(self):
        self.assertEqual(apply_daily_vwap9_gate("BUY_SETUP", 104, OFFICIAL, NOW), ("WATCH", False))
        self.assertEqual(apply_daily_vwap9_gate("BUY_CONFIRMED", 104, OFFICIAL, NOW), ("WATCH", False))
        self.assertEqual(apply_daily_vwap9_gate("BUY_SETUP", 106, OFFICIAL, NOW), ("BUY_SETUP", True))
        self.assertEqual(apply_daily_vwap9_gate("DOWN_CONFIRMED", 104, OFFICIAL, NOW), ("DOWN_CONFIRMED", False))
        self.assertEqual(apply_daily_vwap9_gate("RISK", 104, OFFICIAL, NOW), ("RISK", False))
        self.assertEqual(apply_daily_vwap9_gate("WATCH", 104, OFFICIAL, NOW), ("WATCH", False))

    def test_m30_alignment_plus_daily_vwap9_is_sufficient_without_prev_high(self):
        m30 = {"available": True, "gold": True, "vwap9": 108.0, "vwap26": 100.0}
        stacked, qualified = m30_bullish_qualification(110, m30, OFFICIAL, NOW)
        self.assertTrue(stacked)
        self.assertTrue(qualified)  # live price 110 is below prev_high 120

    def test_m30_alignment_alone_is_not_sufficient(self):
        m30 = {"available": True, "gold": True, "vwap9": 108.0, "vwap26": 100.0}
        daily_below = {**OFFICIAL, "vwap9": 115.0}
        stacked, qualified = m30_bullish_qualification(110, m30, daily_below, NOW)
        self.assertTrue(stacked)
        self.assertFalse(qualified)

    def test_unverified_or_non_aligned_m30_never_qualifies(self):
        m30 = {"available": False, "gold": True, "vwap9": 108.0, "vwap26": 100.0}
        self.assertEqual(m30_bullish_qualification(110, m30, OFFICIAL, NOW), (False, False))
        m30["available"] = True
        m30["gold"] = False
        self.assertEqual(m30_bullish_qualification(110, m30, OFFICIAL, NOW), (False, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
