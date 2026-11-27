import unittest
import numpy as np
import pandas as pd
from datetime import datetime

from services.unified_scan_m30 import (
    daily_obv_wma9_gate,
    apply_daily_obv_wma9_gate,
    indicators,
    m30_bullish_qualification,
)

NOW = datetime.fromisoformat("2026-11-10T13:35:00+09:00")
GOLD = {
    "basis": "COMPLETED_DAILY",
    "as_of": "2026-11-09 15:30:00",
    "vwap9": 105.0,
    "obv": 100.0,
    "obv_wma9": 90.0,
    "obv9": 110.0,
}
DEAD = {**GOLD, "obv": 80.0}


class DailyObvWma9GateTests(unittest.TestCase):
    def test_gold_is_obv_above_wma9_not_legacy_sma9(self):
        self.assertTrue(daily_obv_wma9_gate(GOLD, NOW))
        self.assertFalse(daily_obv_wma9_gate(DEAD, NOW))

    def test_strict_comparison_and_negative_obv_values_are_supported(self):
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "obv": 90.0}, NOW))
        self.assertTrue(daily_obv_wma9_gate({**GOLD, "obv": -80.0, "obv_wma9": -100.0}, NOW))

    def test_missing_or_uncompleted_daily_data_fails_closed(self):
        self.assertFalse(daily_obv_wma9_gate({}, NOW))
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "basis": "INCOMPLETE_DAILY_NOT_CONFIRMED"}, NOW))
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "as_of": "2026-11-10 15:30:00"}, NOW))
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "as_of": "2026-11-11 15:30:00"}, NOW))
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "as_of": "bad-date"}, NOW))
        self.assertFalse(daily_obv_wma9_gate({k:v for k,v in GOLD.items() if k != "obv_wma9"}, NOW))
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "obv": float("nan")}, NOW))
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "obv_wma9": float("inf")}, NOW))

    def test_m30_qualification_requires_both_daily_gates(self):
        m30 = {"available": True, "gold": True, "vwap9": 108.0, "vwap26": 100.0}
        self.assertEqual(m30_bullish_qualification(110, m30, GOLD, NOW), (True, True, True))
        self.assertEqual(m30_bullish_qualification(110, m30, DEAD, NOW), (True, True, False))

    def test_only_bullish_stages_are_blocked(self):
        self.assertEqual(apply_daily_obv_wma9_gate("BUY_SETUP", DEAD, NOW), ("WATCH", False))
        self.assertEqual(apply_daily_obv_wma9_gate("BUY_CONFIRMED", DEAD, NOW), ("WATCH", False))
        self.assertEqual(apply_daily_obv_wma9_gate("BUY_SETUP", GOLD, NOW), ("BUY_SETUP", True))
        self.assertEqual(apply_daily_obv_wma9_gate("DOWN_CONFIRMED", DEAD, NOW), ("DOWN_CONFIRMED", False))
        self.assertEqual(apply_daily_obv_wma9_gate("RISK", DEAD, NOW), ("RISK", False))
        self.assertEqual(apply_daily_obv_wma9_gate("WATCH", DEAD, NOW), ("WATCH", False))

    def test_indicator_matches_linear_weighted_moving_average_1_to_9(self):
        changes = [0.8, -0.3, 1.1, -0.6, 0.2, 0.9, -1.2, 0.4, -0.1, 0.7]
        close = [100.0]
        for i in range(39):
            close.append(close[-1] + changes[i % len(changes)])
        volume = [1000.0 + ((i * 137) % 700) for i in range(len(close))]
        frame = pd.DataFrame(
            {"Open": close, "High": [x + 1 for x in close],
             "Low": [x - 1 for x in close], "Close": close, "Volume": volume},
            index=pd.bdate_range("2026-01-01", periods=len(close)),
        )
        metrics = indicators(frame)
        delta = frame["Close"].diff()
        obv = frame["Volume"].where(delta > 0, (-frame["Volume"]).where(delta < 0, 0)).cumsum()
        weights = np.arange(1, 10, dtype=float)
        expected = obv.rolling(9).apply(
            lambda window: float(np.dot(window, weights) / weights.sum()), raw=True
        ).iloc[-1]
        self.assertAlmostEqual(metrics["obv_wma9"], float(expected), places=6)
        self.assertIn(metrics["obv_wma9_rel"], {"GOLD", "DEAD"})
        self.assertNotAlmostEqual(metrics["obv_wma9"], metrics["obv9"], places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
