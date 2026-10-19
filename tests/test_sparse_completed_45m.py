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

    def test_generate_bb_atr_advisory_passes_allow_sparse_completed(self):
        frame = raw_bars().drop(pd.Timestamp("2026-10-15 12:45"))
        class FakeAnalyzer:
            def fetch_canonical_15m_data(self, code):
                return frame, SOURCE, "NONE"
        original = mod.Intraday45mAnalyzer
        try:
            mod.Intraday45mAnalyzer = FakeAnalyzer
            item = {
                "stock_code": "003230",
                "stock_name": "삼양식품",
                "avg_buy_price": 9000.0,
                "current_price": 9100.0,
                "current_completed_atr": 200.0,
                "effective_exit_line": 8800.0,
                "is_etf": False,
                "trade_mode": "NORMAL",
                "data_validity_flag": 1,
                "data_hold_reason": "정상",
                "is_stop_breached": False,
                "action_status": "계속 보유",
            }
            asof = datetime(2026, 10, 16, 13, 35, tzinfo=ZoneInfo("Asia/Seoul"))
            res = mod.generate_bb_atr_advisory(item, asof, "1335")
            self.assertEqual(res["data_quality"], "VALID")
            self.assertEqual(res["completed_45m_timestamp"], "2026-10-16 13:30:00")
        finally:
            mod.Intraday45mAnalyzer = original

    def test_intraday_analyzer_yfinance_10d_and_naver_volume_diff(self):
        from unittest.mock import MagicMock, patch
        from src.analysis.intraday_analysis import Intraday45mAnalyzer, _compute_naver_bar_volumes

        analyzer = Intraday45mAnalyzer()
        history_calls = []
        class FakeTicker:
            def __init__(self, sym):
                self.sym = sym
            def history(self, interval="15m", period="5d"):
                history_calls.append((self.sym, interval, period))
                return pd.DataFrame()

        with patch("src.analysis.intraday_analysis.yf.Ticker", FakeTicker):
            analyzer._fetch_from_yfinance("005930")
        self.assertTrue(all(call[2] == "10d" for call in history_calls))

        # Naver cumulative volume diff: day 1 normal + inversion + gap, day 2 reset + mid-day start
        naver_rows = []
        # Day 1 starting at 09:00:00 (1-min step)
        base1 = pd.Timestamp("2026-10-15 09:00:00")
        cums1 = [
            (0, 1000),   # 09:00 -> 1000
            (1, 1500),   # 09:01 -> 500
            (2, 1300),   # 09:02 -> negative inversion (-200) -> 0
            (3, 1800),   # 09:03 -> 500
            (6, 9000),   # 09:06 -> gap of 3m (> 1m step) -> 0
        ]
        for idx in range(7, 20):
            cums1.append((idx, 9000 + (idx - 6) * 100))
        for minute_offset, cum_val in cums1:
            ts = (base1 + pd.Timedelta(minutes=minute_offset)).strftime("%Y%m%d%H%M%S")
            naver_rows.append({
                "localDateTime": ts,
                "openPrice": "10000",
                "highPrice": "10100",
                "lowPrice": "9900",
                "currentPrice": "10050",
                "accumulatedTradingVolume": str(cum_val),
            })
        # Day 2 starting at 09:00:00 (reset)
        base2 = pd.Timestamp("2026-10-16 09:00:00")
        naver_rows.append({
            "localDateTime": base2.strftime("%Y%m%d%H%M%S"),
            "openPrice": "10000", "highPrice": "10100", "lowPrice": "9900",
            "currentPrice": "10050", "accumulatedTradingVolume": "700",
        })
        naver_rows.append({
            "localDateTime": (base2 + pd.Timedelta(minutes=1)).strftime("%Y%m%d%H%M%S"),
            "openPrice": "10000", "highPrice": "10100", "lowPrice": "9900",
            "currentPrice": "10050", "accumulatedTradingVolume": "1100",
        })
        # Day 3 starting mid-day at 10:30:00 (missing earlier morning bars -> first bar must be 0.0)
        base3 = pd.Timestamp("2026-10-19 10:30:00")
        naver_rows.append({
            "localDateTime": base3.strftime("%Y%m%d%H%M%S"),
            "openPrice": "10000", "highPrice": "10100", "lowPrice": "9900",
            "currentPrice": "10050", "accumulatedTradingVolume": "85000",
        })
        naver_rows.append({
            "localDateTime": (base3 + pd.Timedelta(minutes=1)).strftime("%Y%m%d%H%M%S"),
            "openPrice": "10000", "highPrice": "10100", "lowPrice": "9900",
            "currentPrice": "10050", "accumulatedTradingVolume": "85400",
        })

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = list(reversed(naver_rows))  # reverse order from API
        with patch("src.analysis.intraday_analysis.requests.get", return_value=fake_resp):
            df_naver, src = analyzer._fetch_from_naver_api("005930")

        self.assertEqual(src, "NAVER_MINUTE_API")
        self.assertEqual(df_naver.loc[pd.Timestamp("2026-10-15 09:00:00"), "Volume"], 1000.0)
        self.assertEqual(df_naver.loc[pd.Timestamp("2026-10-15 09:01:00"), "Volume"], 500.0)
        self.assertEqual(df_naver.loc[pd.Timestamp("2026-10-15 09:02:00"), "Volume"], 0.0)
        self.assertEqual(df_naver.loc[pd.Timestamp("2026-10-15 09:03:00"), "Volume"], 500.0)
        self.assertEqual(df_naver.loc[pd.Timestamp("2026-10-15 09:06:00"), "Volume"], 0.0)
        self.assertEqual(df_naver.loc[pd.Timestamp("2026-10-15 09:07:00"), "Volume"], 100.0)
        self.assertEqual(df_naver.loc[pd.Timestamp("2026-10-16 09:00:00"), "Volume"], 700.0)
        self.assertEqual(df_naver.loc[pd.Timestamp("2026-10-16 09:01:00"), "Volume"], 400.0)
        self.assertEqual(df_naver.loc[pd.Timestamp("2026-10-19 10:30:00"), "Volume"], 0.0)
        self.assertEqual(df_naver.loc[pd.Timestamp("2026-10-19 10:31:00"), "Volume"], 400.0)

if __name__=="__main__":
    unittest.main(verbosity=2)
