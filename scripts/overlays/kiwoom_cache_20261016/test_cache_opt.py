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

if __name__=="__main__":
    unittest.main(verbosity=2)
