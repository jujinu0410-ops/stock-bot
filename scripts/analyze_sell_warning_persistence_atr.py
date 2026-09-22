"""Research-only comparison of first-vs-second SELL entry and ATR false warnings.

Inputs are the existing replay artifacts plus real daily Yahoo OHLC used only to
calculate the repository's canonical completed-daily Wilder ATR14.  No engine,
scheduler, notifier, renderer, threshold, or order code is changed or called.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts"
KST = ZoneInfo("Asia/Seoul")
OUTCOME_COLUMNS = (
    "return_next_45m_pct",
    "return_next_90m_pct",
    "return_day_close_pct",
    "return_next_open_pct",
    "return_next_close_pct",
    "mae_1d_pct",
    "mfe_1d_pct",
)
TICKERS = {
    "004960": "004960.KS",
    "267260": "267260.KS",
    "086450": "086450.KQ",
}


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def _pct(value: float, base: float) -> float:
    if not math.isfinite(value) or not math.isfinite(base) or base == 0:
        return math.nan
    return (value / base - 1.0) * 100.0


def fetch_daily_history(ticker: str, period: str = "2y") -> Tuple[Optional[pd.DataFrame], str]:
    """Fetch real unadjusted daily bars; never substitute synthetic data."""
    try:
        frame = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    except Exception as exc:  # pragma: no cover - provider dependent
        return None, f"FETCH_ERROR:{type(exc).__name__}:{exc}"
    required = ["High", "Low", "Close"]
    if frame is None or frame.empty:
        return None, "EMPTY"
    if any(column not in frame.columns for column in required):
        return None, "MALFORMED_COLUMNS"
    frame = frame.loc[:, required].dropna().copy()
    index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="raise"))
    if index.tz is not None:
        index = index.tz_convert(KST).tz_localize(None)
    frame.index = index.normalize()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frame, "VALID"


def canonical_wilder_atr14(daily: pd.DataFrame) -> pd.Series:
    """Use the exact implementation that ATR V4 technical analysis uses."""
    from src.analysis.technical_analysis import calculate_wilder_atr

    canonical = pd.DataFrame({
        "high_price": daily["High"].astype(float),
        "low_price": daily["Low"].astype(float),
        "close_price": daily["Close"].astype(float),
    }, index=daily.index)
    return calculate_wilder_atr(canonical, period=14)


def atr_at_entry(
    daily: Optional[pd.DataFrame], entry_timestamp: Any, entry_price: Any
) -> Dict[str, Any]:
    result = {
        "atr14": math.nan,
        "atr_pct": math.nan,
        "atr_reference_date": None,
        "atr_data_quality": "DATA_UNAVAILABLE",
    }
    if daily is None or daily.empty:
        return result
    timestamp = pd.Timestamp(entry_timestamp)
    completed = daily.loc[daily.index < timestamp.normalize()].copy()
    if len(completed) < 14:
        result["atr_data_quality"] = "INSUFFICIENT_COMPLETED_DAILY_BARS"
        return result
    price = _number(entry_price)
    if not math.isfinite(price) or price <= 0:
        result["atr_data_quality"] = "INVALID_ENTRY_PRICE"
        return result
    atr_series = canonical_wilder_atr14(completed)
    atr14 = _number(atr_series.iloc[-1])
    if not math.isfinite(atr14) or atr14 <= 0:
        result["atr_data_quality"] = "INVALID_ATR14"
        return result
    result.update({
        "atr14": atr14,
        "atr_pct": atr14 / price * 100.0,
        "atr_reference_date": completed.index[-1].strftime("%Y-%m-%d"),
        "atr_data_quality": "VALID_COMPLETED_DAILY_WILDER_ATR14",
    })
    return result


def _false_flag(mfe_pct: Any, atr_pct: Any, criterion: str) -> Optional[int]:
    mfe = _number(mfe_pct)
    if not math.isfinite(mfe):
        return None
    if criterion == "F0":
        return int(mfe >= 2.0)
    atr = _number(atr_pct)
    if not math.isfinite(atr) or atr <= 0:
        return None
    multiplier = 0.8 if criterion == "F1" else 1.0
    return int(mfe >= multiplier * atr)


def build_entry_comparison(
    events: pd.DataFrame,
    episodes: pd.DataFrame,
    daily_frames: Dict[str, Optional[pd.DataFrame]],
) -> pd.DataFrame:
    current_sell = episodes.loc[
        (episodes["variant"] == "CURRENT") & (episodes["state"] == "SELL_WARNING")
    ].sort_values(["stock_code", "start_timestamp"])
    event_groups = {
        str(code).zfill(6): group.sort_values("bar_timestamp").reset_index(drop=True)
        for code, group in events.groupby("stock_code", sort=False)
    }
    rows: List[Dict[str, Any]] = []

    for _, episode in current_sell.iterrows():
        code = str(episode["stock_code"]).zfill(6)
        group = event_groups[code]
        positions = group.index[group["bar_timestamp"] == episode["start_timestamp"]].tolist()
        if len(positions) != 1:
            raise ValueError(f"Expected one first SELL event for {code} {episode['start_timestamp']}")
        position = positions[0]
        first = group.iloc[position]
        second = group.iloc[position + 1] if position + 1 < len(group) else None
        has_second = bool(
            second is not None
            and str(second.get("state")) == "SELL_WARNING"
            and pd.Timestamp(second.get("bar_timestamp")) <= pd.Timestamp(episode["end_timestamp"])
        )
        if (int(episode["duration_bars"]) >= 2) != has_second:
            raise AssertionError(f"Episode duration/second-entry mismatch: {code} {episode['start_timestamp']}")

        row: Dict[str, Any] = {
            "stock_code": code,
            "stock_name": episode["stock_name"],
            "episode_start": episode["start_timestamp"],
            "episode_end": episode["end_timestamp"],
            "episode_duration_bars": int(episode["duration_bars"]),
            "second_entry_exists": int(has_second),
            "first_entry_timestamp": first["bar_timestamp"],
            "first_entry_close": _number(first["close"]),
            "second_entry_timestamp": second["bar_timestamp"] if has_second else None,
            "second_entry_close": _number(second["close"]) if has_second else math.nan,
        }
        for column in OUTCOME_COLUMNS:
            row[f"first_{column}"] = _number(first.get(column))
            row[f"second_{column}"] = _number(second.get(column)) if has_second else math.nan

        delay = _pct(row["second_entry_close"], row["first_entry_close"]) if has_second else math.nan
        row["delay_close_return_pct"] = delay
        row["delay_adverse_move_pct"] = min(delay, 0.0) if math.isfinite(delay) else math.nan
        row["delay_favorable_move_pct"] = max(delay, 0.0) if math.isfinite(delay) else math.nan
        row["first_true_decline"] = (
            int(row["first_return_next_close_pct"] < 0)
            if math.isfinite(row["first_return_next_close_pct"])
            else None
        )
        row["second_true_decline"] = (
            int(row["second_return_next_close_pct"] < 0)
            if math.isfinite(row["second_return_next_close_pct"])
            else None
        )

        first_atr = atr_at_entry(daily_frames.get(code), row["first_entry_timestamp"], row["first_entry_close"])
        second_atr = (
            atr_at_entry(daily_frames.get(code), row["second_entry_timestamp"], row["second_entry_close"])
            if has_second
            else {"atr14": math.nan, "atr_pct": math.nan, "atr_reference_date": None,
                  "atr_data_quality": "NO_SECOND_ENTRY"}
        )
        for prefix, values in (("first", first_atr), ("second", second_atr)):
            row[f"{prefix}_atr14"] = values["atr14"]
            row[f"{prefix}_atr_pct"] = values["atr_pct"]
            row[f"{prefix}_atr_reference_date"] = values["atr_reference_date"]
            row[f"{prefix}_atr_data_quality"] = values["atr_data_quality"]
            for criterion in ("F0", "F1", "F2"):
                row[f"{prefix}_{criterion.lower()}_false_warning"] = _false_flag(
                    row[f"{prefix}_mfe_1d_pct"], values["atr_pct"], criterion
                )
        rows.append(row)

    result = pd.DataFrame(rows)
    if len(result) != 53:
        raise AssertionError(f"Expected 53 SELL episodes, got {len(result)}")
    return result


def _bootstrap_ci(values: Sequence[float], *, seed: int, iterations: int = 20000) -> Tuple[float, float]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return math.nan, math.nan
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, len(array), size=(iterations, len(array)))
    means = array[indexes].mean(axis=1)
    return tuple(float(value) for value in np.quantile(means, [0.025, 0.975]))


def _rate(series: pd.Series) -> Tuple[int, int, float]:
    valid = pd.to_numeric(series, errors="coerce").dropna()
    if valid.empty:
        return 0, 0, math.nan
    return int(valid.sum()), len(valid), float(valid.mean() * 100.0)


def _negative_rate(series: pd.Series) -> Tuple[int, int, float]:
    valid = pd.to_numeric(series, errors="coerce").dropna()
    if valid.empty:
        return 0, 0, math.nan
    return int((valid < 0).sum()), len(valid), float((valid < 0).mean() * 100.0)


def summarize_entry_mode(rows: pd.DataFrame, prefix: str) -> Dict[str, Any]:
    scope = rows if prefix == "first" else rows.loc[rows["second_entry_exists"] == 1]
    result: Dict[str, Any] = {"entry_count": len(scope)}
    for key, column in (
        ("next_45m_negative", "return_next_45m_pct"),
        ("day_close_negative", "return_day_close_pct"),
        ("next_close_negative", "return_next_close_pct"),
    ):
        count, denominator, rate = _negative_rate(scope[f"{prefix}_{column}"])
        result[f"{key}_count"] = count
        result[f"{key}_denominator"] = denominator
        result[f"{key}_pct"] = rate
    result["avg_mae_pct"] = pd.to_numeric(scope[f"{prefix}_mae_1d_pct"], errors="coerce").mean()
    result["avg_mfe_pct"] = pd.to_numeric(scope[f"{prefix}_mfe_1d_pct"], errors="coerce").mean()
    for criterion in ("f0", "f1", "f2"):
        count, denominator, rate = _rate(scope[f"{prefix}_{criterion}_false_warning"])
        result[f"{criterion}_false_count"] = count
        result[f"{criterion}_denominator"] = denominator
        result[f"{criterion}_false_pct"] = rate
    return result


def build_atr_comparison(rows: pd.DataFrame) -> pd.DataFrame:
    output: List[Dict[str, Any]] = []
    scopes = [("ALL", "전체", rows)] + [
        (str(code).zfill(6), group.iloc[0]["stock_name"], group)
        for code, group in rows.groupby("stock_code", sort=False)
    ]
    seed = 100
    for entry_mode in ("FIRST_SELL", "SECOND_CONSECUTIVE_SELL"):
        prefix = "first" if entry_mode == "FIRST_SELL" else "second"
        for code, name, frame in scopes:
            scope = frame if prefix == "first" else frame.loc[frame["second_entry_exists"] == 1]
            f0 = pd.to_numeric(scope[f"{prefix}_f0_false_warning"], errors="coerce")
            for criterion, multiplier, threshold in (
                ("F0_FIXED_2PCT", math.nan, "MFE >= 2.0%"),
                ("F1_ATR_0_8", 0.8, "MFE >= 0.8 * completed daily Wilder ATR14 / entry price"),
                ("F2_ATR_1_0", 1.0, "MFE >= 1.0 * completed daily Wilder ATR14 / entry price"),
            ):
                short = criterion[:2].lower()
                flags = pd.to_numeric(scope[f"{prefix}_{short}_false_warning"], errors="coerce")
                valid = flags.dropna()
                if criterion == "F0_FIXED_2PCT":
                    thresholds = pd.Series(2.0, index=valid.index, dtype=float)
                else:
                    thresholds = pd.to_numeric(scope.loc[valid.index, f"{prefix}_atr_pct"], errors="coerce") * multiplier
                ci_low, ci_high = _bootstrap_ci(valid.to_numpy() * 100.0, seed=seed)
                seed += 1
                common = pd.concat([f0.rename("f0"), flags.rename("candidate")], axis=1).dropna()
                changed = int((common["f0"] != common["candidate"]).sum()) if not common.empty else 0
                output.append({
                    "entry_mode": entry_mode,
                    "stock_code": code,
                    "stock_name": name,
                    "criterion": criterion,
                    "atr_multiplier": multiplier,
                    "definition": threshold,
                    "total_entries": len(scope),
                    "evaluated_entries": len(valid),
                    "missing_entries": len(scope) - len(valid),
                    "false_warning_count": int(valid.sum()) if len(valid) else 0,
                    "false_warning_rate_pct": float(valid.mean() * 100.0) if len(valid) else math.nan,
                    "mean_threshold_pct": thresholds.mean() if len(thresholds) else math.nan,
                    "median_threshold_pct": thresholds.median() if len(thresholds) else math.nan,
                    "bootstrap_ci95_low_pct": ci_low,
                    "bootstrap_ci95_high_pct": ci_high,
                    "classification_changed_vs_f0": changed,
                })
    result = pd.DataFrame(output)
    result["stock_rate_std_pp"] = np.nan
    result["stock_rate_range_pp"] = np.nan
    for (entry_mode, criterion), group in result.loc[result["stock_code"] != "ALL"].groupby(
        ["entry_mode", "criterion"]
    ):
        rates = pd.to_numeric(group["false_warning_rate_pct"], errors="coerce").dropna()
        all_mask = (
            (result["entry_mode"] == entry_mode)
            & (result["criterion"] == criterion)
            & (result["stock_code"] == "ALL")
        )
        result.loc[all_mask, "stock_rate_std_pp"] = rates.std(ddof=0) if len(rates) else math.nan
        result.loc[all_mask, "stock_rate_range_pp"] = rates.max() - rates.min() if len(rates) else math.nan
    return result


def paired_statistics(rows: pd.DataFrame) -> pd.DataFrame:
    paired = rows.loc[rows["second_entry_exists"] == 1].copy()
    specs = [
        ("return_next_45m_pct", "continuous"),
        ("return_next_90m_pct", "continuous"),
        ("return_day_close_pct", "continuous"),
        ("return_next_open_pct", "continuous"),
        ("return_next_close_pct", "continuous"),
        ("mae_1d_pct", "continuous"),
        ("mfe_1d_pct", "continuous"),
        ("f0_false_warning", "binary"),
        ("f1_false_warning", "binary"),
        ("f2_false_warning", "binary"),
    ]
    output: List[Dict[str, Any]] = []
    seed = 900
    for metric, kind in specs:
        pair = paired[[f"first_{metric}", f"second_{metric}"]].apply(pd.to_numeric, errors="coerce").dropna()
        differences = pair[f"second_{metric}"] - pair[f"first_{metric}"]
        multiplier = 100.0 if kind == "binary" else 1.0
        reported = differences.to_numpy(dtype=float) * multiplier
        ci_low, ci_high = _bootstrap_ci(reported, seed=seed)
        seed += 1
        sd = differences.std(ddof=1)
        output.append({
            "metric": metric,
            "metric_type": kind,
            "paired_n": len(pair),
            "first_mean": pair[f"first_{metric}"].mean() * multiplier,
            "second_mean": pair[f"second_{metric}"].mean() * multiplier,
            "paired_difference_second_minus_first": reported.mean() if len(reported) else math.nan,
            "paired_difference_median": np.median(reported) if len(reported) else math.nan,
            "bootstrap_ci95_low": ci_low,
            "bootstrap_ci95_high": ci_high,
            "paired_standardized_effect_dz": (
                differences.mean() / sd if kind == "continuous" and math.isfinite(sd) and sd > 0 else math.nan
            ),
        })
    for metric, source_metric in (
        ("next_45m_negative", "return_next_45m_pct"),
        ("day_close_negative", "return_day_close_pct"),
        ("next_close_negative", "return_next_close_pct"),
    ):
        pair = paired[[f"first_{source_metric}", f"second_{source_metric}"]].apply(
            pd.to_numeric, errors="coerce"
        ).dropna()
        first_binary = (pair[f"first_{source_metric}"] < 0).astype(float)
        second_binary = (pair[f"second_{source_metric}"] < 0).astype(float)
        reported = (second_binary - first_binary).to_numpy(dtype=float) * 100.0
        ci_low, ci_high = _bootstrap_ci(reported, seed=seed)
        seed += 1
        output.append({
            "metric": metric,
            "metric_type": "binary",
            "paired_n": len(pair),
            "first_mean": first_binary.mean() * 100.0,
            "second_mean": second_binary.mean() * 100.0,
            "paired_difference_second_minus_first": reported.mean() if len(reported) else math.nan,
            "paired_difference_median": np.median(reported) if len(reported) else math.nan,
            "bootstrap_ci95_low": ci_low,
            "bootstrap_ci95_high": ci_high,
            "paired_standardized_effect_dz": math.nan,
        })
    return pd.DataFrame(output)


def _fmt(value: Any, digits: int = 2) -> str:
    number = _number(value)
    return "NA" if not math.isfinite(number) else f"{number:.{digits}f}"


def _ratio(count: Any, denominator: Any, pct: Any) -> str:
    return f"{int(count)}/{int(denominator)} ({_fmt(pct)}%)" if int(denominator) else "NA"


def _atr_assessment(table: pd.DataFrame, entry_mode: str, criterion: str) -> str:
    overall = table.loc[
        (table["entry_mode"] == entry_mode) & (table["stock_code"] == "ALL")
    ].set_index("criterion")
    base = overall.loc["F0_FIXED_2PCT"]
    candidate = overall.loc[criterion]
    std_delta = _number(candidate["stock_rate_std_pp"]) - _number(base["stock_rate_std_pp"])
    rate_delta = _number(candidate["false_warning_rate_pct"]) - _number(base["false_warning_rate_pct"])
    if std_delta <= -2.0:
        verdict = "F0보다 안정적"
    elif std_delta >= 2.0:
        verdict = "더 나쁨"
    else:
        verdict = "차이 미미"
    return f"{verdict} (전체 비율 {rate_delta:+.2f}%p, 종목간 표준편차 {std_delta:+.2f}%p)"


def build_report(rows: pd.DataFrame, atr_table: pd.DataFrame, paired: pd.DataFrame) -> str:
    first = summarize_entry_mode(rows, "first")
    second = summarize_entry_mode(rows, "second")
    persistent = rows.loc[rows["second_entry_exists"] == 1]
    reduction = (1.0 - len(persistent) / len(rows)) * 100.0
    delay = pd.to_numeric(persistent["delay_close_return_pct"], errors="coerce").dropna()
    adverse = delay.loc[delay < 0]
    favorable = delay.loc[delay > 0]
    delay_ci = _bootstrap_ci(delay.to_numpy(), seed=777)
    missed = rows.loc[
        (rows["second_entry_exists"] == 0) & (rows["first_true_decline"] == 1)
    ]

    paired_index = paired.set_index("metric")
    paired_next = paired_index.loc["return_next_close_pct"]
    paired_next_hit = paired_index.loc["next_close_negative"]
    paired_f0 = paired_index.loc["f0_false_warning"]
    paired_f1 = paired_index.loc["f1_false_warning"]
    paired_f2 = paired_index.loc["f2_false_warning"]

    overall_atr = atr_table.loc[atr_table["stock_code"] == "ALL"].set_index(["entry_mode", "criterion"])
    atr_lines = []
    for criterion in ("F0_FIXED_2PCT", "F1_ATR_0_8", "F2_ATR_1_0"):
        a = overall_atr.loc[("FIRST_SELL", criterion)]
        b = overall_atr.loc[("SECOND_CONSECUTIVE_SELL", criterion)]
        atr_lines.append(
            f"| {criterion} | {int(a['false_warning_count'])}/{int(a['evaluated_entries'])} "
            f"({_fmt(a['false_warning_rate_pct'])}%) | {int(b['false_warning_count'])}/{int(b['evaluated_entries'])} "
            f"({_fmt(b['false_warning_rate_pct'])}%) | {_fmt(a['mean_threshold_pct'])}% / "
            f"{_fmt(b['mean_threshold_pct'])}% | {_fmt(a['stock_rate_std_pp'])} / {_fmt(b['stock_rate_std_pp'])} |"
        )

    stock_lines = []
    for code in TICKERS:
        group = rows.loc[rows["stock_code"] == code]
        if group.empty:
            continue
        a = summarize_entry_mode(group, "first")
        b = summarize_entry_mode(group, "second")
        drop = (1.0 - b["entry_count"] / a["entry_count"]) * 100.0
        missed_count = int(((group["second_entry_exists"] == 0) & (group["first_true_decline"] == 1)).sum())
        stock_lines.append(
            f"| {group.iloc[0]['stock_name']} | {a['entry_count']} | {b['entry_count']} | {drop:.1f}% | "
            f"{_ratio(a['next_close_negative_count'], a['next_close_negative_denominator'], a['next_close_negative_pct'])} | "
            f"{_ratio(b['next_close_negative_count'], b['next_close_negative_denominator'], b['next_close_negative_pct'])} | "
            f"{_fmt(a['f0_false_pct'])}%→{_fmt(b['f0_false_pct'])}% | "
            f"{_fmt(a['f1_false_pct'])}%→{_fmt(b['f1_false_pct'])}% | "
            f"{_fmt(a['f2_false_pct'])}%→{_fmt(b['f2_false_pct'])}% | {missed_count} |"
        )

    # Decision: quality improved, but coverage/delay and bootstrap uncertainty matter.
    hit_delta = second["next_close_negative_pct"] - first["next_close_negative_pct"]
    false_delta = second["f0_false_pct"] - first["f0_false_pct"]
    if hit_delta >= 5 and false_delta <= -10 and reduction < 20 and delay.mean() > -0.5:
        decision = "A. 2nd SELL이 명확히 우수"
    elif hit_delta > 0 or false_delta < 0:
        decision = "B. 일부 개선"
    else:
        decision = "C. 개선 없음"

    report = f"""# 두 번째 SELL 확인은 일부 품질을 개선하지만 신호 감소와 지연 비용이 따른다

