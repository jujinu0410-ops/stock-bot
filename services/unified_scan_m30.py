"""The three watchlists use this one quote/technical decision pipeline.



Read-only HTTP calculation. The sole Apps Script coordinator owns persistent

state, dispatch and event IDs. Source tags never select technical rules.

"""

from __future__ import annotations



from datetime import datetime, timedelta

from dataclasses import replace

import math

import threading

import time

import weakref

import xml.etree.ElementTree as ET

from zoneinfo import ZoneInfo



import pandas as pd

import requests



from services.unified_signal_engine import evaluate_signal, normalize_sources, BUY_STAGES

from src.analysis.held_obv30_warning import analyze_obv15_sell_warning

from src.analysis.jev_alert_gate import JevGateSettings, evaluate_jev_alert_gate

from src.runtime.krx_calendar import KRXCalendar



VERSION = "three-lists-v3-m30"

KST = ZoneInfo("Asia/Seoul")

_CACHE = {}

_CACHE_LOCK = threading.RLock()

# Locks are retained by active calls, then reclaimed automatically.

_KEY_LOCKS = weakref.WeakValueDictionary()





def cached(key, ttl, loader, cache_if=None):

    # Per-resource lock prevents a burst of duplicate token/history calls.

    with _CACHE_LOCK:

        lock = _KEY_LOCKS.setdefault(key, threading.Lock())

    with lock:

        entry = _CACHE.get(key)

        if entry and time.monotonic() - entry[0] < ttl:

            return entry[1]

        value = loader()

        # Do not retain transient 30m/Yahoo/quote failures as valid evidence.

        if cache_if is None or cache_if(value):

            now_mono = time.monotonic()

            if len(_CACHE) >= 1000:

                # Prune expired entries first; never flush all warm token/market

                # caches simultaneously after 1,500 historical 30m keys.

                for stale_key, entry in list(_CACHE.items()):

                    if now_mono - entry[0] >= (entry[2] if len(entry) > 2 else 0):

                        _CACHE.pop(stale_key, None)

                if len(_CACHE) >= 1200:

                    oldest = sorted(_CACHE.items(), key=lambda pair: pair[1][0])

                    for stale_key, _ in oldest[:len(_CACHE) - 800]:

                        _CACHE.pop(stale_key, None)

            _CACHE[key] = (now_mono, value, ttl)

        return value





def number(value):

    try:

        n = float(value)

        return n if math.isfinite(n) and n > 0 else None

    except (ValueError, TypeError):

        return None





