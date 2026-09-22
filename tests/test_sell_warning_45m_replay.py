"""Tests for the research-only SellWarning 45m historical replay harness."""

import unittest

import numpy as np
import pandas as pd

from scripts.replay_sell_warning_45m import (
    attach_forward_outcomes,
    build_episodes,
    completed_cutoffs,
    replay_stock,
)


def market_frame(days=5):
    indexes = []
    for day in pd.bdate_range("2026-09-07", periods=days):
        indexes.extend(pd.date_range(day + pd.Timedelta(hours=9), periods=24, freq="15min"))
    index = pd.DatetimeIndex(indexes)
    sequence = np.arange(len(index), dtype=float)
    close = 10_000.0 + sequence * 2.0
    return pd.DataFrame({
        "Open": close,
        "High": close + 10.0,
        "Low": close - 10.0,
        "Close": close,
        "Volume": 1_000.0 + sequence,
    }, index=index)


class TestSellWarning45mReplay(unittest.TestCase):
    SOURCE = "TEST_15M_BAR_START"

    def test_01_future_data_does_not_change_point_in_time_signal(self):
        baseline_raw = market_frame()
        cutoff = pd.Timestamp("2026-09-10 11:15:00")
        changed_raw = baseline_raw.copy()
        future_mask = changed_raw.index >= cutoff
        changed_raw.loc[future_mask, "Close"] *= 2.0
        changed_raw.loc[future_mask, "Open"] = changed_raw.loc[future_mask, "Close"]
        changed_raw.loc[future_mask, "High"] = changed_raw.loc[future_mask, "Close"] + 10.0
        changed_raw.loc[future_mask, "Low"] = changed_raw.loc[future_mask, "Close"] - 10.0
        changed_raw.loc[future_mask, "Volume"] *= 100.0

        baseline = replay_stock("004960", "한신공영", baseline_raw, self.SOURCE)
        changed = replay_stock("004960", "한신공영", changed_raw, self.SOURCE)
        timestamp = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        left = baseline.loc[baseline["bar_timestamp"] == timestamp].iloc[0]
        right = changed.loc[changed["bar_timestamp"] == timestamp].iloc[0]
        for column in (
            "state", "bearish_axes_count", "close", "vwap9", "vwap26", "obv",
            "obv_wma9", "chaikin_value", "adx", "plus_di", "minus_di", "cloud_bottom",
        ):
            if isinstance(left[column], str):
                self.assertEqual(left[column], right[column])
            else:
                self.assertAlmostEqual(float(left[column]), float(right[column]), places=8)

    def test_02_partial_last_slot_is_not_a_completed_cutoff(self):
        frame = market_frame(days=1)
        frame = frame.drop(pd.Timestamp("2026-09-07 14:30:00"))
        cutoffs = completed_cutoffs(frame, self.SOURCE)
        self.assertIn(pd.Timestamp("2026-09-07 14:15:00"), cutoffs)
        self.assertNotIn(pd.Timestamp("2026-09-07 15:00:00"), cutoffs)

    def test_03_consecutive_states_collapse_to_one_episode(self):
        events = pd.DataFrame({
            "stock_code": ["004960"] * 5,
            "stock_name": ["한신공영"] * 5,
            "bar_timestamp": pd.date_range("2026-09-07 09:45", periods=5, freq="45min").astype(str),
            "state": ["NORMAL", "NORMAL", "CAUTION", "CAUTION", "SELL_WARNING"],
            "close": [100.0, 101.0, 99.0, 98.0, 95.0],
        })
        episodes = build_episodes(events)
        self.assertEqual(episodes["state"].tolist(), ["NORMAL", "CAUTION", "SELL_WARNING"])
        self.assertEqual(episodes["duration_bars"].tolist(), [2, 2, 1])
        self.assertEqual(episodes.iloc[1]["next_state"], "SELL_WARNING")

    def test_04_forward_return_calculation(self):
        day1 = pd.date_range("2026-09-07 09:00", periods=24, freq="15min")
        day2 = pd.date_range("2026-09-08 09:00", periods=24, freq="15min")
        day1_close = [100.0] * 24
        day1_close[5] = 102.0
        day1_close[8] = 104.0
        raw = pd.DataFrame({
            "Open": [100.0] * 24 + [105.0] * 24,
            "High": [value + 1.0 for value in day1_close] + [112.0] * 24,
            "Low": [99.0] * 24 + [95.0] * 24,
            "Close": day1_close + [110.0] * 24,
            "Volume": [1_000.0] * 48,
        }, index=day1.append(day2))
        events = pd.DataFrame({
            "bar_timestamp": [
                "2026-09-07 09:45:00", "2026-09-07 10:30:00", "2026-09-07 11:15:00"
            ],
            "close": [100.0, 102.0, 104.0],
        })
        measured = attach_forward_outcomes(events, raw)
        first = measured.iloc[0]
        self.assertAlmostEqual(first["return_next_45m_pct"], 2.0)
        self.assertAlmostEqual(first["return_next_90m_pct"], 4.0)
        self.assertAlmostEqual(first["return_day_close_pct"], 0.0)
        self.assertAlmostEqual(first["return_next_open_pct"], 5.0)
        self.assertAlmostEqual(first["return_next_close_pct"], 10.0)
        self.assertAlmostEqual(first["mae_1d_pct"], -5.0)
        self.assertAlmostEqual(first["mfe_1d_pct"], 12.0)

    def test_05_insufficient_history_exits_as_unknown(self):
        events = replay_stock("004960", "한신공영", market_frame(days=1), self.SOURCE)
        self.assertFalse(events.empty)
        self.assertEqual(set(events["state"]), {"UNKNOWN"})
        self.assertTrue(events["reason_codes"].str.contains("INSUFFICIENT_COMPLETED_45M_BARS").all())


if __name__ == "__main__":
    unittest.main()