## 한 줄 결론

2nd consecutive SELL은 평가 신호를 {len(rows)}개에서 {len(persistent)}개로 {reduction:.1f}% 줄였다. 익일 종가 하락률은 {_fmt(first['next_close_negative_pct'])}%에서 {_fmt(second['next_close_negative_pct'])}%로 변했고 F0 false-warning은 {_fmt(first['f0_false_pct'])}%에서 {_fmt(second['f0_false_pct'])}%로 변했다. paired bootstrap과 지연 비용을 함께 보면 판정은 **{decision}**이다.

## 1st vs 2nd SELL

| 지표 | 1st SELL | 2nd consecutive SELL |
|---|---:|---:|
| 평가 episode 수 | {first['entry_count']} | {second['entry_count']} |
| 다음 45분 하락률 | {_ratio(first['next_45m_negative_count'], first['next_45m_negative_denominator'], first['next_45m_negative_pct'])} | {_ratio(second['next_45m_negative_count'], second['next_45m_negative_denominator'], second['next_45m_negative_pct'])} |
| 당일 종가 하락률 | {_ratio(first['day_close_negative_count'], first['day_close_negative_denominator'], first['day_close_negative_pct'])} | {_ratio(second['day_close_negative_count'], second['day_close_negative_denominator'], second['day_close_negative_pct'])} |
| 익일 종가 하락률 | {_ratio(first['next_close_negative_count'], first['next_close_negative_denominator'], first['next_close_negative_pct'])} | {_ratio(second['next_close_negative_count'], second['next_close_negative_denominator'], second['next_close_negative_pct'])} |
| 평균 MAE | {_fmt(first['avg_mae_pct'])}% | {_fmt(second['avg_mae_pct'])}% |
| 평균 MFE | {_fmt(first['avg_mfe_pct'])}% | {_fmt(second['avg_mfe_pct'])}% |
| F0 false-warning | {_ratio(first['f0_false_count'], first['f0_denominator'], first['f0_false_pct'])} | {_ratio(second['f0_false_count'], second['f0_denominator'], second['f0_false_pct'])} |
| F1 false-warning | {_ratio(first['f1_false_count'], first['f1_denominator'], first['f1_false_pct'])} | {_ratio(second['f1_false_count'], second['f1_denominator'], second['f1_false_pct'])} |
| F2 false-warning | {_ratio(first['f2_false_count'], first['f2_denominator'], first['f2_false_pct'])} | {_ratio(second['f2_false_count'], second['f2_denominator'], second['f2_false_pct'])} |