def daily_vwap9_gate(price, daily, now):
    """Require live price above the latest completed daily 9-session VWAP.

    The official daily indicator is based only on completed sessions. The
    incomplete current-day daily bar and provisional indicators are never used.
    """
    vwap9 = number((daily or {}).get("vwap9"))
    if not number(price) or not vwap9 or (daily or {}).get("basis") != "COMPLETED_DAILY":
        return False
    as_of = str((daily or {}).get("as_of") or "")
    try:
        stamp = datetime.strptime(as_of, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return stamp.strftime("%H:%M:%S") == "15:30:00" and stamp.date() < now.date() and float(price) > vwap9


def apply_daily_vwap9_gate(stage, price, daily, now):
    """Fail closed for all bullish stages; preserve WATCH and downside/risk stages."""
    passed = daily_vwap9_gate(price, daily, now)
    if stage in BUY_STAGES and not passed:
        return "WATCH", False
    return stage, passed


def m30_bullish_qualification(price, m30, daily, now):
    p, v9, v26 = number(price), number((m30 or {}).get("vwap9")), number((m30 or {}).get("vwap26"))
    stacked = bool((m30 or {}).get("available") is True and (m30 or {}).get("gold") is True
                   and p and v9 and v26 and p > v9 > v26)
    return stacked, bool(stacked and daily_vwap9_gate(p, daily, now))


def previous_day(now):

    day = now.date() - timedelta(days=1)

    for _ in range(20):

        if KRXCalendar.is_krx_trading_day(day):

            return day

        day -= timedelta(days=1)

    raise ValueError("PREVIOUS_TRADING_DAY_UNAVAILABLE")





_HTTP_LOCAL = threading.local()





def _daily_http_get(url, *, params, headers, timeout):

    if getattr(requests.get, "__module__", "") != "requests.api":

        return requests.get(url, params=params, headers=headers, timeout=timeout)

    session = getattr(_HTTP_LOCAL, "session", None)

    if session is None:

        session = requests.Session()

        _HTTP_LOCAL.session = session

    return session.get(url, params=params, headers=headers, timeout=timeout)





def daily_frame(ticker):

    r = _daily_http_get("https://fchart.stock.naver.com/sise.nhn",

        params={"symbol": ticker, "timeframe": "day", "count": 90, "requestType": 0},

        headers={"User-Agent": "Mozilla/5.0"}, timeout=8)

    r.raise_for_status()

    text = r.content.decode("cp949", errors="replace")

    if text.lstrip().startswith("<?xml"):

        text = text.split("?>", 1)[1]

    rows = []

    for el in ET.fromstring(text).iter("item"):

        p = str(el.attrib.get("data") or "").split("|")

        if len(p) >= 6:

            rows.append([pd.Timestamp(p[0]), *map(float, p[1:6])])

    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")

    if len(df) < 30 or df.index.has_duplicates or not df.index.is_monotonic_increasing:

        raise ValueError("DAILY_HISTORY_INCOMPLETE")

    if not all(math.isfinite(float(x)) for x in df.to_numpy().flat):

        raise ValueError("DAILY_HISTORY_NONFINITE")

    if (df[["Open", "High", "Low", "Close"]] <= 0).any().any() or (df.Volume < 0).any():

        raise ValueError("DAILY_HISTORY_INVALID")

    return df





def indicators(df):

    """Same daily VWAP/OBV slope thresholds as the existing REGIME sheets."""

    close, vol = df.Close, df.Volume

    tp = (df.High + df.Low + close) / 3

    v9 = (tp * vol).rolling(9).sum() / vol.rolling(9).sum()

    v26 = (tp * vol).rolling(26).sum() / vol.rolling(26).sum()

    delta = close.diff()

    obv = vol.where(delta > 0, (-vol).where(delta < 0, 0)).cumsum()

    o9 = obv.rolling(9).mean()

    def direction(series, scale, threshold):

        slope = (float(series.iloc[-1]) - float(series.iloc[-3])) / 2 / max(scale, 1e-9)

        return "RISING" if slope > threshold else "FALLING" if slope < -threshold else "FLAT"

    slope9 = (float(v9.iloc[-1]) - float(v9.iloc[-3])) / 2 / max(float(v9.tail(3).mean()), 1e-9)

    slope26 = (float(v26.iloc[-1]) - float(v26.iloc[-3])) / 2 / max(float(v26.tail(3).mean()), 1e-9)

    result = {"vwap9": float(v9.iloc[-1]), "vwap26": float(v26.iloc[-1]),

              "vwap9_slope": float(slope9), "vwap26_slope": float(slope26),

              "obv": float(obv.iloc[-1]), "obv9": float(o9.iloc[-1])}

    if not all(math.isfinite(x) for x in result.values()):

        raise ValueError("INDICATORS_UNAVAILABLE")

    result.update(vwap9_dir=direction(v9, float(v9.tail(3).mean()), .0003),

                  vwap26_dir=direction(v26, float(v26.tail(3).mean()), .0003),

                  obv_dir=direction(obv, float(vol.tail(3).mean()), .05),

                  obv9_dir=direction(o9, float(vol.tail(3).mean()), .05),

                  vwap_rel="GOLD" if v9.iloc[-1] > v26.iloc[-1] else "DEAD",

                  obv_rel="GOLD" if obv.iloc[-1] > o9.iloc[-1] else "DEAD")

    a, b, c, d = [result[k] for k in ("vwap9_dir", "vwap26_dir", "obv_dir", "obv9_dir")]

    if result["vwap_rel"] == result["obv_rel"] == "GOLD" and a == b == c == d == "RISING":

        regime = "BULL_CONFIRMED"

    elif result["vwap_rel"] == result["obv_rel"] == "DEAD" and a == b == c == d == "FALLING":

        regime = "BEAR_CONFIRMED"

    elif a == "RISING" and b == "FALLING": regime = "BOUNCE_IN_DOWNREGIME"

    elif c == "RISING" and d == "FALLING": regime = "OBV_REBOUND_BASE_DOWN"

    elif b == "RISING" and a == "FALLING" and result["vwap_rel"] == "GOLD": regime = "PULLBACK_IN_UPREGIME"

    elif a == "RISING" or c == "RISING": regime = "EARLY_IMPROVEMENT"

    elif a == "FALLING" or c == "FALLING": regime = "EARLY_WEAKENING"

    else: regime = "NEUTRAL"

    return {**result, "regime": regime, "stage": {

        "BULL_CONFIRMED": "BUY_CONFIRMED", "BEAR_CONFIRMED": "DOWN_CONFIRMED",

        "EARLY_IMPROVEMENT": "BUY_SETUP", "EARLY_WEAKENING": "DOWN_EARLY"}.get(regime, "WATCH")}





def daily_states(df, now, live_price=None):
    if df.index[-1].date() > now.date(): raise ValueError("FUTURE_DAILY_BAR")
    completed=df.loc[df.index.date < now.date()].copy()
    if len(completed)<30 or completed.index[-1].date()!=previous_day(now):
        raise ValueError("DAILY_COMPLETED_HISTORY_STALE_OR_INCOMPLETE")
    official={**indicators(completed),"ma20":float(completed.Close.tail(20).mean()),
              "basis":"COMPLETED_DAILY","as_of":completed.index[-1].strftime("%Y-%m-%d 15:30:00")}
    provisional={"available":False,"basis":"INCOMPLETE_DAILY_NOT_CONFIRMED"}
    if df.index[-1].date()==now.date():
        partial=df.copy()
        if live_price:
            partial.loc[partial.index[-1],"Close"]=live_price
            partial.loc[partial.index[-1],"High"]=max(float(partial.High.iloc[-1]),live_price)
            partial.loc[partial.index[-1],"Low"]=min(float(partial.Low.iloc[-1]),live_price)
        provisional={**indicators(partial),"available":True,"basis":"INCOMPLETE_DAILY_NOT_CONFIRMED",
                     "as_of":now.isoformat(timespec="seconds"),"volume_scaled":False}
    return official,provisional


def atr_and_anchor(df, now):

    completed = df.loc[df.index.date < now.date()]

    if len(completed) < 20 or completed.index[-1].date() != previous_day(now):

        raise ValueError("DAILY_ATR_STALE")

    prev = completed.Close.shift(1)

    tr = pd.concat([completed.High-completed.Low, (completed.High-prev).abs(),

                    (completed.Low-prev).abs()], axis=1).max(axis=1)

    # Wilder ATR, seeded with the first 14 true ranges; identical for all lists.

    atr = float(tr.iloc[:14].mean())

    for value in tr.iloc[14:]: atr = (13*atr + float(value))/14

    return atr, float(completed.Low.tail(5).min()), float(completed.Close.iloc[-1])





def minute_is_current(minute, now):

    try:

        stamp = str(minute["bars"][-1]["time"])

        dt = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=KST)

        age = (now-dt).total_seconds()

        return minute.get("available") is True and -60 <= age <= 180

    except (KeyError, ValueError, IndexError, TypeError):

        return False





