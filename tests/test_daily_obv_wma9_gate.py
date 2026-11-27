import unittest
import numpy as np
import pandas as pd
from datetime import datetime

from services.unified_scan_m30 import (
    daily_obv_wma9_gate,
    daily_obv_wma9_status,
    daily_states,
    indicators,
    m30_bullish_qualification,
)
from src.runtime.krx_calendar import KRXCalendar

NOW = datetime.fromisoformat("2026-11-10T13:35:00+09:00")
GOLD = {
    "basis": "COMPLETED_DAILY",
    "as_of": "2026-11-09 15:30:00",
    "vwap9": 105.0,
    "obv": 100.0,
    "obv_wma9": 90.0,
    "obv9": 110.0,
    "obv_wma9_window_valid": True,
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
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "as_of": "2026-11-06 15:30:00"}, NOW))
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "obv_wma9_window_valid": False}, NOW))
        self.assertFalse(daily_obv_wma9_gate({k:v for k,v in GOLD.items() if k != "obv_wma9"}, NOW))
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "obv": float("nan")}, NOW))
        self.assertFalse(daily_obv_wma9_gate({**GOLD, "obv_wma9": float("inf")}, NOW))

    def test_m30_qualification_requires_both_daily_gates(self):
        m30 = {"available": True, "gold": True, "vwap9": 108.0, "vwap26": 100.0}
        self.assertEqual(m30_bullish_qualification(110, m30, GOLD, NOW), (True, True, True))
        self.assertEqual(m30_bullish_qualification(110, m30, DEAD, NOW), (True, True, False))

    def test_wire_requires_real_finite_json_numbers(self):
        for value in (None, False, True, "", " ", "90", "NaN", float("nan"),
                      float("inf"), float("-inf"), [], {}):
            with self.subTest(value=value):
                status = daily_obv_wma9_status({**GOLD, "obv_wma9": value}, NOW)
                self.assertFalse(status["valid"])
                self.assertFalse(status["qualified"])
                self.assertIsNone(status["wma9"])

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
        metrics = indicators(frame, with_obv_wma9=True)
        delta = frame["Close"].diff()
        obv = frame["Volume"].where(delta > 0, (-frame["Volume"]).where(delta < 0, 0)).cumsum()
        weights = np.arange(1, 10, dtype=float)
        expected = obv.rolling(9).apply(
            lambda window: float(np.dot(window, weights) / weights.sum()), raw=True
        ).iloc[-1]
        self.assertAlmostEqual(metrics["obv_wma9"], float(expected), places=6)
        self.assertIn(metrics["obv_wma9_rel"], {"GOLD", "DEAD"})
        self.assertNotAlmostEqual(metrics["obv_wma9"], metrics["obv9"], places=6)
        legacy = indicators(frame)
        self.assertEqual(legacy, {k: v for k, v in metrics.items() if not k.startswith("obv_wma9")})

    def test_calendar_window_including_holiday_and_provisional_today(self):
        now = datetime.fromisoformat("2026-10-12T13:35:00+09:00")
        dates = [d for d in pd.date_range("2026-07-01", now.date()) if KRXCalendar.is_krx_trading_day(d)]
        values = np.arange(100, 100 + len(dates), dtype=float)
        frame = pd.DataFrame({"Open": values, "High": values + 1, "Low": values - 1,
                              "Close": values, "Volume": 100.0}, index=dates)
        official, provisional = daily_states(frame, now, live_price=50.)
        status = daily_obv_wma9_status(official, now)
        self.assertTrue(status["qualified"])
        self.assertEqual(status["expected_as_of"], "2026-10-08 15:30:00")
        self.assertAlmostEqual(official["obv"] - official["obv_wma9"], 800 / 3)
        self.assertFalse(daily_obv_wma9_gate(provisional, now))
        self.assertNotIn("obv_wma9", provisional)
        without_intermediate = frame.drop(pd.Timestamp("2026-10-07"))
        official, _ = daily_states(without_intermediate, now)
        self.assertFalse(official["obv_wma9_window_valid"])
        self.assertFalse(daily_obv_wma9_gate(official, now))
        for invalid_frame in (frame.iloc[-9:], frame.drop(pd.Timestamp("2026-10-08"))):
            with self.assertRaisesRegex(ValueError, "DAILY_COMPLETED_HISTORY_STALE_OR_INCOMPLETE"):
                daily_states(invalid_frame, now)


if __name__ == "__main__":
    unittest.main(verbosity=2)