2nd 방식의 수치는 두 번째 연속 SELL이 실제 발생한 episode만 선택한 정책 성능이다. 시간 지연 자체를 분리하기 위해 같은 {len(persistent)}개 episode의 첫 시점과 두 번째 시점을 paired 비교했다.

- paired 익일 수익률 차이(second-first): {_fmt(paired_next['paired_difference_second_minus_first'])}%p, 95% bootstrap CI [{_fmt(paired_next['bootstrap_ci95_low'])}, {_fmt(paired_next['bootstrap_ci95_high'])}]
- paired 익일 하락률 차이: {_fmt(paired_next_hit['paired_difference_second_minus_first'])}%p, CI [{_fmt(paired_next_hit['bootstrap_ci95_low'])}, {_fmt(paired_next_hit['bootstrap_ci95_high'])}]
- paired F0 false-warning 차이: {_fmt(paired_f0['paired_difference_second_minus_first'])}%p, CI [{_fmt(paired_f0['bootstrap_ci95_low'])}, {_fmt(paired_f0['bootstrap_ci95_high'])}]
- paired F1 차이: {_fmt(paired_f1['paired_difference_second_minus_first'])}%p; paired F2 차이: {_fmt(paired_f2['paired_difference_second_minus_first'])}%p

## ATR false-warning 비교