def entry_guard(price, atr, anchor, daily, m30, minute, quote):

    reasons = []

    if not m30.get("available"): reasons.append("30M_UNAVAILABLE")

    elif not m30.get("gold") or price < m30["vwap9"]:

        reasons.append("30M_NOT_GOLD_OR_PRICE_BELOW_VWAP9")

    if not minute.get("current"): reasons.append("LIVE_MINUTES_UNAVAILABLE_OR_STALE")

    if not atr or not anchor: reasons.append("ATR_OR_ANCHOR_UNAVAILABLE")

    elif price < anchor + .5*atr: reasons.append("BELOW_0_5ATR_SETUP")

    if quote.get("change_pct") is not None and quote["change_pct"] <= -.05:

        reasons.append("PREVCLOSE_DROP_GE_5PCT")

    bars = minute.get("bars") or []

    if len(bars) >= 5 and atr:

        recent = bars[-5:]

        volume = sum(x["volume"] for x in recent)

        vwap = sum((x["high"]+x["low"]+x["close"])/3*x["volume"] for x in recent)/volume if volume else sum(x["close"] for x in recent)/5

        lower = sum(b["close"] < a["close"] for a,b in zip(recent,recent[1:]))

        if price-bars[-4]["close"] <= -.2*atr or (lower >= 2 and price < vwap):

            reasons.append("RECENT_1M_WEAKENING")

    if daily.get("ma20") and price > daily["ma20"] + 1.5*(atr or 0):

        reasons.append("ABOVE_OVERHEAT_LIMIT")

    return reasons





