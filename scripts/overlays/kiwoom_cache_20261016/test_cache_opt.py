"""No-network regression checks of production Kiwoom token / quote / 45m caches."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
import threading
import time
from services import unified_scan as us
from services import youtube_signal_service as svc

class CacheOptimizationTests(unittest.TestCase):
    def setUp(self):
        us._CACHE.clear()

    def test_token_shared_context_quote_and_live_ticker(self):
        stats={"tokens":0,"quotes":[]}
        class Session:
            retry_count=0
            def __init__(self, max_attempts=3): pass
        def token(session):
            stats["tokens"]+=1
            return "mock-token"
        def quote(session, token, code):
            stats["quotes"].append(code)
            return {"available": True, "current_price": 10000, "change_pct": 0,
                    "source":"TEST_KIWOOM"}
        def daily(_):
            raise ValueError("TEST_NO_HISTORY")
        def decision(_):
            return {"stage":"WATCH","reasons":[],"send_allowed":False,
                    "confidence_adjustment":0,"events":[]}
        now=datetime(2026,10,16,10,15,tzinfo=ZoneInfo("Asia/Seoul"))
        with patch.object(svc,"RetryingKiwoomSession",Session), \
             patch.object(svc,"_get_kiwoom_token",side_effect=token), \
             patch.object(svc,"_fetch_kiwoom_quote",side_effect=quote), \
             patch.object(us,"daily_frame",side_effect=daily), \
             patch.object(us,"evaluate_signal",side_effect=decision), \
             patch.object(us.KRXCalendar,"is_krx_trading_day",return_value=True):
            codes=[f"{i:06}" for i in range(100001,100007)]
            for code in codes:
                res=us.scan({"ticker":code,"sources":["YOUTUBE"],"market":"KOSPI"},svc,now=now)
                self.assertEqual(res["live_price"],10000)
                self.assertEqual(res["stage"],"WATCH")
            self.assertEqual(stats["tokens"],1,"One token per warm worker, not 6")
            self.assertEqual(stats["quotes"].count("069500"),1,"Shared market quote fetched once")
            self.assertEqual(len(stats["quotes"]),7,"One live quote per ticker plus shared context")
            self.assertEqual(set(stats["quotes"]),set(codes+["069500"]))
            again=us.scan({"ticker":codes[0],"sources":["YOUTUBE"],"market":"KOSPI"},svc,now=now)
            self.assertEqual(stats["tokens"],1)
            self.assertEqual(stats["quotes"].count(codes[0]),2,
                             "The ticker itself is live on each scan, never 15s stale")
            self.assertEqual(stats["quotes"].count("069500"),1)

    def test_45m_valid_only_cache_with_cutoff_and_ttl(self):
        fetches=[]
        def valid():
            fetches.append("fetch")
            return (object(),"MOCK","VALID")
        oldclock=us.time.monotonic
        state={"t":10000.0}
        with patch.object(us.time,"monotonic",side_effect=lambda:state["t"]):
            def get(cutoff):
                return us.cached(("45m","003230",cutoff),900,valid,
                   cache_if=lambda r:r[0] is not None and r[2]=="VALID")
            self.assertEqual(get("2026-10-16T10:30")[2],"VALID")
            state["t"]+=301
            self.assertEqual(get("2026-10-16T10:30")[2],"VALID")
            self.assertEqual(len(fetches),1,"At 5min reuse same completed 45m")
            get("2026-10-16T11:15")
            self.assertEqual(len(fetches),2,"New cutoff must refresh")
            state["t"]+=901
            get("2026-10-16T11:15")
            self.assertEqual(len(fetches),3,"TTL expiry reloads")
        us._CACHE.clear()

    def test_invalid_45m_and_market_quote_not_cached(self):
        calls=[]
        def failing():
            calls.append("failure")
            return (None,"YFINANCE","MALFORMED_INTERVAL")
        for _ in range(2):
            result=us.cached(("45m","003230","bad"),900,failing,
                             cache_if=lambda r:r[0] is not None and r[2]=="VALID")
            self.assertEqual(result[2],"MALFORMED_INTERVAL")
        self.assertEqual(len(calls),2)

    def test_concurrent_token_singleflight_and_bounded_cache(self):
        n={"token":0}
        lock=threading.Lock()
        def loader():
            with lock:n["token"]+=1
            time.sleep(0.025)
            return "test-token"
        with ThreadPoolExecutor(max_workers=10) as pool:
            result=list(pool.map(lambda _:us.cached("token",1200,loader),range(10)))
        self.assertEqual(n["token"],1)
        self.assertEqual(result,["test-token"]*10)
        # Expired completed-candle cache should be pruned without flushing warm token.
        us._CACHE.update({("old",i):(time.monotonic()-99999,"X",5) for i in range(1100)})
        us.cached("active",1200,lambda:"active")
        self.assertLess(len(us._CACHE),20)
        self.assertEqual(us.cached("token",1200,lambda:"should-not-fetch"),"test-token")

    def test_p0_2_sparse_45m_passed_everywhere_and_stop_breach_independent(self):
        import pandas as pd
        import numpy as np
        # 1. Verify STOP_BREACH fires even when daily and 45m fail completely
        class Session:
            retry_count=0
            def __init__(self, max_attempts=3): pass
        now = datetime(2026,10,19,10,15,tzinfo=ZoneInfo("Asia/Seoul"))
        sparse_kwargs = []
        def fake_45m(ticker, cutoff, **kwargs):
            sparse_kwargs.append(kwargs.get("allow_sparse_completed"))
            return (None, "NONE", "NO_INTRADAY_DATA")
        with patch.object(svc,"RetryingKiwoomSession",Session), \
             patch.object(svc,"_get_kiwoom_token",return_value="tok"), \
             patch.object(svc,"_fetch_kiwoom_quote",return_value={"available":True,"current_price":58000,"change_pct":-0.04,"source":"KIWOOM"}), \
             patch.object(svc,"_fetch_kiwoom_minute_bars",return_value={"available":False,"bars":[]}), \
             patch.object(svc,"get_strictly_sliced_45m_df",side_effect=fake_45m), \
             patch.object(us,"daily_frame",side_effect=ValueError("NO_DAILY")), \
             patch.object(us.KRXCalendar,"is_krx_trading_day",return_value=True):
            res = us.scan({"ticker":"058470","sources":["HELD"],"stop_price":60000,"atr14":2000,"market":"KOSDAQ"},svc,now=now)
            self.assertEqual(res["stage"], "RISK")
            self.assertTrue(any(e["key"] == "STOP_BREACH" for e in res["events"]))
            self.assertEqual(sparse_kwargs, [True])

        # 2. Verify svc._fetch_45m_exit_risk, _evaluate_early_obv_recovery_gate, _held_obv15 pass allow_sparse_completed=True
        calls = []
        def record_45m(ticker, cutoff, **kwargs):
            calls.append((ticker, kwargs.get("allow_sparse_completed")))
            return (None, "MOCK", "INSUFFICIENT_COMPLETED_45M_BARS")
        with patch.object(svc, "get_strictly_sliced_45m_df", side_effect=record_45m), \
             patch.object(svc.KRXCalendar, "get_completed_45m_bar", return_value=("09:00", "09:45", "09:45")), \
             patch.object(svc.KRXCalendar, "is_krx_trading_day", return_value=True):
            svc._fetch_45m_exit_risk("010950")
            svc._evaluate_early_obv_recovery_gate("010950", minute_bars=[], fresh_price=10000.0, atr14=300.0)
            svc._held_obv15({"ticker": "010950", "atr14": 300.0})
        self.assertTrue(len(calls) >= 5)
        self.assertTrue(all(flag is True for _, flag in calls))

    def test_p1_1_daily_cache_600s_and_thread_local_session_reuse(self):
        import pandas as pd
        import numpy as np
        # Verify RetryingKiwoomSession reuses thread-local requests.Session on same thread
        s1 = svc.RetryingKiwoomSession(max_attempts=3)
        s2 = svc.RetryingKiwoomSession(max_attempts=3)
        self.assertIs(s1._session, s2._session)
        other_sessions = []
        t = threading.Thread(target=lambda: other_sessions.append(svc.RetryingKiwoomSession()._session))
        t.start()
        t.join()
        self.assertIsNot(s1._session, other_sessions[0])

        # Verify daily_frame is cached for 600s (reused at +300s 5-min cycle, while live price still updates today's Close)
        dates = pd.bdate_range("2026-08-01", "2026-10-19")
        df_base = pd.DataFrame({
            "Open": np.full(len(dates), 10000.0),
            "High": np.full(len(dates), 10100.0),
            "Low": np.full(len(dates), 9900.0),
            "Close": np.linspace(9500.0, 10000.0, len(dates)),
            "Volume": np.full(len(dates), 50000.0),
        }, index=dates)
        daily_calls = []
        def load_daily(ticker):
            daily_calls.append(ticker)
            return df_base.copy()
        clock = {"t": 50000.0}
        prices = [10200.0, 10500.0]
        class Session:
            retry_count=0
            def __init__(self, max_attempts=3): pass
        now = datetime(2026,10,19,10,15,tzinfo=ZoneInfo("Asia/Seoul"))
        with patch.object(us.time, "monotonic", side_effect=lambda: clock["t"]), \
             patch.object(svc, "RetryingKiwoomSession", Session), \
             patch.object(svc, "_get_kiwoom_token", return_value="tok"), \
             patch.object(svc, "_fetch_kiwoom_quote", side_effect=lambda s, tok, code: {"available":True,"current_price":prices.pop(0) if code=="010950" else 30000,"change_pct":0.01,"source":"KIWOOM"}), \
             patch.object(svc, "_fetch_kiwoom_minute_bars", return_value={"available":False,"bars":[]}), \
             patch.object(svc, "get_strictly_sliced_45m_df", return_value=(None,"MOCK","NONE")), \
             patch.object(us, "daily_frame", side_effect=load_daily), \
             patch.object(us.KRXCalendar, "is_krx_trading_day", side_effect=lambda d: d.weekday() < 5):
            r1 = us.scan({"ticker":"010950","sources":["HELD"],"market":"KOSPI"}, svc, now=now)
            clock["t"] += 305.0  # 5 minutes + 5s later (within 600s TTL, would have expired under 240s)
            r2 = us.scan({"ticker":"010950","sources":["HELD"],"market":"KOSPI"}, svc, now=now)
        self.assertEqual(len(daily_calls), 1, "Daily fchart reused across 5-minute cycle")
        self.assertEqual(r1["live_price"], 10200.0)
        self.assertEqual(r2["live_price"], 10500.0)
        self.assertGreater(r2["technical"]["ma20"], r1["technical"]["ma20"], "Today's Close updated with live price")

if __name__=="__main__":
    unittest.main(verbosity=2)
