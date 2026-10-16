"""Safety regression tests for sparse but individually complete KRX 45-minute candles."""
from datetime import datetime
from zoneinfo import ZoneInfo
import importlib.util
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

module_path = Path(__file__).with_name("bollinger_atr_strategy.py")
if not module_path.exists():
    module_path = Path(__file__).resolve().parents[1] / "src/analysis/bollinger_atr_strategy.py"
if not module_path.exists():
    module_path = Path("/app/src/analysis/bollinger_atr_strategy.py")
spec = importlib.util.spec_from_file_location("tested_bollinger", module_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
CUTOFF = datetime(2026, 10, 16, 13, 30, tzinfo=ZoneInfo("Asia/Seoul"))
SOURCE = "TEST_15M_BAR_START"

def raw_bars():
    stamps = []
    for day in pd.date_range("2026-10-12", "2026-10-16"):
        stamps.extend(pd.date_range(day + pd.Timedelta(hours=9), periods=24, freq="15min"))
    length = len(stamps)
    closes = np.linspace(9000, 9120, length)
    return pd.DataFrame({"Open":closes,"High":closes+5,"Low":closes-5,"Close":closes,"Volume":np.full(length,1000.)}, index=pd.DatetimeIndex(stamps))

class Sparse45mSafety(unittest.TestCase):
    def test_complete_identical(self):
        frame=raw_bars()
        a,q=mod.prepare_completed_45m_data(frame,SOURCE,CUTOFF)
        b,r=mod.prepare_completed_45m_data(frame,SOURCE,CUTOFF,allow_sparse_completed=True)
        self.assertEqual((q,r),("VALID","VALID"))
        pd.testing.assert_frame_equal(a,b)
        self.assertEqual(len(a),38) # four past full days plus six completed today
    def test_single_missing_interval(self):
        frame=raw_bars().drop(pd.Timestamp("2026-10-15 12:45"))
        _,strict=mod.prepare_completed_45m_data(frame,SOURCE,CUTOFF)
        cleaned,q=mod.prepare_completed_45m_data(frame,SOURCE,CUTOFF,allow_sparse_completed=True)
        self.assertEqual(strict,"MALFORMED_INTERVAL")
        self.assertEqual(q,"VALID")
        self.assertEqual(len(cleaned),37)
        self.assertNotIn(pd.Timestamp("2026-10-15 13:30"),cleaned.index)
        self.assertEqual(cleaned.index[-1],pd.Timestamp("2026-10-16 13:30"))
    def test_latest_candle_incomplete_fail_closed(self):
        frame=raw_bars().drop(pd.Timestamp("2026-10-16 13:15"))
        output,q=mod.prepare_completed_45m_data(frame,SOURCE,CUTOFF,allow_sparse_completed=True)
        self.assertIsNone(output)
        self.assertEqual(q,"CUTOFF_MISMATCH")
    def test_not_enough_complete_bars_fail_closed(self):
        frame=raw_bars()
        for day in ("2026-10-12", "2026-10-13"):
            for stamp in pd.date_range(day+" 09:00",periods=8,freq="45min"):
                frame=frame.drop(stamp) if stamp in frame.index else frame
        output,q=mod.prepare_completed_45m_data(frame,SOURCE,CUTOFF,allow_sparse_completed=True)
        self.assertIsNone(output)
        self.assertEqual(q,"INSUFFICIENT_COMPLETED_45M_BARS")
    def test_bad_ohlcv_not_accepted(self):
        frame=raw_bars()
        frame.loc[pd.Timestamp("2026-10-15 13:15"),"Volume"]=-1
        output,q=mod.prepare_completed_45m_data(frame,SOURCE,CUTOFF,allow_sparse_completed=True)
        self.assertIsNone(output)
        self.assertEqual(q,"MALFORMED_OHLCV")
    def test_wrapper_fetch_once_and_strict_unchanged(self):
        frame=raw_bars().drop(pd.Timestamp("2026-10-15 12:45"))
        called=[]
        class FakeAnalyzer:
            def fetch_canonical_15m_data(self,code):
                called.append(code)
                return frame,SOURCE,"NONE"
        original=mod.Intraday45mAnalyzer
        try:
            mod.Intraday45mAnalyzer=FakeAnalyzer
            strict,s1,q1=mod.get_strictly_sliced_45m_df("003230",CUTOFF)
            sparse,s2,q2=mod.get_strictly_sliced_45m_df("003230",CUTOFF,allow_sparse_completed=True)
            self.assertIsNone(strict)
            self.assertEqual(q1,"MALFORMED_INTERVAL")
            self.assertEqual(q2,"VALID")
            self.assertEqual((s1,s2),(SOURCE,SOURCE))
            self.assertEqual(called,["003230","003230"])
            self.assertEqual(len(sparse),37)
        finally:
            mod.Intraday45mAnalyzer=original

if __name__=="__main__":
    unittest.main(verbosity=2)
