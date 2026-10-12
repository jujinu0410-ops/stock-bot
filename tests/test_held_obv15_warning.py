import unittest
from datetime import date

import pandas as pd

from src.analysis.held_obv15_warning import analyze_obv15_sell_warning


class HeldObv15WarningTest(unittest.TestCase):
    def history(self, *, last_volume=1000.0):
        rows = []
        idx = []
        for day in ("2026-10-06", "2026-10-07", "2026-10-08"):
            for i in range(8):
                n = len(rows)
                idx.append(pd.Timestamp(day) + pd.Timedelta(hours=9, minutes=45*(i+1)))
                rows.append({"Close": float(150000-1000*n), "Volume": last_volume})
        return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))

    def minute(self, *, amount=20.0, start=125000.0, step=-3.0):
        return [
            {"time": f"2026101209{i:02d}00", "close": start+step*j, "volume": amount}
            for j, i in enumerate(range(10, 20))
        ]

    def test_0919_15_dead_bars_warn_even_without_macd_or_bear_regime(self):
        result=analyze_obv15_sell_warning(
            self.history(), self.minute(), today=date(2026,10,12),
            fresh_price=124970.0, atr14=7000.0
        )
        self.assertEqual(result["decision"], "WARN")
        self.assertGreaterEqual(result["consecutive_no_gold_bars"],15)
        self.assertTrue(result["recent_obv_down"])
        self.assertTrue(result["recent_obv9_down"])

    def test_recovery_with_price_and_volume_suppresses_warning(self):
        result=analyze_obv15_sell_warning(
            self.history(), self.minute(amount=8000.0,start=126000.0,step=400.0),
            today=date(2026,10,12), fresh_price=129600.0, atr14=7000.0
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["reason"],"LIVE_RECOVERY_CONFIRMED")

    def test_insufficient_live_minutes_waits_to_avoid_false_warning(self):
        result=analyze_obv15_sell_warning(
            self.history(), self.minute()[:3], today=date(2026,10,12),
            fresh_price=124970.0, atr14=7000.0
        )
        self.assertEqual(result["decision"],"UNAVAILABLE")
        self.assertEqual(result["reason"],"LIVE_RECOVERY_DATA_INSUFFICIENT")

    def test_obv_gold_resets_streak_even_when_15_bars_ago_down(self):
        df=self.history()
        df.iloc[-1, df.columns.get_loc("Close")]=175000.0
        result=analyze_obv15_sell_warning(
            df,self.minute(),today=date(2026,10,12),
            fresh_price=175000.0,atr14=7000.0
        )
        self.assertEqual(result["decision"],"PASS")

    def test_less_than_24_history_is_unavailable(self):
        result=analyze_obv15_sell_warning(
            self.history().iloc[-20:],self.minute(),today=date(2026,10,12),
            fresh_price=124970.0,atr14=7000.0
        )
        self.assertEqual(result["decision"],"UNAVAILABLE")

    def test_valid_history_but_not_falling_three_is_not_warn(self):
        df=self.history()
        df.iloc[-1,df.columns.get_loc("Close")]=float(df["Close"].iloc[-2])
        result=analyze_obv15_sell_warning(
            df,self.minute(),today=date(2026,10,12),
            fresh_price=124970.0,atr14=7000.0
        )
        self.assertEqual(result["decision"],"PASS")


if __name__=="__main__":
    unittest.main()
