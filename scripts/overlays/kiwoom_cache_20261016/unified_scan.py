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
from src.analysis.held_obv15_warning import analyze_obv15_sell_warning
from src.analysis.jev_alert_gate import JevGateSettings, evaluate_jev_alert_gate
from src.runtime.krx_calendar import KRXCalendar

VERSION = "three-lists-v2"
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
        # Do not retain transient 45m/Yahoo/quote failures as valid evidence.
        if cache_if is None or cache_if(value):
            now_mono = time.monotonic()
            if len(_CACHE) >= 1000:
                # Prune expired entries first; never flush all warm token/market
                # caches simultaneously after 1,500 historical 45m keys.
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
    result = {"vwap9": float(v9.iloc[-1]), "vwap26": float(v26.iloc[-1]),
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


def entry_guard(price, atr, anchor, daily, m45, minute, quote):
    reasons = []
    if not m45.get("available"): reasons.append("45M_UNAVAILABLE")
    elif not m45.get("gold") or price < m45["vwap9"]:
        reasons.append("45M_NOT_GOLD_OR_PRICE_BELOW_VWAP9")
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


def scan(payload, service, now=None):
    now = now or datetime.now(KST)
    ticker = service._clean_ticker(payload.get("ticker"))
    sources = normalize_sources(payload.get("sources", payload.get("source")))
    if not sources: raise service.SignalServiceError("UNIFIED_SOURCE_REQUIRED")
    base = {"ok": True, "engine_version": VERSION, "ticker": ticker,
            "name": str(payload.get("name") or ticker), "sources": sources,
            "as_of": now.isoformat(timespec="seconds"), "session": "KRX_SESSION",
            "order_api": False, "mail_sent": False}
    if payload.get("suspended") is True:
        return {**base, "stage": "WATCH", "send_allowed": False, "reasons": ["SUSPENDED"], "events": []}
    if not KRXCalendar.is_krx_trading_day(now.date()) or not 900 <= now.hour*100+now.minute <= 1530:
        return {**base, "stage": "WATCH", "send_allowed": False, "reasons": ["OUTSIDE_VERIFIED_KRX_SESSION"], "events": []}
    session = service.RetryingKiwoomSession(max_attempts=3)
    token = cached("token", 1200, lambda: service._get_kiwoom_token(session))
    # The traded ticker is always fetched live, avoiding a 15s-old BUY/STOP price.
    # Context quotes (market / sector ETF) can share a brief verified cache.
    quote = service._fetch_kiwoom_quote(session, token, ticker)
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
    daily, atr, anchor, ref = {}, number(payload.get("atr14")), None, None
    try:
        df = cached(("daily", ticker, now.date().isoformat()), 600, lambda: daily_frame(ticker)).copy()
        atr, anchor, ref = atr_and_anchor(df, now)
        if df.index[-1].date() > now.date(): raise ValueError("FUTURE_DAILY_BAR")
        if price and df.index[-1].date() == now.date():
            df.loc[df.index[-1], "Close"] = price
            df.loc[df.index[-1], "High"] = max(float(df.High.iloc[-1]), price)
            df.loc[df.index[-1], "Low"] = min(float(df.Low.iloc[-1]), price)
        daily = {**indicators(df), "ma20": float(df.Close.tail(20).mean())}
    except Exception as exc:
        reasons.append("DAILY_UNAVAILABLE:" + type(exc).__name__)
    minute, m45, obv15, exit_risk = {}, {}, {}, {}
    need_history = bool(daily) or bool(price and atr)
    if price and need_history:
        try:
            minute = service._fetch_kiwoom_minute_bars(session, token, ticker, limit=65, base_date=now.date())
            minute["current"] = minute_is_current(minute, now)
            slot = KRXCalendar.get_completed_45m_bar(now)
            cutoff = datetime.combine(now.date() if slot else previous_day(now),
                datetime.strptime(slot[2] if slot else "15:00", "%H:%M").time(), tzinfo=KST)
            # Completed candles are immutable for this 45m cutoff. Cache valid
            # results for 15m; a new completed cutoff always creates a new key.
            # Failures are never cached, so subsequent runs can recover.
            frame, source, quality = cached(("45m", ticker, cutoff.isoformat()), 900,
                lambda: service.get_strictly_sliced_45m_df(ticker, cutoff, allow_sparse_completed=True),
                cache_if=lambda r: r[0] is not None and r[2] == "VALID")
            if frame is not None and quality == "VALID" and len(frame) >= 28:
                values = indicators(frame)
                fresh = frame.index[-1].date() == cutoff.date()
                m45 = {"available": fresh, "gold": values["vwap_rel"] == "GOLD",
                       "vwap9": values["vwap9"], "vwap26": values["vwap26"],
                       "completed_bar": str(frame.index[-1]), "source": source}
                if fresh and atr and minute.get("current"):
                    obv15 = analyze_obv15_sell_warning(frame, minute["bars"],
                        today=now.date(), fresh_price=price, atr14=atr)
                    if obv15.get("decision") == "WARN":
                        events.append({"key": "OBV15_WARNING", "stage": "RISK", "level": 1,
                            "reason": "45분봉 OBV %s봉 연속 약세·실시간 회복 미확인" % obv15.get("consecutive_no_gold_bars")})
                    exit_risk = service._analyze_45m_exit_risk_df(frame, source)
                    if exit_risk.get("decision") == "WARN":
                        events.append({"key": "SELL_PREP_WARNING", "stage": "RISK", "level": 1,
                            "reason": "45분봉 OBV·MACD 지속 약화"})
        except Exception as exc:
            reasons.append("INTRADAY_UNAVAILABLE:" + type(exc).__name__)
    stage = daily.get("stage", "WATCH")
    if stage in BUY_STAGES:
        blockers = entry_guard(price or 0, atr, anchor, daily, m45, minute, quote)
        reasons.extend(blockers)
        if blockers: stage = "WATCH"
        elif stage == "BUY_CONFIRMED" and price < anchor + .6*atr: stage = "BUY_SETUP"
    if stage != "WATCH" and price:
        events.append({"key": stage, "stage": stage, "level": 1, "reason": daily.get("regime", stage)})
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
        cfg = replace(JevGateSettings.from_env("UNIFIED"), mode="ACTIVE")
        gate = evaluate_jev_alert_gate({"source":"UNIFIED", "ticker":ticker, "name":base["name"],
            "event_keys":[stage], "current_price":price, "change_pct":quote.get("change_pct"),
            **daily, "context":{"market_adjustment":decision["confidence_adjustment"], "45m":m45}}, settings=cfg)
        # Inability to verify the final buy filter is a hold, never a new buy.
        if gate.get("status") != "OK" or gate.get("decision") != "SEND":
            decision["send_allowed"] = False
            reasons.append("JEV_BUY_HOLD:" + str(gate.get("reason")))
    return {**base, **decision, "engine_version": VERSION, "events": events,
        "technical": daily, "m45": m45, "obv15": obv15, "exit_risk": exit_risk,
        "live_price": price, "live_change_pct": quote.get("change_pct"),
        "live_price_source": quote.get("source"), "atr14":atr, "anchor":anchor,
        "market_proxy":proxy, "sector_etf":sector, "jev_gate":gate,
        "reasons":decision["reasons"]+reasons, "kiwoom_429_retry_count":session.retry_count}