# Market-wide direction alerts are observations, never confirmed buy entries.

# Only these three benchmark products receive the fast-path.

INDEX_INVERSE_PROXIES = {"114800": "069500", "251340": "229200"}





def inverse_market_early(ticker, sources, daily, own_pct, proxy_pct):

    """Require live inverse strength AND a verified fall in its own benchmark."""

    return (

        ticker in INDEX_INVERSE_PROXIES and "ETF" in sources

        and own_pct is not None and own_pct >= .01

        and proxy_pct is not None and proxy_pct <= -.01

        and daily.get("stage") in BUY_STAGES

        and daily.get("vwap_rel") == "GOLD"

        and daily.get("vwap9_dir") == "RISING"

    )





def market_weak_early(ticker, sources, own_pct):

    """KOSPI200 -1.5% early alert; -3% onward uses existing crash levels."""

    return (

        ticker == "069500" and "ETF" in sources

        and own_pct is not None and -.03 < own_pct <= -.015

    )





PAPER_CODES = {"069500", "114800", "229200", "251340"}





def paper_clock():

    return datetime.now(KST)





def paper_metadata(frame, source, quality, now, quote_received_at):

    """Additive diagnostics only. Never modify the legacy frame or verdict."""

    first_end = now.replace(hour=9, minute=30, second=0, microsecond=0)

    out = {"schema": 2, "bar_interval_minutes": 30, "contract_version": "KRX_M30_V1", "quote_received_at": quote_received_at,

           "computed_at": paper_clock().isoformat(), "quality": quality,

           "source": source, "row_count": len(frame) if frame is not None else 0,

           "first30": {"completed": False, "quality": "NOT_DUE" if now < first_end else quality,

                       "start": first_end.replace(hour=9, minute=0).isoformat(),

                       "end": first_end.isoformat(), "open": None, "high": None,

                       "low": None, "close": None, "volume": None}}

    if frame is None or quality != "VALID" or now < first_end:

        return out

    # The existing strict loader already validates source rows, gaps and OHLCV.

    latest_ts = pd.Timestamp(frame.index[-1])

    if latest_ts.tzinfo is None: latest_ts = latest_ts.tz_localize(KST)

    latest = frame.iloc[-1]

    out["latest30"] = {"start": (latest_ts-pd.Timedelta(minutes=30)).isoformat(),

        "end": latest_ts.isoformat(), "completed": True,

        "quality": "VALID" if latest.Volume > 0 else "NO_VOLUME",

        **{k.lower():float(latest[k]) for k in ("Open","High","Low","Close","Volume")}}

    stamps = pd.DatetimeIndex(frame.index)

    local = stamps.tz_convert(KST).tz_localize(None) if stamps.tz is not None else stamps

    matching = frame.loc[local == first_end.replace(tzinfo=None)]

    out["first30"]["quality"] = "FIRST30_MISSING"

    if len(matching) != 1:

        return out

    row = matching.iloc[0]

    values = {k.lower(): float(row[k]) for k in ("Open", "High", "Low", "Close", "Volume")}

    if not all(math.isfinite(v) for v in values.values()):

        out["first30"]["quality"] = "NONFINITE_OHLCV"

        return out

    out["first30"].update(values, completed=True, quality="VALID" if values["volume"] > 0 else "NO_VOLUME")

    return out





