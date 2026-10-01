"""Held-position intraday regime helpers.

Purpose
-------
Pure indicator/regime calculations for the GoogleFinance held-position sidecar.
This module does not send mail, place orders, touch the production DB, or mutate
the Google Sheet. It is intentionally isolated so we can verify the math before
wiring it into the 5-minute monitor.

Definitions are aligned with the existing 45m ADD ADVISORY engine:
- VWAP9 / VWAP26 use typical price = (High + Low + Close) / 3 weighted by Volume.
- OBV signal is OBV9 WMA(9), matching add_advisory_engine.py.

The extra fields here focus on *direction* (slope) rather than only cross state.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = ("High", "Low", "Close", "Volume")


def _ensure_numeric_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        raise ValueError("OHLCV dataframe is empty")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {missing}")

    out = df.copy()
    for col in REQUIRED_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=list(REQUIRED_COLUMNS))
    out = out[out["Volume"] >= 0]
    if len(out) < 26:
        raise ValueError(f"Need at least 26 valid bars, got {len(out)}")
    return out


def _wma(series: pd.Series, window: int) -> pd.Series:
    weights = np.arange(1, window + 1, dtype=float)
    denom = float(weights.sum())
    return series.rolling(window=window).apply(
        lambda x: float(np.dot(x, weights) / denom),
        raw=True,
    )


def _linear_slope(series: pd.Series, lookback: int) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna().tail(lookback)
    if len(values) < 2:
        return float("nan")
    y = values.to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)
    return float(np.polyfit(x, y, 1)[0])


def _pct_slope(series: pd.Series, lookback: int) -> float:
    slope = _linear_slope(series, lookback)
    values = pd.to_numeric(series, errors="coerce").dropna().tail(lookback)
    if not np.isfinite(slope) or len(values) == 0:
        return float("nan")
    base = float(np.nanmean(np.abs(values.to_numpy(dtype=float))))
    if base <= 1e-12:
        return 0.0
    return float(slope / base)


def _obv_normalized_slope(obv: pd.Series, volume: pd.Series, lookback: int) -> float:
    slope = _linear_slope(obv, lookback)
    recent_vol = pd.to_numeric(volume, errors="coerce").dropna().tail(lookback)
    if not np.isfinite(slope) or len(recent_vol) == 0:
        return float("nan")
    scale = float(np.nanmean(np.abs(recent_vol.to_numpy(dtype=float))))
    if scale <= 1e-12:
        return 0.0
    return float(slope / scale)


def _direction(value: float, epsilon: float) -> str:
    if not np.isfinite(value):
        return "UNKNOWN"
    if value > epsilon:
        return "RISING"
    if value < -epsilon:
        return "FALLING"
    return "FLAT"


def build_indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return OHLCV plus VWAP9/26, OBV, and OBV9(WMA9).

    Input bars may be daily or intraday; semantics are determined by the caller.
    For the held GoogleFinance monitor we intend to feed recent daily bars plus
    the current in-progress day snapshot.
    """
    out = _ensure_numeric_frame(df)

    typical_price = (out["High"] + out["Low"] + out["Close"]) / 3.0
    tpv = typical_price * out["Volume"]

    out["VWAP9"] = (
        tpv.rolling(window=9).sum()
        / out["Volume"].rolling(window=9).sum().replace(0, np.nan)
    )
    out["VWAP26"] = (
        tpv.rolling(window=26).sum()
        / out["Volume"].rolling(window=26).sum().replace(0, np.nan)
    )

    price_diff = out["Close"].diff()
    direction = np.where(price_diff > 0, 1.0, np.where(price_diff < 0, -1.0, 0.0))
    out["OBV"] = pd.Series(
        direction * out["Volume"].to_numpy(dtype=float), index=out.index
    ).cumsum()
    out["OBV9"] = _wma(out["OBV"], 9)

    out["VWAP_GOLD"] = out["VWAP9"] > out["VWAP26"]
    out["OBV_GOLD"] = out["OBV"] > out["OBV9"]
    return out