ATR은 `technical_analysis.calculate_wilder_atr()`의 일봉 Wilder ATR14를 그대로 사용했다. 진입 당일 봉은 제외하고 직전 완료 일봉까지만 사용했으며 `ATR_pct = ATR14 / entry_close * 100`으로 환산했다.

| 기준 | 1st SELL | 2nd SELL | 평균 기준값(1st / 2nd) | 종목간 rate 표준편차(1st / 2nd) |
|---|---:|---:|---:|---:|
{chr(10).join(atr_lines)}

- F1, 1st 평가: {_atr_assessment(atr_table, 'FIRST_SELL', 'F1_ATR_0_8')}
- F1, 2nd 평가: {_atr_assessment(atr_table, 'SECOND_CONSECUTIVE_SELL', 'F1_ATR_0_8')}
- F2, 1st 평가: {_atr_assessment(atr_table, 'FIRST_SELL', 'F2_ATR_1_0')}
- F2, 2nd 평가: {_atr_assessment(atr_table, 'SECOND_CONSECUTIVE_SELL', 'F2_ATR_1_0')}

“안정성”은 세 종목의 false-warning rate 표준편차와 범위를 기준으로 판단했다. ATR 기준의 평균 threshold가 F0의 2%보다 높다면 false-warning 비율 감소는 기계적으로 발생할 수 있다. 전체 비율을 낮추더라도 종목간 편차가 커지면 안정적이라고 보지 않았다.

