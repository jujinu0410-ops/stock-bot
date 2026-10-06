import math
import os
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import yfinance as yf
import pandas as pd
import numpy as np
import sys
from pathlib import Path


class DataStaleError(ValueError):
    """Market input is stale or has future timestamps; do not generate a report."""

@dataclass
class StrategyResult:
    current_price: float
    atr14: float
    atr_pct: float
    ma5: float
    ma20: float
    ma60: float
    ma120: float
    high_20d: float
    low_20d:  float
    high_60d: float
    low_60d:  float
    recent_support:    float
    recent_resistance: float
    swing_low:         float
    swing_high:        float
    entry_zone_low:  float
    entry_zone_high: float
    pullback_zone:   float
    breakout_trigger: float
    stop_price:        float
    target_1:          float
    target_2:          float
    trailing_distance: float
    representative_entry: float
    risk_reward_ratio: float
    risk_pct: float
    reward_pct: float
    rr_target1: float
    rr_target2: float
    chase_risk: str
    chase_reasons: List[str]
    calc_notes: Dict[str, Any]
    ohlcv_rows: int
    data_source: str
    market_symbol: str = ""
    daily_ohlcv_asof: str = ""
    current_price_asof: str = ""
    intraday_45m_asof: str = ""
    risk_per_share: float = 0.0
    reward1_per_share: float = 0.0
    reward2_per_share: float = 0.0

def _safe_float(x) -> Optional[float]:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None

def _flatten_df(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df

def compute_atr14(df: pd.DataFrame) -> float:
    high = df["High"].values
    low  = df["Low"].values
    close = df["Close"].values
    tr_list = []
    for i in range(1, len(df)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i]  - close[i - 1]),
        )
        tr_list.append(tr)
    if len(tr_list) < 14:
        return float(np.mean(tr_list)) if tr_list else 0.0
    return float(np.mean(tr_list[-14:]))

def _rnd(v: float) -> float:
    # Reuse V8's existing KRX helper; never modify the stock trading system.
    v8_path = str(Path(os.environ.get("V8_ENGINE_PATH") or (Path(__file__).resolve().parents[1] / "stock_analysis_system")).resolve())
    if v8_path not in sys.path:
        sys.path.insert(0, v8_path)
    from src.analysis.technical_analysis import adjust_krx_tick_size
    return adjust_krx_tick_size(v, direction="nearest")

