from datetime import datetime,timedelta
from typing import Optional,Tuple
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from src.runtime.krx_calendar import KRXCalendar
KST=ZoneInfo("Asia/Seoul")
def _as_kst_naive(value):
    t=pd.Timestamp(value)
    return t.tz_convert(KST).tz_localize(None) if t.tzinfo else t
def _source_interval_minutes(source):
    text=str(source).upper()
    for prefix,n in [("KIWOOM_15M_BAR_START",15),("YFINANCE_15M",15),("NAVER_MINUTE_API",1),("TEST_15M_BAR_START",15),("TEST_1M_BAR_START",1)]:
        if text.startswith(prefix):return n
    return None
def prepare_completed_30m_data(raw_df: pd.DataFrame, source: str, cutoff_dt: datetime, *, allow_sparse_completed: bool = False) -> Tuple[Optional[pd.DataFrame], str]:
    """Aggregate validated start-labelled rows into end-labelled 30m bars."""
    if not isinstance(raw_df, pd.DataFrame) or raw_df.empty:
        return None, "REQUIRED_SOURCE_DATA_MISSING"
    if str(source).upper().startswith("YFINANCE"):
        watermark=raw_df.attrs.get("source_watermark")
        if not watermark:return None,"SOURCE_WATERMARK_UNAVAILABLE"
        if _as_kst_naive(watermark)<_as_kst_naive(cutoff_dt):return None,"SOURCE_NOT_COMPLETE"
    if str(source).startswith("KIWOOM_15M_BAR_START"):
        watermark=raw_df.attrs.get("source_watermark")
        if not watermark or _as_kst_naive(watermark)<_as_kst_naive(cutoff_dt):return None,"SOURCE_NOT_COMPLETE"
    interval_minutes = _source_interval_minutes(source)
    if interval_minutes is None or 30 % interval_minutes != 0:
        return None, "UNSUPPORTED_TIMESTAMP_SEMANTICS"
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in raw_df.columns for column in required):
        return None, "MALFORMED_OHLCV"

    frame = raw_df.loc[:, required].copy()
    try:
        original_index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="raise"))
    except Exception:
        return None, "INVALID_TIMESTAMP"
    if original_index.has_duplicates:
        return None, "DUPLICATE_TIMESTAMP"
    if not original_index.is_monotonic_increasing:
        return None, "NON_MONOTONIC_TIMESTAMP"
    try:
        normalized_index = original_index.tz_convert(KST).tz_localize(None) if original_index.tz is not None else original_index
    except Exception:
        return None, "INVALID_TIMESTAMP"
    if normalized_index.has_duplicates or not normalized_index.is_monotonic_increasing:
        return None, "INVALID_TIMESTAMP"
    frame.index = normalized_index

    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    numeric = frame[required].to_numpy(dtype=float)
    if np.isnan(numeric).any() or not np.isfinite(numeric).all():
        return None, "MALFORMED_OHLCV"
    if (frame[["Open", "High", "Low", "Close"]] <= 0).any().any() or (frame["Volume"] < 0).any():
        return None, "MALFORMED_OHLCV"
    if (frame["High"] < frame[["Open", "Close", "Low"]].max(axis=1)).any() or (frame["Low"] > frame[["Open", "Close", "High"]].min(axis=1)).any():
        return None, "MALFORMED_OHLCV"

    cutoff = _as_kst_naive(cutoff_dt)
    duration = pd.Timedelta(minutes=interval_minutes)
    day = frame.index.normalize()
    day_open = day + pd.Timedelta(hours=9)
    day_last_completed = day + pd.Timedelta(hours=15, minutes=30)
    per_row_cutoff = pd.DatetimeIndex([min(pd.Timestamp(limit), cutoff) for limit in day_last_completed])
    eligible = frame.loc[(frame.index >= day_open) & (frame.index + duration <= per_row_cutoff)].copy()
    if eligible.empty:
        return None, "CUTOFF_MISMATCH"

    rows_per_candle = 30 // interval_minutes
    completed_rows = []
    for trading_day, daily in eligible.groupby(eligible.index.normalize(), sort=True):
        if not KRXCalendar.is_krx_trading_day(trading_day.date()):
            continue
        expected_open = pd.Timestamp(trading_day) + pd.Timedelta(hours=9)
        offsets = (daily.index - expected_open).total_seconds() / 60.0
        if any(offset < 0 or offset >= 390 or offset % interval_minutes != 0 for offset in offsets):
            return None, "MALFORMED_INTERVAL"
        if len(daily) > 1 and not (daily.index.to_series().diff().dropna() == duration).all():
            if not allow_sparse_completed:
                return None, "MALFORMED_INTERVAL"
            # Individual complete 30m bins remain valid when a different bin
            # has a missing 15m interval. Never invent bars to fill the gap.
        daily = daily.copy()
        daily["__slot"] = (offsets // 30).astype(int)
        for slot, group in daily.groupby("__slot", sort=True):
            bin_start = expected_open + pd.Timedelta(minutes=int(slot) * 30)
            bin_end = bin_start + pd.Timedelta(minutes=30)
            allowed_end = min(pd.Timestamp(trading_day) + pd.Timedelta(hours=15, minutes=30), cutoff)
            if bin_end.time().isoformat() == "15:30:00":
                continue  # FINAL_AUCTION_OHLCV_UNVERIFIED; exposed separately as PARTIAL.
            if bin_end > allowed_end:
                continue
            expected_index = pd.date_range(bin_start, periods=rows_per_candle, freq=f"{interval_minutes}min")
            if len(group) != rows_per_candle or not group.index.equals(expected_index):
                if allow_sparse_completed:
                    continue  # Exclude incomplete candle; never infer its OHLCV.
                return None, "PARTIAL_BAR_CONTAMINATION"
            completed_rows.append((
                bin_end, float(group["Open"].iloc[0]), float(group["High"].max()),
                float(group["Low"].min()), float(group["Close"].iloc[-1]),
                float(group["Volume"].sum()),
            ))

    if not completed_rows:
        return None, "INSUFFICIENT_COMPLETED_30M_BARS"
    completed = pd.DataFrame(
        completed_rows,
        columns=["completed_30m_timestamp", "Open", "High", "Low", "Close", "Volume"],
    ).set_index("completed_30m_timestamp")
    if completed.index.has_duplicates or not completed.index.is_monotonic_increasing:
        return None, "INVALID_COMPLETED_30M_INDEX"
    if completed.index[-1] != cutoff:
        return None, "CUTOFF_MISMATCH"
    if len(completed) < 1:
        return None, "INSUFFICIENT_COMPLETED_30M_BARS"
    return completed, "VALID"


def completed_frame(raw,source,asof=None):
    asof=asof or datetime.now(KST)
    if asof.tzinfo:asof=asof.astimezone(KST)
    day=asof.date()
    slot=KRXCalendar.get_completed_30m_bar(asof)
    if slot:
        end=min(slot[2],"15:00")
    else:
        day-=timedelta(days=1)
        for _ in range(20):
            if KRXCalendar.is_krx_trading_day(day):break
            day-=timedelta(days=1)
        end="15:00"
    cutoff=datetime.fromisoformat(day.isoformat()+"T"+end+":00+09:00")
    return prepare_completed_30m_data(raw,source,cutoff,allow_sparse_completed=True)