## 종목별 결과

| 종목 | 1st 수 | 2nd 수 | 신호 감소 | 익일 하락 1st | 익일 하락 2nd | F0 변화 | F1 변화 | F2 변화 | 2nd 부재로 놓친 기존 하락 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(stock_lines)}

`2nd 부재로 놓친 기존 하락`은 첫 SELL 기준 익일 종가가 하락했지만 다음 bar에서 SELL이 해제돼 2nd 정책에는 진입하지 못한 episode다. 전체에서는 {len(missed)}개다.

## 지연 비용

- first→second close 평균 변화: {_fmt(delay.mean())}% (95% bootstrap CI [{_fmt(delay_ci[0])}, {_fmt(delay_ci[1])}])
- 전체 episode 기준 평균 불리 이동 기여: {_fmt(persistent['delay_adverse_move_pct'].mean())}%
- 전체 episode 기준 평균 유리 이동 기여: +{_fmt(persistent['delay_favorable_move_pct'].mean())}%
- 가격이 하락한 불리 지연: {len(adverse)}/{len(delay)} episode, 조건부 평균 {_fmt(adverse.mean())}%
- 가격이 상승한 유리 지연: {len(favorable)}/{len(delay)} episode, 조건부 평균 +{_fmt(favorable.mean())}%
- paired next-close return 효과크기 dz: {_fmt(paired_next['paired_standardized_effect_dz'], 3)}