@dataclass(frozen=True)
class HeldRegimeSnapshot:
    vwap9: float
    vwap26: float
    vwap_relation: str
    vwap_cross: str
    vwap9_slope_pct: float
    vwap26_slope_pct: float
    vwap9_direction: str
    vwap26_direction: str

    obv: float
    obv9: float
    obv_relation: str
    obv_cross: str
    obv_slope_norm: float
    obv9_slope_norm: float
    obv_direction: str
    obv9_direction: str

    regime: str
    regime_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def latest_regime_snapshot(
    df: pd.DataFrame,
    *,
    slope_lookback: int = 3,
    vwap_flat_epsilon: float = 0.0003,
    obv_flat_epsilon: float = 0.05,
) -> HeldRegimeSnapshot:
    """Summarize cross + slope regime.

    Defaults:
    - slope_lookback=3: detect a genuine 'head-up/head-down' turn without relying
      on a single tick.
    - VWAP epsilon is relative slope per bar (0.03%).
    - OBV epsilon is normalized by recent average volume (0.05x average volume
      per bar), so tiny numerical moves are treated as flat.
    """
    ind = build_indicator_frame(df)
    valid = ind.dropna(subset=["VWAP9", "VWAP26", "OBV9"])
    if len(valid) < max(2, slope_lookback):
        raise ValueError("Insufficient fully-formed indicator bars")

    cur = valid.iloc[-1]
    prev = valid.iloc[-2]

    v9 = float(cur["VWAP9"])
    v26 = float(cur["VWAP26"])
    v9_prev = float(prev["VWAP9"])
    v26_prev = float(prev["VWAP26"])

    vwap_relation = "GOLD" if v9 > v26 else "DEAD"
    if v9 > v26 and v9_prev <= v26_prev:
        vwap_cross = "GOLD_CROSS"
    elif v9 < v26 and v9_prev >= v26_prev:
        vwap_cross = "DEAD_CROSS"
    else:
        vwap_cross = "NONE"

    v9_slope_pct = _pct_slope(valid["VWAP9"], slope_lookback)
    v26_slope_pct = _pct_slope(valid["VWAP26"], slope_lookback)
    v9_dir = _direction(v9_slope_pct, vwap_flat_epsilon)
    v26_dir = _direction(v26_slope_pct, vwap_flat_epsilon)

    obv = float(cur["OBV"])
    obv9 = float(cur["OBV9"])
    obv_prev = float(prev["OBV"])
    obv9_prev = float(prev["OBV9"])

    obv_relation = "GOLD" if obv > obv9 else "DEAD"
    if obv > obv9 and obv_prev <= obv9_prev:
        obv_cross = "GOLD_CROSS"
    elif obv < obv9 and obv_prev >= obv9_prev:
        obv_cross = "DEAD_CROSS"
    else:
        obv_cross = "NONE"

    obv_slope_norm = _obv_normalized_slope(valid["OBV"], valid["Volume"], slope_lookback)
    obv9_slope_norm = _obv_normalized_slope(valid["OBV9"], valid["Volume"], slope_lookback)
    obv_dir = _direction(obv_slope_norm, obv_flat_epsilon)
    obv9_dir = _direction(obv9_slope_norm, obv_flat_epsilon)

    # Trend-first, cross-second. A cross against a falling long/baseline is
    # treated as a bounce until the long/baseline itself turns.
    if (
        vwap_relation == "GOLD"
        and v9_dir == "RISING"
        and v26_dir == "RISING"
        and obv_relation == "GOLD"
        and obv_dir == "RISING"
        and obv9_dir == "RISING"
    ):
        regime = "BULL_CONFIRMED"
        reason = "VWAP9↑/VWAP26↑/VWAP_GOLD + OBV↑/OBV9↑/OBV_GOLD"
    elif (
        vwap_relation == "DEAD"
        and v9_dir == "FALLING"
        and v26_dir == "FALLING"
        and obv_relation == "DEAD"
        and obv_dir == "FALLING"
        and obv9_dir == "FALLING"
    ):
        regime = "BEAR_CONFIRMED"
        reason = "VWAP9↓/VWAP26↓/VWAP_DEAD + OBV↓/OBV9↓/OBV_DEAD"
    elif v9_dir == "RISING" and v26_dir == "FALLING":
        regime = "BOUNCE_IN_DOWNREGIME"
        reason = "VWAP9↑지만 VWAP26↓: 단기 반등 우선, 장기 추세전환 미확인"
    elif obv_dir == "RISING" and obv9_dir == "FALLING":
        regime = "OBV_REBOUND_BASE_DOWN"
        reason = "OBV↑지만 OBV9 기준선↓: 수급 반등은 있으나 기준선 전환 미확인"
    elif v26_dir == "RISING" and v9_dir == "FALLING" and vwap_relation == "GOLD":
        regime = "PULLBACK_IN_UPREGIME"
        reason = "VWAP26 상승 유지 + VWAP9 하락: 상승국면 내 단기 눌림"
    elif v9_dir == "RISING" or obv_dir == "RISING":
        regime = "EARLY_IMPROVEMENT"
        reason = "단기선 또는 OBV가 먼저 상승 전환; 장기/기준선 확인 필요"
    elif v9_dir == "FALLING" or obv_dir == "FALLING":
        regime = "EARLY_WEAKENING"
        reason = "단기선 또는 OBV 약화; 장기/기준선 훼손 여부 확인 필요"
    else:
        regime = "NEUTRAL"
        reason = "방향성 우위 없음"

    return HeldRegimeSnapshot(
        vwap9=round(v9, 4),
        vwap26=round(v26, 4),
        vwap_relation=vwap_relation,
        vwap_cross=vwap_cross,
        vwap9_slope_pct=round(float(v9_slope_pct), 8),
        vwap26_slope_pct=round(float(v26_slope_pct), 8),
        vwap9_direction=v9_dir,
        vwap26_direction=v26_dir,
        obv=round(obv, 4),
        obv9=round(obv9, 4),
        obv_relation=obv_relation,
        obv_cross=obv_cross,
        obv_slope_norm=round(float(obv_slope_norm), 6),
        obv9_slope_norm=round(float(obv9_slope_norm), 6),
        obv_direction=obv_dir,
        obv9_direction=obv9_dir,
        regime=regime,
        regime_reason=reason,
    )
