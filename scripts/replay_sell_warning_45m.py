"""Historical replay harness for ``SellWarning45mEngine``.

Research-only: this module is not imported by production schedulers, renderers,
or notifiers.  It never persists trading state and never places orders.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis.bollinger_atr_strategy import (  # noqa: E402
    SOURCE_BAR_START_MINUTES,
    prepare_completed_45m_data,
)
from src.analysis.sell_warning_45m_engine import SellWarning45mEngine  # noqa: E402


KST = ZoneInfo("Asia/Seoul")
DEFAULT_TARGETS = (
    ("004960", "한신공영"),
    ("267260", "HD현대일렉트릭"),
    ("086450", "동국제약"),
)
REPLAY_END_TIMES = ("09:45", "10:30", "11:15", "12:00", "12:45", "13:30", "14:15", "15:00")
FALSE_WARNING_MFE_PCT = 2.0
FALSE_NEGATIVE_MAE_PCT = -2.0


@dataclass(frozen=True)
class FetchResult:
    stock_code: str
    stock_name: str
    frame: Optional[pd.DataFrame]
    source: str
    ticker: Optional[str]
    status: str
    error: Optional[str] = None


class ReplayAnalyzer:
    """Static data adapter matching the production analyzer fetch contract."""

    def __init__(self, frame: pd.DataFrame, source: str):
        self.frame = frame
        self.source = source

    def fetch_canonical_15m_data(self, stock_code: str):
        return self.frame, self.source, "NONE"


def _kst_naive_index(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    index = pd.DatetimeIndex(pd.to_datetime(result.index, errors="raise"))
    if index.tz is not None:
        index = index.tz_convert(KST).tz_localize(None)
    result.index = index
    return result.sort_index()


def _source_interval_minutes(source: str) -> Optional[int]:
    upper = str(source or "").upper()
    for prefix, minutes in SOURCE_BAR_START_MINUTES.items():
        if upper.startswith(prefix):
            return minutes
    return None


def fetch_yfinance_15m(stock_code: str, stock_name: str, period: str = "60d") -> FetchResult:
    """Fetch real 15m data, trying KOSPI then KOSDAQ without synthetic fallback."""
    code = str(stock_code).zfill(6)
    errors: List[str] = []
    for suffix in ("KS", "KQ"):
        symbol = f"{code}.{suffix}"
        try:
            frame = yf.Ticker(symbol).history(period=period, interval="15m", auto_adjust=False)
            if frame is None or frame.empty:
                errors.append(f"{symbol}:EMPTY")
                continue
            required = ["Open", "High", "Low", "Close", "Volume"]
            if any(column not in frame.columns for column in required):
                errors.append(f"{symbol}:MALFORMED_COLUMNS")
                continue
            frame = frame.loc[:, required].dropna().copy()
            if len(frame) < 26 * 3:
                errors.append(f"{symbol}:INSUFFICIENT_ROWS_{len(frame)}")
                continue
            frame = _kst_naive_index(frame)
            return FetchResult(
                stock_code=code,
                stock_name=stock_name,
                frame=frame,
                source=f"YFINANCE_15M ({symbol})",
                ticker=symbol,
                status="AVAILABLE",
            )
        except Exception as exc:  # pragma: no cover - network/provider dependent
            errors.append(f"{symbol}:{type(exc).__name__}:{exc}")
    return FetchResult(
        stock_code=code,
        stock_name=stock_name,
        frame=None,
        source="NONE",
        ticker=None,
        status="DATA_UNAVAILABLE",
        error=" | ".join(errors) or "NO_INTRADAY_DATA",
    )


def completed_cutoffs(raw_frame: pd.DataFrame, source: str) -> List[pd.Timestamp]:
    """Return only cutoffs whose full source rows exist for the 45m slot."""
    interval = _source_interval_minutes(source)
    if interval is None or 45 % interval != 0:
        return []
    frame = _kst_naive_index(raw_frame)
    available = set(frame.index)
    cutoffs: List[pd.Timestamp] = []
    for day in sorted(set(frame.index.normalize())):
        for end_text in REPLAY_END_TIMES:
            hour, minute = (int(part) for part in end_text.split(":"))
            cutoff = pd.Timestamp(day) + pd.Timedelta(hours=hour, minutes=minute)
            expected = pd.date_range(
                cutoff - pd.Timedelta(minutes=45),
                periods=45 // interval,
                freq=f"{interval}min",
            )
            if all(stamp in available for stamp in expected):
                cutoffs.append(cutoff)
    return cutoffs


def _variant_state(row: Dict[str, Any], strict_obv: bool) -> Tuple[str, Optional[int], Optional[bool]]:
    """Replay-only A/B classification; never mutates the production engine."""
    if row.get("state") == "UNKNOWN" or not str(row.get("data_quality", "")).startswith("VALID"):
        return "UNKNOWN", None, None
    obv_weak = bool(row.get("obv_weakness"))
    if strict_obv:
        try:
            obv_weak = (
                float(row["obv"]) < float(row["obv_wma9"])
                and (
                    float(row["obv_gap_delta"]) < 0
                    or str(row.get("obv_gap_state", "")).upper() == "CONTRACTING"
                )
            )
        except (TypeError, ValueError):
            return "UNKNOWN", None, None
    price = bool(row.get("price_weakness"))
    chaikin = bool(row.get("chaikin_weakness"))
    bear = bool(row.get("bear_trend"))
    axes_count = sum((price, obv_weak, chaikin, bear))
    conflict = bool(row.get("chaikin_recovery_conflict")) or bool(row.get("bull_trend_conflict"))
    warning = (
        price
        and axes_count >= 3
        and (obv_weak or chaikin)
        and (bear or (obv_weak and chaikin))
        and not conflict
    )
    state = "SELL_WARNING" if warning else ("CAUTION" if axes_count >= 2 else "NORMAL")
    return state, axes_count, obv_weak


def _event_row(result: Dict[str, Any], stock_name: str, source: str) -> Dict[str, Any]:
    row = {
        "trade_date": result.get("trading_date"),
        "bar_timestamp": result.get("bar_timestamp"),
        "stock_code": result.get("stock_code"),
        "stock_name": stock_name,
        "source": source,
        "state": result.get("sell_warning_state", "UNKNOWN"),
        "bearish_axes_count": result.get("bearish_axes_count"),
        "price_weakness": result.get("price_weakness"),
        "obv_weakness": result.get("obv_weakness"),
        "chaikin_weakness": result.get("chaikin_weakness"),
        "bear_trend": result.get("bear_trend"),
        "chaikin_recovery_conflict": result.get("chaikin_recovery_conflict"),
        "bull_trend_conflict": result.get("bull_trend_conflict"),
        "close": result.get("close_45m"),
        "vwap9": result.get("vwap9"),
        "vwap26": result.get("vwap26"),
        "obv": result.get("obv"),
        "obv_wma9": result.get("obv_wma9"),
        "obv_gap": result.get("obv_gap"),
        "obv_gap_delta": result.get("obv_gap_delta"),
        "obv_gap_state": result.get("obv_gap_state"),
        "chaikin_value": result.get("chaikin_value"),
        "chaikin_delta": result.get("chaikin_delta"),
        "chaikin_state": result.get("chaikin_state"),
        "adx": result.get("adx_14_45m"),
        "plus_di": result.get("plus_di_45m"),
        "minus_di": result.get("minus_di_45m"),
        "cloud_bottom": result.get("cloud_bottom_45m"),
        "data_quality": result.get("data_quality"),
        "reason_codes": result.get("reason_codes"),
    }
    state_b, axes_b, obv_b = _variant_state(row, strict_obv=True)
    row["state_obv_strict"] = state_b
    row["bearish_axes_count_obv_strict"] = axes_b
    row["obv_weakness_strict"] = None if obv_b is None else int(obv_b)
    return row


def replay_stock(
    stock_code: str,
    stock_name: str,
    raw_frame: pd.DataFrame,
    source: str,
    lookback_trading_days: int = 5,
) -> pd.DataFrame:
    """Replay the unchanged engine at every fully completed 45m cutoff."""
    frame = _kst_naive_index(raw_frame)
    rows: List[Dict[str, Any]] = []
    for cutoff in completed_cutoffs(frame, source):
        # Production's canonical yfinance collector requests period=5d.  Give
        # each replay point the same rolling trading-day window and remove all
        # rows at/after the cutoff before the unchanged engine sees them.
        point_in_time = frame.loc[frame.index < cutoff].copy()
        available_days = sorted(set(point_in_time.index.normalize()))
        if lookback_trading_days > 0 and len(available_days) > lookback_trading_days:
            first_day = available_days[-lookback_trading_days]
            point_in_time = point_in_time.loc[point_in_time.index.normalize() >= first_day]
        analyzer = ReplayAnalyzer(point_in_time, source)
        engine = SellWarning45mEngine(intraday_analyzer=analyzer)
        cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        result = engine.evaluate_stock_warning(
            stock_code=stock_code,
            trading_date=cutoff.strftime("%Y-%m-%d"),
            bar_timestamp=cutoff_text,
        )
        rows.append(_event_row(result, stock_name, source))
    events = pd.DataFrame(rows)
    if events.empty:
        return events
    return attach_forward_outcomes(events, frame, source)


def _pct(value: float, base: float) -> float:
    return (float(value) / float(base) - 1.0) * 100.0


def realized_45m_bars(raw_frame: pd.DataFrame, source: str) -> pd.DataFrame:
    """Aggregate realized bars for outcome measurement, including UNKNOWN signals."""
    raw = _kst_naive_index(raw_frame)
    interval = _source_interval_minutes(source)
    if interval is None:
        return pd.DataFrame()
    rows = []
    for cutoff in completed_cutoffs(raw, source):
        expected = pd.date_range(
            cutoff - pd.Timedelta(minutes=45),
            periods=45 // interval,
            freq=f"{interval}min",
        )
        group = raw.loc[expected]
        rows.append({
            "bar_timestamp": cutoff,
            "close": float(group["Close"].iloc[-1]),
            "high": float(group["High"].max()),
            "low": float(group["Low"].min()),
        })
    return pd.DataFrame(rows).set_index("bar_timestamp") if rows else pd.DataFrame()


def attach_forward_outcomes(
    events: pd.DataFrame,
    raw_frame: pd.DataFrame,
    source: str = "TEST_15M_BAR_START",
) -> pd.DataFrame:
    """Attach future-only outcomes; event rows themselves remain point-in-time."""
    result = events.copy().sort_values("bar_timestamp").reset_index(drop=True)
    raw = _kst_naive_index(raw_frame)
    realized = realized_45m_bars(raw, source)
    realized_positions = {stamp: index for index, stamp in enumerate(realized.index)}
    raw_dates = sorted(set(raw.index.normalize()))
    date_position = {pd.Timestamp(day): index for index, day in enumerate(raw_dates)}

    for column in (
        "return_next_45m_pct", "return_next_90m_pct", "return_day_close_pct",
        "return_next_open_pct", "return_next_close_pct", "mae_1d_pct", "mfe_1d_pct",
    ):
        result[column] = np.nan

    for row_index, row in result.iterrows():
        if pd.isna(row.get("close")):
            continue
        row = result.loc[row_index]
        base = float(row["close"])
        timestamp = pd.Timestamp(row["bar_timestamp"])
        trading_day = timestamp.normalize()
        realized_index = realized_positions.get(timestamp)
        if realized_index is not None and realized_index + 1 < len(realized):
            result.at[row_index, "return_next_45m_pct"] = _pct(realized.iloc[realized_index + 1]["close"], base)
        if realized_index is not None and realized_index + 2 < len(realized):
            result.at[row_index, "return_next_90m_pct"] = _pct(realized.iloc[realized_index + 2]["close"], base)

        day_rows = raw.loc[raw.index.normalize() == trading_day]
        if not day_rows.empty:
            result.at[row_index, "return_day_close_pct"] = _pct(day_rows["Close"].iloc[-1], base)

        day_index = date_position.get(trading_day)
        next_day = raw_dates[day_index + 1] if day_index is not None and day_index + 1 < len(raw_dates) else None
        if next_day is not None:
            next_rows = raw.loc[raw.index.normalize() == next_day]
            result.at[row_index, "return_next_open_pct"] = _pct(next_rows["Open"].iloc[0], base)
            result.at[row_index, "return_next_close_pct"] = _pct(next_rows["Close"].iloc[-1], base)
            forward = raw.loc[(raw.index >= timestamp) & (raw.index.normalize() <= next_day)]
            if not forward.empty:
                result.at[row_index, "mae_1d_pct"] = _pct(forward["Low"].min(), base)
                result.at[row_index, "mfe_1d_pct"] = _pct(forward["High"].max(), base)
    return result


def build_episodes(events: pd.DataFrame, state_column: str = "state", variant: str = "CURRENT") -> pd.DataFrame:
    """Collapse consecutive identical states so persistent warnings count once."""
    if events.empty:
        return pd.DataFrame()
    rows: List[Dict[str, Any]] = []
    for (stock_code, stock_name), group in events.groupby(["stock_code", "stock_name"], sort=False):
        group = group.sort_values("bar_timestamp").reset_index(drop=True)
        episode_id = (group[state_column] != group[state_column].shift()).cumsum()
        grouped = list(group.groupby(episode_id, sort=True))
        for position, (_, episode) in enumerate(grouped):
            first = episode.iloc[0]
            last = episode.iloc[-1]
            next_state = grouped[position + 1][1].iloc[0][state_column] if position + 1 < len(grouped) else None
            start_close = first.get("close")
            end_close = last.get("close")
            episode_return = (
                _pct(end_close, start_close)
                if pd.notna(start_close) and pd.notna(end_close) and float(start_close) != 0
                else np.nan
            )
            rows.append({
                "variant": variant,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "state": first[state_column],
                "start_timestamp": first["bar_timestamp"],
                "end_timestamp": last["bar_timestamp"],
                "duration_bars": len(episode),
                "next_state": next_state,
                "start_close": start_close,
                "end_close": end_close,
                "episode_return_pct": episode_return,
                "return_next_45m_pct": first.get("return_next_45m_pct"),
                "return_next_90m_pct": first.get("return_next_90m_pct"),
                "return_day_close_pct": first.get("return_day_close_pct"),
                "return_next_open_pct": first.get("return_next_open_pct"),
                "return_next_close_pct": first.get("return_next_close_pct"),
                "mae_1d_pct": first.get("mae_1d_pct"),
                "mfe_1d_pct": first.get("mfe_1d_pct"),
            })
    return pd.DataFrame(rows)


def build_transitions(episodes: pd.DataFrame) -> pd.DataFrame:
    if episodes.empty:
        return pd.DataFrame()
    rows: List[Dict[str, Any]] = []
    for (variant, stock_code), group in episodes.groupby(["variant", "stock_code"], sort=False):
        group = group.sort_values("start_timestamp").reset_index(drop=True)
        for index in range(1, len(group)):
            previous = group.iloc[index - 1]
            current = group.iloc[index]
            rows.append({
                "variant": variant,
                "stock_code": stock_code,
                "stock_name": current["stock_name"],
                "from_state": previous["state"],
                "to_state": current["state"],
                "transition_timestamp": current["start_timestamp"],
                "prior_duration_bars": previous["duration_bars"],
                "prior_start_timestamp": previous["start_timestamp"],
                "prior_end_timestamp": previous["end_timestamp"],
                "prior_episode_return_pct": previous["episode_return_pct"],
                "post_45m_return_pct": current["return_next_45m_pct"],
            })
    return pd.DataFrame(rows)


def _ratio(series: pd.Series, predicate) -> float:
    valid = pd.to_numeric(series, errors="coerce").dropna()
    if valid.empty:
        return math.nan
    return float(predicate(valid).mean() * 100.0)


def _mean(series: pd.Series) -> float:
    valid = pd.to_numeric(series, errors="coerce").dropna()
    return float(valid.mean()) if not valid.empty else math.nan


def _category_ratio(series: pd.Series, expected: str) -> float:
    valid = series.dropna().astype(str)
    if valid.empty:
        return math.nan
    return float((valid == expected).mean() * 100.0)


def summarize(events: pd.DataFrame, episodes: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    variants = (("CURRENT", "state"), ("OBV_STRICT", "state_obv_strict"))
    stocks = list(events[["stock_code", "stock_name"]].drop_duplicates().itertuples(index=False, name=None))
    scopes: List[Tuple[str, str, pd.DataFrame]] = [
        (code, name, events.loc[events["stock_code"] == code]) for code, name in stocks
    ]
    scopes.append(("ALL", "전체", events))
    for variant, state_column in variants:
        variant_episodes = episodes.loc[episodes["variant"] == variant]
        for code, name, scope_events in scopes:
            scope_eps = variant_episodes if code == "ALL" else variant_episodes.loc[variant_episodes["stock_code"] == code]
            sell = scope_eps.loc[scope_eps["state"] == "SELL_WARNING"]
            caution = scope_eps.loc[scope_eps["state"] == "CAUTION"]
            normal = scope_eps.loc[scope_eps["state"] == "NORMAL"]
            valid_events = scope_events.loc[scope_events[state_column] != "UNKNOWN"]
            rows.append({
                "variant": variant,
                "stock_code": code,
                "stock_name": name,
                "data_start": scope_events["bar_timestamp"].min(),
                "data_end": scope_events["bar_timestamp"].max(),
                "trading_days": scope_events["trade_date"].nunique(),
                "completed_45m_events": len(scope_events),
                "valid_events": len(valid_events),
                "unknown_events": int((scope_events[state_column] == "UNKNOWN").sum()),
                "sell_warning_events": int((scope_events[state_column] == "SELL_WARNING").sum()),
                "caution_events": int((scope_events[state_column] == "CAUTION").sum()),
                "normal_events": int((scope_events[state_column] == "NORMAL").sum()),
                "sell_warning_episodes": len(sell),
                "caution_episodes": len(caution),
                "normal_episodes": len(normal),
                "sell_next_45m_negative_pct": _ratio(sell["return_next_45m_pct"], lambda s: s < 0),
                "sell_day_close_negative_pct": _ratio(sell["return_day_close_pct"], lambda s: s < 0),
                "sell_next_close_negative_pct": _ratio(sell["return_next_close_pct"], lambda s: s < 0),
                "sell_avg_mae_1d_pct": _mean(sell["mae_1d_pct"]),
                "sell_avg_mfe_1d_pct": _mean(sell["mfe_1d_pct"]),
                "sell_false_warning_mfe_ge_2pct": _ratio(sell["mfe_1d_pct"], lambda s: s >= FALSE_WARNING_MFE_PCT),
                "sell_false_warning_episode_count": int((pd.to_numeric(sell["mfe_1d_pct"], errors="coerce") >= FALSE_WARNING_MFE_PCT).sum()),
                "caution_to_sell_pct": _category_ratio(caution["next_state"], "SELL_WARNING"),
                "caution_to_normal_pct": _category_ratio(caution["next_state"], "NORMAL"),
                "caution_avg_duration_bars": _mean(caution["duration_bars"]),
                "normal_false_negative_candidates": int((pd.to_numeric(normal["mae_1d_pct"], errors="coerce") <= FALSE_NEGATIVE_MAE_PCT).sum()),
                "non_sell_missed_decline_candidates": int((
                    (scope_eps["state"] != "SELL_WARNING")
                    & (pd.to_numeric(scope_eps["mae_1d_pct"], errors="coerce") <= FALSE_NEGATIVE_MAE_PCT)
                ).sum()),
            })
    return pd.DataFrame(rows)


def _load_extended_targets(limit: int) -> List[Tuple[str, str]]:
    if limit <= 0:
        return []
    holdings_path = ROOT / "config" / "portfolio_holdings.json"
    try:
        holdings = json.loads(holdings_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    existing = {code for code, _ in DEFAULT_TARGETS}
    result = []
    for item in holdings:
        code = str(item.get("stock_code", "")).zfill(6)
        name = str(item.get("stock_name", code))
        if code in existing:
            continue
        result.append((code, name))
        if len(result) >= limit:
            break
    return result


def run_replay(
    targets: Sequence[Tuple[str, str]],
    output_dir: Path,
    period: str = "60d",
) -> Dict[str, pd.DataFrame]:
    output_dir.mkdir(parents=True, exist_ok=True)
    event_frames: List[pd.DataFrame] = []
    availability_rows: List[Dict[str, Any]] = []
    for code, name in targets:
        fetched = fetch_yfinance_15m(code, name, period=period)
        availability_rows.append({
            "stock_code": code,
            "stock_name": name,
            "status": fetched.status,
            "source": fetched.source,
            "ticker": fetched.ticker,
            "raw_rows": 0 if fetched.frame is None else len(fetched.frame),
            "raw_start": None if fetched.frame is None else str(fetched.frame.index.min()),
            "raw_end": None if fetched.frame is None else str(fetched.frame.index.max()),
            "error": fetched.error,
        })
        if fetched.frame is None:
            continue
        events = replay_stock(code, name, fetched.frame, fetched.source)
        if not events.empty:
            event_frames.append(events)

    events = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    if not events.empty:
        events = events.sort_values(["stock_code", "bar_timestamp"]).reset_index(drop=True)
        episodes_current = build_episodes(events, "state", "CURRENT")
        episodes_strict = build_episodes(events, "state_obv_strict", "OBV_STRICT")
        episodes = pd.concat([episodes_current, episodes_strict], ignore_index=True)
        transitions = build_transitions(episodes)
        summary = summarize(events, episodes)
    else:
        episodes = pd.DataFrame()
        transitions = pd.DataFrame()
        summary = pd.DataFrame()
    availability = pd.DataFrame(availability_rows)

    outputs = {
        "events": events,
        "episodes": episodes,
        "transitions": transitions,
        "summary": summary,
        "availability": availability,
    }
    for name, frame in outputs.items():
        frame.to_csv(output_dir / f"sell_warning_45m_replay_{name}.csv", index=False, encoding="utf-8-sig")
    metadata = {
        "research_only": True,
        "engine_modified": False,
        "threshold_modified": False,
        "period": period,
        "targets": [{"stock_code": code, "stock_name": name} for code, name in targets],
        "false_warning_definition": f"SELL_WARNING episode MFE >= {FALSE_WARNING_MFE_PCT}%",
        "false_negative_definition": f"NORMAL episode MAE <= {FALSE_NEGATIVE_MAE_PCT}%",
        "forward_window": "after signal cutoff through end of next available trading day",
        "generated_at_kst": pd.Timestamp.now(tz=KST).isoformat(),
    }
    (output_dir / "sell_warning_45m_replay_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return outputs


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Historical replay for SellWarning45mEngine")
    parser.add_argument("--period", default="60d", help="yfinance intraday period (default: 60d)")
    parser.add_argument("--extend-holdings", type=int, default=0, help="append N real holdings from config")
    parser.add_argument("--output-dir", default=str(ROOT / "artifacts"), help="CSV/JSON output directory")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    targets = list(DEFAULT_TARGETS) + _load_extended_targets(args.extend_holdings)
    outputs = run_replay(targets, Path(args.output_dir), period=args.period)
    available = int((outputs["availability"]["status"] == "AVAILABLE").sum()) if not outputs["availability"].empty else 0
    print(f"Replay complete: {available}/{len(targets)} stocks available")
    print(f"Events: {len(outputs['events'])}, Episodes: {len(outputs['episodes'])}")
    print(f"Output: {Path(args.output_dir).resolve()}")
    return 0 if available else 2


if __name__ == "__main__":
    raise SystemExit(main())