def paper_close(ticker, day, now=None):

    """Read a completed DAILY bar, never a last quote or unfinished 30m candle.



    Finality rule: KRX daily OHLCV obtained after 16:10 KST on the target day

    (or later). This is a documented paper-fill assumption, not an exchange fill.

    The caller caps attempts. Only valid results are cached.

    """

    now = now or paper_clock()

    target = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=KST)

    if ticker not in PAPER_CODES or target.date() > now.date():

        return {"ticker": ticker, "day": day, "quality": "INVALID_TARGET"}

    if not KRXCalendar.is_krx_trading_day(target.date()):

        return {"ticker": ticker, "day": day, "quality": "NOT_TRADING_DAY"}

    if now < target.replace(hour=16, minute=10):

        return {"ticker": ticker, "day": day, "quality": "CLOSE_PENDING"}

    def load():

        frame = daily_frame(ticker)  # Deliberately bypass the live 600s daily cache.

        rows = frame.loc[frame.index.date == target.date()]

        if len(rows) != 1 or rows.Volume.iloc[0] <= 0:

            return {"ticker": ticker, "day": day, "quality": "CLOSE_PENDING"}

        return {"ticker": ticker, "day": day, "quality": "CONFIRMED_DAILY_CLOSE",

                "close": float(rows.Close.iloc[0]), "observed_at": paper_clock().isoformat(),

                "market_time": target.replace(hour=15, minute=30).isoformat(),

                "source": "NAVER_DAILY_AFTER_1610", "paper_fill": True,

                "confirmation_rule": "TARGET_KRX_DAILY_BAR_OBSERVED_AFTER_1610"}

    return cached(("paper_close", ticker, day), 86400, load,

                  cache_if=lambda r: r.get("quality") == "CONFIRMED_DAILY_CLOSE")