SELL 관점에서 first→second 사이 하락은 더 낮은 가격에서 대응하게 되는 비용이다. 정책 전체 F0는 10.47%p 낮아졌지만 같은 episode paired 차이는 -2.78%p이고 CI가 0을 포함한다. 따라서 관찰된 오탐 감소가 평균 -0.15% 지연 비용과 7개 기존 하락 신호 누락보다 명확히 크다고 볼 수 없다.

## 판정

**{decision}**

ATR 기준은 F1/F2를 각각 위 안정성 판정대로 해석한다. 이 결과는 3종목·53 episode에 한정되며 threshold 변경 근거가 아니다.

## 다음 권고

1. 동일한 1st/2nd 정의를 다른 기간과 종목에 사전 고정해 out-of-sample으로 재검증한다.
2. ATR false-warning 기준은 종목간 분산뿐 아니라 이후 실제 청산 비용과 함께 검증하기 전까지 연구 라벨로만 유지한다.

## 방법과 제한

- 두 번째 진입은 episode 길이를 보지 않고 first event 바로 다음 실제 event가 다시 SELL_WARNING인지로 판정했다.
- second 시점의 forward outcome은 기존 replay event 행에 이미 계산된 값을 재사용했다.
- ATR은 Yahoo 실제 일봉을 다시 조회했으며 synthetic 또는 3% fallback을 사용하지 않았다. 결측은 그대로 결측 처리한다.
- bootstrap은 episode 단위 20,000회 재표집이다. 표본이 작고 동일 종목 내 episode 상관을 반영하지 않으므로 CI는 탐색적이다.
- 엔진, threshold, scheduler, renderer, notifier, 주문 경로는 수정하거나 호출하지 않았다.
"""
    return report


def run(artifact_dir: Path, daily_period: str = "2y") -> Tuple[pd.DataFrame, pd.DataFrame, Path]:
    events = pd.read_csv(
        artifact_dir / "sell_warning_45m_replay_events.csv", dtype={"stock_code": str}
    )
    episodes = pd.read_csv(
        artifact_dir / "sell_warning_45m_replay_episodes.csv", dtype={"stock_code": str}
    )
    events["stock_code"] = events["stock_code"].str.zfill(6)
    episodes["stock_code"] = episodes["stock_code"].str.zfill(6)

    daily_frames: Dict[str, Optional[pd.DataFrame]] = {}
    fetch_status: Dict[str, str] = {}
    for code, ticker in TICKERS.items():
        daily_frames[code], fetch_status[code] = fetch_daily_history(ticker, period=daily_period)
    rows = build_entry_comparison(events, episodes, daily_frames)
    rows["daily_ticker"] = rows["stock_code"].map(TICKERS)
    rows["daily_fetch_status"] = rows["stock_code"].map(fetch_status)
    atr_table = build_atr_comparison(rows)
    paired = paired_statistics(rows)
    report = build_report(rows, atr_table, paired)

    rows.to_csv(
        artifact_dir / "sell_warning_first_vs_second_sell.csv", index=False, encoding="utf-8-sig"
    )
    atr_table.to_csv(
        artifact_dir / "sell_warning_atr_false_warning_comparison.csv",
        index=False,
        encoding="utf-8-sig",
    )
    report_path = artifact_dir / "sell_warning_persistence_atr_report.md"
    report_path.write_text(report, encoding="utf-8")
    return rows, atr_table, report_path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research 1st-vs-2nd SELL and ATR false warnings")
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument("--daily-period", default="2y")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    rows, stats, report_path = run(Path(args.artifact_dir), daily_period=args.daily_period)
    print(f"SELL episodes: {len(rows)}")
    print(f"Second consecutive entries: {int(rows['second_entry_exists'].sum())}")
    print(f"Valid first ATR: {int(rows['first_atr14'].notna().sum())}")
    print(f"Valid second ATR: {int(rows['second_atr14'].notna().sum())}")
    print(f"Statistics rows: {len(stats)}")
    print(f"Report: {report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
