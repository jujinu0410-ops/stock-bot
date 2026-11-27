"""Completed-bar Bollinger context for E30 observation."""

import pandas as pd

from src.analysis.bollinger_atr_30m import BB_WINDOW, bb26_2_bands
from src.analysis.m30_indicators import macd_series, obv_series, vwap_series
from src.runtime.krx_calendar import COMPLETED_30M_BAR_SCHEDULE, KRXCalendar

LOOKBACK = 90
COMPRESSION_QUANTILE = 0.20
PIVOT_BARS = 4
PREPARATION_BARS = 8
SLOT_NUMBERS = {slot[3]: number for number, slot in enumerate(COMPLETED_30M_BAR_SCHEDULE)}


def _consecutive_runs(index):
    days = pd.date_range(index[0].normalize(), index[-1].normalize())
    trading_days = [day for day in days if KRXCalendar.is_krx_trading_day(day.date())]
    day_numbers = {day: number for number, day in enumerate(trading_days)}
    bar_numbers = pd.Series(
        [day_numbers[stamp.normalize()] * len(SLOT_NUMBERS) + SLOT_NUMBERS[stamp.strftime("%H:%M")]
         for stamp in index],
        index=index,
    )
    same_run = bar_numbers.diff().eq(1)
    return bar_numbers, same_run, (~same_run).cumsum()


def bollinger_assist_history(frame, *, quantile=COMPRESSION_QUANTILE):
    """Evaluate each provided completed bar using information available at its close."""
    close, volume = frame.Close, frame.Volume
    lower, middle, upper = bb26_2_bands(close)
    span = (upper - lower).where(lambda width: width > middle * 1e-12)
    percent_b = (close - lower) / span * 100
    bandwidth = span / middle.where(middle > 0) * 100
    ceiling = bandwidth.shift(1).rolling(LOOKBACK).quantile(quantile)
    compressed = bandwidth.le(ceiling)

    bar_numbers, same_run, run = _consecutive_runs(frame.index)
    lower_pivot = (
        percent_b.shift(1).le(30) & percent_b.shift(1).lt(percent_b.shift(2))
        & percent_b.gt(percent_b.shift(1)) & same_run & same_run.shift(1, fill_value=False)
        & compressed.shift(1, fill_value=False)
    )
    pivot_bar = bar_numbers.where(lower_pivot).groupby(run).ffill()
    near_pivot = (bar_numbers - pivot_bar).lt(PIVOT_BARS)
    obv, obv_signal = obv_series(close, volume)
    obv_gap = obv - obv_signal
    obv_recovers = (obv_gap.gt(0) & obv_gap.shift(1).le(0)) | (
        obv.diff(2).div(2).gt(volume.rolling(3).mean() * 0.05)
    )
    macd, macd_signal = macd_series(close)
    pivot_rising = percent_b.gt(percent_b.shift(1))
    can_prepare = compressed & near_pivot & pivot_rising & obv_recovers & macd.gt(macd_signal)
    preparing = can_prepare & can_prepare.groupby(pivot_bar).cumsum().eq(1)
    prepared_bar = bar_numbers.where(preparing).groupby(run).ffill().groupby(run).shift(1)
    recently_prepared = (bar_numbers - prepared_bar).le(PREPARATION_BARS)

    prior_volume = volume.shift(1).rolling(20).median()
    volume_ratio = volume / prior_volume.where(prior_volume > 0)
    vwap9 = vwap_series(frame, 9)
    watching = (
        recently_prepared & bandwidth.gt(ceiling) & bandwidth.gt(bandwidth.shift(1))
        & close.gt(close.shift(1)) & close.gt(vwap9) & volume_ratio.ge(1)
    )
    first_watch = watching & watching.groupby(prepared_bar).cumsum().eq(1)
    stages = pd.Series("NONE", index=frame.index)
    stages.loc[compressed & percent_b.le(50)] = "COMPRESSION"
    stages.loc[preparing] = "BUY_PREPARATION"
    stages.loc[first_watch] = "TRAILING_WATCH"
    return pd.DataFrame({"stage": stages, "percent_b": percent_b, "bandwidth_pct": bandwidth,
                         "compression_ceiling_pct": ceiling, "volume_ratio": volume_ratio})


def bollinger_assist(frame, expected_end):
    """Return a compact report for the exact completed KRX bar requested."""
    if frame is None or len(frame) < BB_WINDOW + LOOKBACK or frame.index[-1] != expected_end:
        return None
    latest = bollinger_assist_history(frame).iloc[-1]
    required = ("percent_b", "bandwidth_pct", "compression_ceiling_pct")
    if latest[list(required)].isna().any():
        return None
    report = {key: float(latest[key]) for key in required}
    report.update(stage=latest.stage, completed_bar=expected_end.isoformat(timespec="minutes"))
    if pd.notna(latest.volume_ratio):
        report["volume_ratio"] = float(latest.volume_ratio)
    return report
