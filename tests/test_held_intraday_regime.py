import numpy as np
import pandas as pd

from src.analysis.held_intraday_regime import build_indicator_frame, latest_regime_snapshot


def _frame(close, volume=None):
    close = np.asarray(close, dtype=float)
    if volume is None:
        volume = np.linspace(1000.0, 2000.0, len(close))
    else:
        volume = np.asarray(volume, dtype=float)
    return pd.DataFrame({
        "High": close + 2.0,
        "Low": close - 2.0,
        "Close": close,
        "Volume": volume,
    })


def test_rising_long_and_short_with_rising_obv_is_bull_confirmed():
    close = np.linspace(100.0, 140.0, 40)
    snap = latest_regime_snapshot(_frame(close))
    assert snap.vwap_relation == "GOLD"
    assert snap.vwap9_direction == "RISING"
    assert snap.vwap26_direction == "RISING"
    assert snap.obv_relation == "GOLD"
    assert snap.obv_direction == "RISING"
    assert snap.obv9_direction == "RISING"
    assert snap.regime == "BULL_CONFIRMED"


def test_vwap9_turns_up_while_vwap26_still_falls_is_bounce_not_confirmed():
    close = np.concatenate([
        np.linspace(140.0, 100.0, 34),
        np.array([101.0, 103.0, 106.0, 110.0, 115.0, 121.0]),
    ])
    volume = np.concatenate([np.full(34, 1500.0), np.full(6, 1800.0)])
    snap = latest_regime_snapshot(_frame(close, volume))
    assert snap.vwap9_direction == "RISING"
    assert snap.vwap26_direction == "FALLING"
    assert snap.regime == "BOUNCE_IN_DOWNREGIME"


def test_obv_signal_is_wma9_not_simple_cross_only():
    close = np.linspace(100.0, 130.0, 40)
    ind = build_indicator_frame(_frame(close))
    obv = ind["OBV"]
    weights = np.arange(1, 10, dtype=float)
    expected = np.dot(obv.tail(9).to_numpy(), weights) / weights.sum()
    assert np.isclose(ind["OBV9"].iloc[-1], expected)