def compute_strategy(stock_code: str, dto=None, *, now=None) -> Optional[StrategyResult]:
    # Same safe suffix fallback as the existing V8 market provider.
    market = str(getattr(dto, "market_type", "")).upper()
    suffixes = (".KQ",) if market == "KOSDAQ" else ((".KS",) if market == "KOSPI" else (".KS", ".KQ"))
    df = None
    for suffix in suffixes:
        ticker = f"{stock_code}{suffix}"
        try:
            raw = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=False)
            candidate = _flatten_df(raw).dropna(subset=["Open", "High", "Low", "Close"])
            if len(candidate) >= 20:
                df = candidate.sort_index()
                break
        except Exception:
            continue

    if df is None:
        return None

    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="Asia/Seoul")
    now = now.tz_localize("Asia/Seoul") if now.tzinfo is None else now.tz_convert("Asia/Seoul")
    latest = pd.Timestamp(df.index[-1])
    latest = latest.tz_localize("Asia/Seoul") if latest.tzinfo is None else latest.tz_convert("Asia/Seoul")
    from daily_v8.krx_calendar import get_last_completed_trading_day

    last_trading_day = get_last_completed_trading_day(now.date())
    if latest > now or latest.date() < last_trading_day:
        raise DataStaleError("DATA_STALE: daily OHLCV is stale or future-dated")
    current_price = _safe_float(getattr(dto, "current_price", None)) or float(df["Close"].iloc[-1])
    price_asof = str(getattr(dto, "current_price_asof", "") or "")
    if not price_asof:
        # A collection timestamp is not a quote timestamp. Use the dated daily close.
        current_price = float(df["Close"].iloc[-1])
        price_asof = latest.isoformat()
    else:
        quote_time = pd.Timestamp(price_asof)
        quote_time = quote_time.tz_localize("Asia/Seoul") if quote_time.tzinfo is None else quote_time.tz_convert("Asia/Seoul")
        if quote_time > now or now - quote_time > pd.Timedelta(minutes=90):
            raise DataStaleError("DATA_STALE: current price is stale or future-dated")
    # ATR and MA share the same timestamped OHLCV instead of undated DTO inputs.
    atr14 = compute_atr14(df)
    if current_price <= 0 or not math.isfinite(atr14) or atr14 <= 0:
        return None
    atr_pct = round(atr14 / current_price * 100, 2) if current_price else 0.0

    close = df["Close"]
    ma5   = float(close.rolling(5).mean().iloc[-1])   if len(close) >= 5   else current_price
    ma20  = float(close.rolling(20).mean().iloc[-1])  if len(close) >= 20  else current_price
    ma60  = float(close.rolling(60).mean().iloc[-1])  if len(close) >= 60  else current_price
    ma120 = float(close.rolling(120).mean().iloc[-1]) if len(close) >= 120 else current_price

    recent_20 = df.tail(20)
    recent_60 = df.tail(60)
    high_20d = float(recent_20["High"].max())
    low_20d  = float(recent_20["Low"].min())
    high_60d = float(recent_60["High"].max())
    low_60d  = float(recent_60["Low"].min())

    tail30 = df.tail(30)
    swing_low  = float(tail30["Low"].min())
    swing_high = float(tail30["High"].max())

    recent_support    = _rnd(max(ma20, low_20d) * 0.5 + min(ma20, low_20d) * 0.5)
    recent_resistance = _rnd(high_20d)

    dto_buy = _safe_float(getattr(dto, "candidate_buy_price", None))
    dto_stop = _safe_float(getattr(dto, "candidate_stop_price", None))
    dto_target = _safe_float(getattr(dto, "candidate_target_price", None))

    chase_reasons = []
    ma20_gap_pct = (current_price - ma20) / ma20 * 100 if ma20 else 0

    if ma20_gap_pct > 2 * atr_pct:
        chase_reasons.append(f"MA20 이격 {ma20_gap_pct:.1f}% > ATR*2 ({2*atr_pct:.1f}%)")
    if current_price >= high_20d * 0.98:
        chase_reasons.append(f"20D 고점({_rnd(high_20d):,}) 근접")
    if current_price >= high_60d * 0.97:
        chase_reasons.append(f"60D 고점({_rnd(high_60d):,}) 근접")

    obv_trend = str(getattr(dto, "obv_45m_trend", "")).lower()
    if "dead" in obv_trend or "데드" in obv_trend:
        chase_reasons.append("45분봉 OBV 데드크로스 (단기 이탈)")

    if len(chase_reasons) >= 3:
        chase_risk = "HIGH"
    elif len(chase_reasons) >= 1:
        chase_risk = "MEDIUM"
    else:
        chase_risk = "LOW"

    # ENTRY LOGIC
    entry_zone_high = current_price if chase_risk == "LOW" else _rnd(current_price - 0.3 * atr14)
    if chase_risk == "HIGH":
        pullback_zone = _rnd(current_price - 1.5 * atr14)
        entry_zone_low = _rnd(pullback_zone)
    else:
        pullback_zone = _rnd(dto_buy or (current_price - 1.5 * atr14))
        entry_zone_low = _rnd(max(pullback_zone, recent_support))

    # DAMAGE_PRICE LOGIC
    if dto_stop and dto_stop < pullback_zone:
        stop_price = _rnd(dto_stop)
    else:
        stop_price = _rnd(pullback_zone - 0.5 * atr14)
    stop_price = max(stop_price, _rnd(swing_low - 0.2*atr14))

    if stop_price >= pullback_zone:
        stop_price = _rnd(pullback_zone - 0.5 * atr14)

    # BREAKOUT LOGIC
    breakout_trigger = _rnd(max(recent_resistance + 0.1 * atr14, current_price + 0.5 * atr14))

    # TARGET LOGIC
    target_1 = _rnd(dto_target or (current_price + 2.0 * atr14))
    if target_1 <= current_price:
        target_1 = _rnd(current_price + 2.0 * atr14)
    target_2 = _rnd(max(current_price + 3.0 * atr14, target_1 + 1.0 * atr14))
    trailing_distance = _rnd(0.8 * atr14)

    representative_entry = current_price if chase_risk == "LOW" else pullback_zone
    
    # Recheck after tick rounding: rounding can collapse strict inequalities.
    if stop_price >= pullback_zone:
        stop_price = _rnd(pullback_zone - max(atr14 * 0.5, 1))
    if not (0 < stop_price < pullback_zone <= entry_zone_high and target_2 > target_1 > representative_entry):
        return None
    entry_zone_low = min(entry_zone_low, entry_zone_high)
    entry_zone_low = min(entry_zone_low, representative_entry)
    risk_abs   = representative_entry - stop_price
    reward1_abs = target_1 - representative_entry
    reward2_abs = target_2 - representative_entry
    
    if risk_abs < atr14 * 0.5:
        risk_abs = atr14 * 0.5
        stop_price = _rnd(representative_entry - risk_abs)
    risk_abs = representative_entry - stop_price
    if not (0 < stop_price < pullback_zone and risk_abs > 0):
        return None
    risk_pct   = round(risk_abs / representative_entry * 100, 2) if representative_entry else 0.0
    reward_pct = round(reward1_abs / representative_entry * 100, 2) if representative_entry else 0.0
    rr_target1 = round(reward1_abs / risk_abs, 2)
    rr_target2 = round(reward2_abs / risk_abs, 2)
    rrr = rr_target1

    calc_notes = {
        "atr14_source": "yfinance_computed",
        "atr14_asof": latest.isoformat(),
        "current_price_source": "DTO" if getattr(dto, "current_price_asof", "") else "yfinance_daily_close",
        "buy_price_source": "DTO.candidate_buy_price" if dto_buy else "current - 1.5*ATR",
        "stop_price_source": "DTO.candidate_stop_price" if dto_stop else "pullback - 0.5*ATR",
        "target_1_source": "DTO.candidate_target_price" if dto_target else "current + 2*ATR",
        "ma20_gap_pct": round(ma20_gap_pct, 2),
        "atr14_in_pct": atr_pct,
    }

    return StrategyResult(
        current_price=current_price,
        atr14=atr14,
        atr_pct=atr_pct,
        ma5=_rnd(ma5),
        ma20=_rnd(ma20),
        ma60=_rnd(ma60),
        ma120=_rnd(ma120),
        high_20d=_rnd(high_20d),
        low_20d=_rnd(low_20d),
        high_60d=_rnd(high_60d),
        low_60d=_rnd(low_60d),
        recent_support=recent_support,
        recent_resistance=recent_resistance,
        swing_low=_rnd(swing_low),
        swing_high=_rnd(swing_high),
        entry_zone_low=entry_zone_low,
        entry_zone_high=entry_zone_high,
        pullback_zone=pullback_zone,
        breakout_trigger=breakout_trigger,
        stop_price=stop_price,
        target_1=target_1,
        target_2=target_2,
        trailing_distance=trailing_distance,
        representative_entry=representative_entry,
        risk_reward_ratio=rrr,
        risk_pct=risk_pct,
        reward_pct=reward_pct,
        rr_target1=rr_target1,
        rr_target2=rr_target2,
        chase_risk=chase_risk,
        chase_reasons=chase_reasons,
        calc_notes=calc_notes,
        ohlcv_rows=len(df),
        data_source="yfinance_1y",
        market_symbol=ticker,
        daily_ohlcv_asof=latest.isoformat(),
        current_price_asof=price_asof,
        intraday_45m_asof=str(getattr(dto, "intraday_45m_asof", "") or ""),
        risk_per_share=risk_abs,
        reward1_per_share=reward1_abs,
        reward2_per_share=reward2_abs,
    )


