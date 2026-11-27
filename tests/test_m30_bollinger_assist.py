"""Offline checks for the E30 completed-bar Bollinger advisory."""

from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.analysis.bollinger_atr_30m import calculate_bb26_2, prepare_completed_30m_data
from src.analysis.m30_bollinger_assist import bollinger_assist, bollinger_assist_history
from src.runtime.krx_calendar import COMPLETED_30M_BAR_SCHEDULE, KRXCalendar


def prices(close, index):
    return pd.DataFrame({
        "Open": close, "High": close + 0.1, "Low": close - 0.1,
        "Close": close, "Volume": 100.0,
    }, index=index)


class BollingerAssistTests(TestCase):
    def setUp(self):
        self.index = pd.DatetimeIndex([
            pd.Timestamp(f"{day.date()} {slot[3]}")
            for day in pd.date_range("2026-11-02", "2026-11-26")
            if KRXCalendar.is_krx_trading_day(day.date())
            for slot in COMPLETED_30M_BAR_SCHEDULE
        ])
        self.frame = prices(100 + np.sin(np.arange(len(self.index)) / 9), self.index)

    def test_values_match_bb26_2_and_hts_scale(self):
        lower, middle, upper = calculate_bb26_2(self.frame)
        close = self.frame.Close.iloc[-1]
        report = bollinger_assist(self.frame, self.index[-1])
        self.assertAlmostEqual(report["percent_b"], (close - lower) / (upper - lower) * 100)
        self.assertAlmostEqual(report["bandwidth_pct"], (upper - lower) / middle * 100)
        self.assertAlmostEqual(middle, self.frame.Close.tail(26).mean())
        self.assertAlmostEqual(upper - lower, 4 * self.frame.Close.tail(26).std(ddof=0))

    def test_stale_flat_and_short_data_are_rejected(self):
        self.assertIsNone(bollinger_assist(self.frame, self.index[-2]))
        self.assertIsNone(bollinger_assist(self.frame.iloc[:115], self.index[114]))
        flat = prices(np.full(len(self.index), 100.0), self.index)
        self.assertIsNone(bollinger_assist(flat, self.index[-1]))

    def test_history_prefix_is_invariant(self):
        end = self.index[145]
        full = bollinger_assist_history(self.frame).loc[end]
        prefix = bollinger_assist_history(self.frame.loc[:end]).loc[end]
        pd.testing.assert_series_equal(full, prefix)

    def test_preparation_and_first_watch_require_sequence_vol_and_complete_bars(self):
        frame = prices(np.full(len(self.index), 100.0), self.index)
        width = pd.Series(8.0, index=self.index)
        width.iloc[130:133] = 6
        width.iloc[133:135] = [10, 12]
        frame.loc[self.index[129:135], "Close"] = [99.7, 99.4, 98.4, 99.7, 102.0, 103.0]
        frame.loc[self.index[132:135], "Volume"] = [250, 140, 150]
        zeros = width * 0
        with patch("src.analysis.m30_bollinger_assist.bb26_2_bands",
                   return_value=(100 - width / 2, 100 + zeros, 100 + width / 2)), patch(
                       "src.analysis.m30_bollinger_assist.macd_series", return_value=(width, zeros)):
            reports = bollinger_assist_history(frame)
            self.assertEqual(reports.stage.iloc[131], "COMPRESSION")
            self.assertEqual(reports.stage.iloc[132], "BUY_PREPARATION")
            self.assertEqual(reports.stage.iloc[133], "TRAILING_WATCH")
            self.assertEqual(reports.stage.iloc[134], "NONE")
            quiet = frame.copy()
            quiet.loc[self.index[133:135], "Volume"] = 1
            self.assertEqual(bollinger_assist_history(quiet).stage.iloc[133], "NONE")
            skipped = frame.drop(self.index[132])
            self.assertEqual(bollinger_assist_history(skipped).stage.at[self.index[133]], "NONE")

    def test_completed_frame_discards_open_candle_and_final_auction(self):
        end = self.index[-2]
        raw_index = self.index.repeat(2) - pd.to_timedelta(np.tile([30, 15], len(self.index)), unit="min")
        raw = prices(100 + np.sin(np.arange(len(raw_index)) / 9), raw_index)
        raw.attrs["source_watermark"] = raw_index[-1].isoformat()
        frame, quality = prepare_completed_30m_data(raw, "KIWOOM_15M_BAR_START", end)
        self.assertEqual(quality, "VALID")
        self.assertEqual(frame.index[-1], end)
        self.assertNotIn(self.index[-1], frame.index)
        self.assertNotIn("15:30", frame.index.strftime("%H:%M"))