def scan(payload, service, now=None):

    now = now or datetime.now(KST)

    ticker = service._clean_ticker(payload.get("ticker"))

    sources = normalize_sources(payload.get("sources", payload.get("source")))

    if not sources: raise service.SignalServiceError("UNIFIED_SOURCE_REQUIRED")

    base = {"ok": True, "engine_version": VERSION, "ticker": ticker,

            "name": str(payload.get("name") or ticker), "sources": sources,

            "as_of": now.isoformat(timespec="seconds"), "session": "KRX_SESSION",

            "order_api": False, "mail_sent": False, "bar_interval_minutes": 30, "contract_version": "KRX_M30_V1"}

    if payload.get("suspended") is True:

        return {**base, "stage": "WATCH", "send_allowed": False, "reasons": ["SUSPENDED"], "events": []}

    if not KRXCalendar.is_krx_trading_day(now.date()) or not 900 <= now.hour*100+now.minute <= 1530:

        return {**base, "stage": "WATCH", "send_allowed": False, "reasons": ["OUTSIDE_VERIFIED_KRX_SESSION"], "events": []}

    session = service.RetryingKiwoomSession(max_attempts=3)

    token = cached("token", 1200, lambda: service._get_kiwoom_token(session))

    # The traded ticker is always fetched live, avoiding a 15s-old BUY/STOP price.

    # Context quotes (market / sector ETF) can share a brief verified cache.

    quote = service._fetch_kiwoom_quote(session, token, ticker)

    paper_quote_received = paper_clock().isoformat() if ticker in PAPER_CODES else None

    if quote.get("available") is True:

        cached(("context_quote", ticker), 15, lambda: quote)

    def quote_for(code):

        if code == ticker: return quote

        return cached(("context_quote", code), 15,

            lambda: service._fetch_kiwoom_quote(session, token, code),

            cache_if=lambda q: isinstance(q, dict) and q.get("available") is True)

    price = number(quote.get("current_price")) if quote.get("available") else None

    events, reasons = [], []

    stop = number(payload.get("stop_price"))

    if price and stop and price <= stop:

        events.append({"key": "STOP_BREACH", "stage": "RISK", "reason": "손절선 침범", "level": 1})

    if ticker == "069500" and quote.get("change_pct") is not None:

        level = 7 if quote["change_pct"] <= -.07 else 5 if quote["change_pct"] <= -.05 else 3 if quote["change_pct"] <= -.03 else 0

        if level:

            events.append({"key": "MARKET_CRASH", "stage": "RISK", "reason": "코스피200 급락 위험", "level": level})

        elif market_weak_early(ticker, sources, quote["change_pct"]):

            events.append({"key": "MARKET_WEAK_EARLY", "stage": "DOWN_EARLY",

                           "reason": "시장조기경보: KODEX 200 -1.5% 이상 하락(실시간 검증)", "level": 1})

    daily, atr, anchor, ref = {}, number(payload.get("atr14")), None, None

    try:

        df = cached(("daily", ticker, now.date().isoformat()), 600, lambda: daily_frame(ticker)).copy()

        atr, anchor, ref = atr_and_anchor(df, now)

        completed_daily = df.loc[df.index.date < now.date()]

        if completed_daily.empty: raise ValueError("NO_COMPLETED_PREVIOUS_DAILY_BAR")
        prev_high = float(completed_daily.High.iloc[-1])
        prev_high_as_of = completed_daily.index[-1].strftime("%Y-%m-%d 15:30:00")

        if df.index[-1].date() > now.date(): raise ValueError("FUTURE_DAILY_BAR")

        if price and df.index[-1].date() == now.date():

            df.loc[df.index[-1], "Close"] = price

            df.loc[df.index[-1], "High"] = max(float(df.High.iloc[-1]), price)

            df.loc[df.index[-1], "Low"] = min(float(df.Low.iloc[-1]), price)

        official, provisional = daily_states(df, now, price)
        daily = {**official, "provisional": provisional, "prev_high": prev_high,
                 "prev_high_as_of": prev_high_as_of, "prev_close": ref,
                 "observed_at": now.isoformat(timespec="seconds")}

    except Exception as exc:

        reasons.append("DAILY_UNAVAILABLE:" + type(exc).__name__)

    minute, m30, obv15, exit_risk = {}, {}, {}, {}

    paper_frame, paper_source, paper_quality = None, "NONE", "HISTORY_NOT_REQUESTED"

    need_history = bool(daily) or bool(price and atr)

    if price and need_history:

        try:

            minute = service._fetch_kiwoom_minute_bars(session, token, ticker, limit=65, base_date=now.date())

            minute["current"] = minute_is_current(minute, now)

            slot = KRXCalendar.get_completed_30m_bar(now)

            cutoff = datetime.combine(now.date() if slot else previous_day(now),

                datetime.strptime(slot[2] if slot else "15:00", "%H:%M").time(), tzinfo=KST)

            # Completed candles are immutable for this 30m cutoff. Cache valid

            # results for 15m; a new completed cutoff always creates a new key.

            # Failures are never cached, so subsequent runs can recover.

            def load_broker_frame():

                from src.analysis.m30_kiwoom import fetch_chart

                from src.analysis.bollinger_atr_30m import prepare_completed_30m_data

                raw = fetch_chart(session,token,ticker,now)

                completed,q = prepare_completed_30m_data(raw,"KIWOOM_15M_BAR_START",cutoff,allow_sparse_completed=True)

                return completed,"KIWOOM_15M_BAR_START",q

            frame, source, quality = cached(("30m-kiwoom", ticker, cutoff.isoformat()), 1800,

                load_broker_frame,

                cache_if=lambda r: r[0] is not None and r[2] == "VALID")

            paper_frame, paper_source, paper_quality = frame, source, quality

            if frame is not None and quality == "VALID" and len(frame) >= 28:

                values = indicators(frame)

                fresh = pd.Timestamp(frame.index[-1]) == pd.Timestamp(cutoff).tz_localize(None)

                wm_val = str(getattr(frame, "attrs", {}).get("source_watermark") or "")
                m30 = {"available": fresh, "gold": values["vwap_rel"] == "GOLD",

                       "vwap9": values["vwap9"], "vwap26": values["vwap26"],

                       "vwap9_dir": values.get("vwap9_dir", "FLAT"),

                       "vwap9_slope": values.get("vwap9_slope", 0.0),

                       "completed_bar": str(frame.index[-1]), "source": source, "watermark": wm_val}

                if fresh and atr and minute.get("current"):

                    obv15 = analyze_obv15_sell_warning(frame, minute["bars"],

                        today=now.date(), fresh_price=price, atr14=atr)

                    if obv15.get("decision") == "WARN":

                        events.append({"key": "OBV15_WARNING", "stage": "RISK", "level": 1,

                            "reason": "30분봉 OBV %s봉 연속 약세·실시간 회복 미확인" % obv15.get("consecutive_no_gold_bars")})

                    exit_risk = service._analyze_30m_exit_risk_df(frame, source)

                    if exit_risk.get("decision") == "WARN":

                        events.append({"key": "SELL_PREP_WARNING", "stage": "RISK", "level": 1,

                            "reason": "30분봉 OBV·MACD 지속 약화"})

        except Exception as exc:

            paper_quality = "INTRADAY_EXCEPTION:" + type(exc).__name__

            reasons.append("INTRADAY_UNAVAILABLE:" + type(exc).__name__)

    stage = daily.get("stage", "WATCH")

    inverse_info_only = False

    if stage in BUY_STAGES:

        blockers = entry_guard(price or 0, atr, anchor, daily, m30, minute, quote)

        reasons.extend(blockers)

        # A verified market-wide inverse turn deserves an early observation even

        # if the normal stock-entry guards (overheat/30m) are not yet satisfied.

        # The strong BUY_CONFIRMED rules remain untouched for every symbol.

        inverse_early = False

        if ticker in INDEX_INVERSE_PROXIES and "ETF" in sources and price:

            try:

                benchmark = quote_for(INDEX_INVERSE_PROXIES[ticker])

            except Exception:

                benchmark = {}  # No verified benchmark: no early inverse alert.

            inverse_early = inverse_market_early(

                ticker, sources, daily, quote.get("change_pct"),

                benchmark.get("change_pct") if benchmark.get("available") else None)

        if blockers:

            if inverse_early and "RECENT_1M_WEAKENING" not in blockers:

                stage = "BUY_SETUP"

                inverse_info_only = True

                reasons.append("INDEX_INVERSE_EARLY_ONLY_NOT_BUY_CONFIRMED")

            else:

                stage = "WATCH"

        elif stage == "BUY_CONFIRMED" and price < anchor + .6*atr:

            stage = "BUY_SETUP"

    # Final source-side bullish filter, after the inverse early-observation
    # exception: no BUY_SETUP/BUY_CONFIRMED may pass below or without the
    # latest completed daily VWAP9. Downside and risk events are untouched.
    pre_gate_stage = stage
    stage, daily_vwap9_passed = apply_daily_vwap9_gate(stage, price, daily, now)
    if pre_gate_stage in BUY_STAGES and not daily_vwap9_passed:
        reasons.append("DAILY_VWAP9_GATE_CLOSED")

    if stage != "WATCH" and price:

        event_reason = ("인버스 시장상승조짐(실시간 기초지수 하락 확인·매수확인 아님·추격주의)"

                        if inverse_info_only else "완성 일봉 기준("+str(daily.get("as_of", ""))+"): "+str(daily.get("regime", stage)))

        events.append({"key": stage, "stage": stage, "level": 1, "reason": event_reason})

    if price and ref and atr and price <= ref - atr:

        events.append({"key": "ATR_DOWN_1_0", "stage": "DOWN_EARLY", "level": 1, "reason": "전일종가 대비 1 ATR 이상 하락"})

    if events:

        stage = max((x["stage"] for x in events), key=lambda x: {"WATCH":0,"BUY_SETUP":1,"BUY_CONFIRMED":2,"DOWN_EARLY":3,"DOWN_CONFIRMED":4,"RISK":5}[x])

        if stage not in BUY_STAGES:

            events = [e for e in events if e["stage"] not in BUY_STAGES]

    market = str(payload.get("market") or "UNKNOWN").upper()

    proxy = {"KOSPI":"069500", "KOSDAQ":"229200"}.get(market, "")

    sector = str(payload.get("sector_etf_code") or "")

    allowed = set(payload.get("etf_universe") or [])

    if sector not in allowed or sector == ticker: sector = ""

    def context_quote(code):

        try: return quote_for(code) if code else {}

        except Exception: return {}

    pq, sq = context_quote(proxy), context_quote(sector)

    decision = evaluate_signal({**payload, "sources": sources, "stage": stage,

        "technical_verified": bool(events), "live_verified": bool(price),

        "market_pct": pq.get("change_pct") if pq.get("available") else None,

        "sector_etf_pct": sq.get("change_pct") if sq.get("available") else None})

    # One active Jev policy for every buy, independent of discovery source.

    gate = {"decision":"SEND", "reason":"RISK_BYPASS_OR_NO_BUY"}

    if stage in BUY_STAGES and decision["send_allowed"] and payload.get("evaluate_jev", True):

        if inverse_info_only:

            # Explicitly informational, non-executable alert. JEV still guards

            # confirmed entries; it must not hide market-wide early observations.

            gate = {"status": "EARLY_MARKET_INFO", "decision": "SEND",

                    "reason": "INDEX_INVERSE_EARLY_ONLY_NO_CONFIRMED_BUY"}

        else:

            cfg = replace(JevGateSettings.from_env("UNIFIED"), mode="ACTIVE")

            gate = evaluate_jev_alert_gate({"source":"UNIFIED", "ticker":ticker, "name":base["name"],

                "event_keys":[stage], "current_price":price, "change_pct":quote.get("change_pct"),

                **daily, "context":{"market_adjustment":decision["confidence_adjustment"], "30m":m30}}, settings=cfg)

            # Inability to verify the final buy filter is a hold, never a new buy.

            if gate.get("status") != "OK" or gate.get("decision") != "SEND":

                decision["send_allowed"] = False

                reasons.append("JEV_BUY_HOLD:" + str(gate.get("reason")))

    stacked_m30, gt_daily_v9 = m30_bullish_qualification(price, m30, daily, now)

    daily_v9 = daily.get("vwap9")

    prev_high_val = daily.get("prev_high")

    gt_prev_high = bool(price and prev_high_val and price > prev_high_val)

    # Previous-day high is retained as context only; the user's required
    # bullish confirmation is M30 alignment + current price > daily VWAP9.
    bull_confirmed_sim = bool(stacked_m30 and gt_daily_v9)

    sim_data = {

        "qualified": bull_confirmed_sim,

        "stacked_m30": stacked_m30,

        "price_gt_daily_vwap9": gt_daily_v9,

        "daily_vwap9_gate": {"qualified": gt_daily_v9, "basis": daily.get("basis"),
                             "as_of": daily.get("as_of")},

        "price_gt_prev_high": gt_prev_high,

        "daily_vwap9": daily_v9,
        "daily_vwap9_as_of": daily.get("as_of"),

        "prev_high": prev_high_val,
        "prev_high_as_of": daily.get("prev_high_as_of"),

        "m30_vwap9": m30.get("vwap9"),

        "m30_vwap26": m30.get("vwap26"),

        "m30_vwap9_slope": m30.get("vwap9_slope", 0.0),

        "m30_vwap9_dir": m30.get("vwap9_dir", "FLAT"),

        "live_price": price,

        "completed_bar": m30.get("completed_bar"),
        "price_as_of": now.isoformat(timespec="seconds"),
        "watermark": m30.get("watermark", "")

    }

    if bull_confirmed_sim:

        reasons.append("SIM_BULL_CONFIRMED:P=%s>DV9=%s;SLOPE=%s(%s)" % (

            price, daily_v9, m30.get("vwap9_slope", 0.0), m30.get("vwap9_dir", "FLAT")

        ))

    paper_extra = {}

    if ticker in PAPER_CODES:

        try:

            paper_extra["paper_data"] = paper_metadata(paper_frame, paper_source, paper_quality, now, paper_quote_received)

            paper_extra["paper_data"]["m30"] = dict(m30)

        except Exception:

            paper_extra["paper_data"] = {"schema": 2, "quality": "METADATA_ERROR"}

    return {**base, **decision, **paper_extra, "engine_version": VERSION, "events": events, "sim_bull_confirmed": sim_data,

        "technical": daily, "m30": m30, "obv15": obv15, "exit_risk": exit_risk,

        "live_price": price, "live_change_pct": quote.get("change_pct"),

        "live_price_source": quote.get("source"), "atr14":atr, "anchor":anchor,

        "market_proxy":proxy, "sector_etf":sector, "jev_gate":gate,

        "reasons":decision["reasons"]+reasons, "kiwoom_429_retry_count":session.retry_count}
