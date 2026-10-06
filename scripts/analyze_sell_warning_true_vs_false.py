"""Research-only feature analysis for historical SELL_WARNING episodes.

The script consumes existing replay CSVs and writes research artifacts only.
It does not import or modify the production engine, scheduler, notifier, or orders.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts"


FEATURES: Dict[str, Tuple[str, str, bool]] = {
    # feature: (category, role, cross-stock ranking eligible)
    "vwap_spread": ("PRICE", "LEVEL", False),
    "vwap_spread_pct": ("PRICE", "LEVEL", True),
    "vwap_spread_change_1": ("PRICE", "MOMENTUM", False),
    "vwap_spread_change_2": ("PRICE", "MOMENTUM", False),
    "vwap_spread_pct_change_1": ("PRICE", "MOMENTUM", True),
    "vwap_spread_pct_change_2": ("PRICE", "MOMENTUM", True),
    "close_minus_vwap26": ("PRICE", "LEVEL", False),
    "close_minus_vwap26_pct": ("PRICE", "LEVEL", True),
    "close_minus_cloud_bottom": ("PRICE", "LEVEL", False),
    "close_minus_cloud_bottom_pct": ("PRICE", "LEVEL", True),
    "price_weakness_streak": ("PRICE", "PERSISTENCE", True),
    "obv_minus_wma9": ("OBV", "LEVEL", False),
    "obv_gap_pct_of_scale": ("OBV", "LEVEL", True),
    "obv_gap_delta": ("OBV", "MOMENTUM", False),
    "obv_gap_delta_pct_of_scale": ("OBV", "MOMENTUM", True),
    "obv_gap_change_1": ("OBV", "MOMENTUM", False),
    "obv_gap_change_2": ("OBV", "MOMENTUM", False),
    "obv_gap_change_3": ("OBV", "MOMENTUM", False),
    "obv_gap_delta_mean_last3": ("OBV", "MOMENTUM", False),
    "obv_weakness_streak": ("OBV", "PERSISTENCE", True),
    "chaikin_value": ("CHAIKIN", "LEVEL", False),
    "chaikin_delta": ("CHAIKIN", "MOMENTUM", False),
    "chaikin_delta_change_1": ("CHAIKIN", "MOMENTUM", False),
    "chaikin_delta_change_2": ("CHAIKIN", "MOMENTUM", False),
    "chaikin_delta_mean_last2": ("CHAIKIN", "MOMENTUM", False),
    "chaikin_delta_mean_last3": ("CHAIKIN", "MOMENTUM", False),
    "chaikin_falling_streak": ("CHAIKIN", "PERSISTENCE", True),
    "chaikin_negative_streak": ("CHAIKIN", "PERSISTENCE", True),
    "chaikin_recovery_prior3": ("CHAIKIN", "MOMENTUM", True),
    "di_spread": ("DMI_ADX", "LEVEL", True),
    "di_spread_change_1": ("DMI_ADX", "MOMENTUM", True),
    "di_spread_change_2": ("DMI_ADX", "MOMENTUM", True),
    "adx": ("DMI_ADX", "LEVEL", True),
    "adx_change_1": ("DMI_ADX", "MOMENTUM", True),
    "adx_change_2": ("DMI_ADX", "MOMENTUM", True),
    "bear_trend_streak": ("DMI_ADX", "PERSISTENCE", True),
    "caution_streak_before": ("STATE", "PERSISTENCE", True),
    "entered_from_caution": ("STATE", "PERSISTENCE", True),
    "axes_prev1": ("STATE", "LEVEL", True),
    "axes_prev2": ("STATE", "LEVEL", True),
    "axes_prev3": ("STATE", "LEVEL", True),
    "axes_change_1": ("STATE", "MOMENTUM", True),
    "axes_change_2": ("STATE", "MOMENTUM", True),
    "axes_mean_prior3": ("STATE", "LEVEL", True),
    "state_oscillations_last4": ("STATE", "PERSISTENCE", True),
    "sell_duration_bars": ("STATE", "PERSISTENCE", True),
    "sell_persistent_2plus": ("STATE", "PERSISTENCE", True),
}

POST_ENTRY_FEATURES = {"sell_duration_bars", "sell_persistent_2plus"}


def _number(value: Any) -> float:
    return float(pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0])


def _safe_pct(numerator: float, denominator: float) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or abs(denominator) < 1e-12:
        return math.nan
    return numerator / abs(denominator) * 100.0


def _difference(current: pd.Series, previous: Optional[pd.Series], column: str) -> float:
    if previous is None:
        return math.nan
    left = _number(current.get(column))
    right = _number(previous.get(column))
    return left - right if math.isfinite(left) and math.isfinite(right) else math.nan


def _consecutive_true(group: pd.DataFrame, position: int, predicate) -> int:
    count = 0
    for cursor in range(position, -1, -1):
        row = group.iloc[cursor]
        if str(row.get("state")) == "UNKNOWN" or not predicate(row):
            break
        count += 1
    return count


def _prior_state_streak(group: pd.DataFrame, position: int, expected: str) -> int:
    count = 0
    for cursor in range(position - 1, -1, -1):
        if str(group.iloc[cursor].get("state")) != expected:
            break
        count += 1
    return count


def _lag(group: pd.DataFrame, position: int, bars: int) -> Optional[pd.Series]:
    cursor = position - bars
    return None if cursor < 0 else group.iloc[cursor]


def _mean_values(rows: Iterable[Optional[pd.Series]], column: str) -> float:
    values = []
    for row in rows:
        if row is None:
            continue
        value = _number(row.get(column))
        if math.isfinite(value):
            values.append(value)
    return float(np.mean(values)) if values else math.nan


def build_episode_features(events: pd.DataFrame, episodes: pd.DataFrame) -> pd.DataFrame:
    current_sell = episodes.loc[
        (episodes["variant"] == "CURRENT") & (episodes["state"] == "SELL_WARNING")
    ].copy()
    event_groups = {
        str(code): group.sort_values("bar_timestamp").reset_index(drop=True)
        for code, group in events.groupby("stock_code", sort=False)
    }
    rows: List[Dict[str, Any]] = []

    for _, episode in current_sell.sort_values(["stock_code", "start_timestamp"]).iterrows():
        code = str(episode["stock_code"]).zfill(6)
        group = event_groups[code]
        matches = group.index[group["bar_timestamp"] == episode["start_timestamp"]].tolist()
        if len(matches) != 1:
            raise ValueError(f"Expected one event for {code} {episode['start_timestamp']}, got {len(matches)}")
        position = matches[0]
        now = group.iloc[position]
        lag1, lag2, lag3 = (_lag(group, position, bars) for bars in (1, 2, 3))
        prior_rows = [lag1, lag2, lag3]

        vwap9 = _number(now.get("vwap9"))
        vwap26 = _number(now.get("vwap26"))
        close = _number(now.get("close"))
        cloud = _number(now.get("cloud_bottom"))
        vwap_spread = vwap9 - vwap26
        obv = _number(now.get("obv"))
        obv_wma9 = _number(now.get("obv_wma9"))
        obv_gap = obv - obv_wma9
        obv_scale = max(abs(obv), abs(obv_wma9), 1.0)
        di_spread = _number(now.get("minus_di")) - _number(now.get("plus_di"))

        def spread(row: Optional[pd.Series]) -> float:
            if row is None:
                return math.nan
            return _number(row.get("vwap9")) - _number(row.get("vwap26"))

        def spread_pct(row: Optional[pd.Series]) -> float:
            if row is None:
                return math.nan
            return _safe_pct(spread(row), _number(row.get("vwap26")))

        def gap(row: Optional[pd.Series]) -> float:
            if row is None:
                return math.nan
            return _number(row.get("obv")) - _number(row.get("obv_wma9"))

        def dmi_spread(row: Optional[pd.Series]) -> float:
            if row is None:
                return math.nan
            return _number(row.get("minus_di")) - _number(row.get("plus_di"))

        previous_states = [str(row.get("state")) for row in reversed(prior_rows) if row is not None]
        last4_states = previous_states + [str(now.get("state"))]
        oscillations = sum(left != right for left, right in zip(last4_states, last4_states[1:]))
        recovery_prior3 = any(
            row is not None
            and str(row.get("state")) != "UNKNOWN"
            and (
                _number(row.get("chaikin_delta")) > 0
                or str(row.get("chaikin_state")) == "CHAIKIN_RISING"
            )
            for row in prior_rows
        )

        next_close = _number(episode.get("return_next_close_pct"))
        mfe = _number(episode.get("mfe_1d_pct"))
        true_decline: Optional[int] = None if not math.isfinite(next_close) else int(next_close < 0)
        false_warning: Optional[int] = None if not math.isfinite(mfe) else int(mfe >= 2.0)
        overlap = int(true_decline == 1 and false_warning == 1) if None not in (true_decline, false_warning) else None
        if true_decline is None or false_warning is None:
            analysis_group = "UNLABELED"
        elif true_decline and false_warning:
            analysis_group = "OVERLAP"
        elif true_decline:
            analysis_group = "TRUE_ONLY"
        elif false_warning:
            analysis_group = "FALSE_ONLY"
        else:
            analysis_group = "NEITHER"

        prior_valid = [row is not None and str(row.get("state")) != "UNKNOWN" for row in prior_rows]
        feature_row: Dict[str, Any] = {
            "stock_code": code,
            "stock_name": episode["stock_name"],
            "episode_start": episode["start_timestamp"],
            "episode_end": episode["end_timestamp"],
            "sell_duration_bars": int(episode["duration_bars"]),
            "sell_persistent_2plus": int(int(episode["duration_bars"]) >= 2),
            "return_next_45m_pct": episode.get("return_next_45m_pct"),
            "return_next_90m_pct": episode.get("return_next_90m_pct"),
            "return_day_close_pct": episode.get("return_day_close_pct"),
            "return_next_open_pct": episode.get("return_next_open_pct"),
            "return_next_close_pct": episode.get("return_next_close_pct"),
            "mae_1d_pct": episode.get("mae_1d_pct"),
            "mfe_1d_pct": episode.get("mfe_1d_pct"),
            "true_decline": true_decline,
            "false_warning": false_warning,
            "overlap_label": overlap,
            "analysis_group": analysis_group,
            "prior3_history_complete": int(all(prior_valid)),
            "prior1_timestamp": None if lag1 is None else lag1.get("bar_timestamp"),
            "prior2_timestamp": None if lag2 is None else lag2.get("bar_timestamp"),
            "prior3_timestamp": None if lag3 is None else lag3.get("bar_timestamp"),
            "vwap_spread": vwap_spread,
            "vwap_spread_pct": _safe_pct(vwap_spread, vwap26),
            "vwap_spread_change_1": vwap_spread - spread(lag1),
            "vwap_spread_change_2": vwap_spread - spread(lag2),
            "vwap_spread_pct_change_1": _safe_pct(vwap_spread, vwap26) - spread_pct(lag1),
            "vwap_spread_pct_change_2": _safe_pct(vwap_spread, vwap26) - spread_pct(lag2),
            "close_minus_vwap26": close - vwap26,
            "close_minus_vwap26_pct": _safe_pct(close - vwap26, vwap26),
            "close_minus_cloud_bottom": close - cloud,
            "close_minus_cloud_bottom_pct": _safe_pct(close - cloud, cloud),
            "price_weakness_streak": _consecutive_true(
                group, position, lambda row: _number(row.get("price_weakness")) == 1
            ),
            "obv_minus_wma9": obv_gap,
            "obv_gap_pct_of_scale": _safe_pct(obv_gap, obv_scale),
            "obv_gap_delta": _number(now.get("obv_gap_delta")),
            "obv_gap_delta_pct_of_scale": _safe_pct(_number(now.get("obv_gap_delta")), obv_scale),
            "obv_gap_change_1": obv_gap - gap(lag1),
            "obv_gap_change_2": obv_gap - gap(lag2),
            "obv_gap_change_3": obv_gap - gap(lag3),
            "obv_gap_delta_mean_last3": _mean_values([now, lag1, lag2], "obv_gap_delta"),
            "obv_weakness_streak": _consecutive_true(
                group, position, lambda row: _number(row.get("obv_weakness")) == 1
            ),
            "chaikin_value": _number(now.get("chaikin_value")),
            "chaikin_delta": _number(now.get("chaikin_delta")),
            "chaikin_delta_change_1": _difference(now, lag1, "chaikin_delta"),
            "chaikin_delta_change_2": _difference(now, lag2, "chaikin_delta"),
            "chaikin_delta_mean_last2": _mean_values([now, lag1], "chaikin_delta"),
            "chaikin_delta_mean_last3": _mean_values([now, lag1, lag2], "chaikin_delta"),
            "chaikin_falling_streak": _consecutive_true(
                group, position, lambda row: str(row.get("chaikin_state")) == "CHAIKIN_FALLING"
            ),
            "chaikin_negative_streak": _consecutive_true(
                group, position, lambda row: _number(row.get("chaikin_value")) < 0
            ),
            "chaikin_recovery_prior3": int(recovery_prior3),
            "di_spread": di_spread,
            "di_spread_change_1": di_spread - dmi_spread(lag1),
            "di_spread_change_2": di_spread - dmi_spread(lag2),
            "adx": _number(now.get("adx")),
            "adx_change_1": _difference(now, lag1, "adx"),
            "adx_change_2": _difference(now, lag2, "adx"),
            "bear_trend_streak": _consecutive_true(
                group, position, lambda row: _number(row.get("bear_trend")) == 1
            ),
            "caution_streak_before": _prior_state_streak(group, position, "CAUTION"),
            "entered_from_caution": int(lag1 is not None and str(lag1.get("state")) == "CAUTION"),
            "axes_prev1": math.nan if lag1 is None else _number(lag1.get("bearish_axes_count")),
            "axes_prev2": math.nan if lag2 is None else _number(lag2.get("bearish_axes_count")),
            "axes_prev3": math.nan if lag3 is None else _number(lag3.get("bearish_axes_count")),
            "axes_change_1": _number(now.get("bearish_axes_count")) - (
                math.nan if lag1 is None else _number(lag1.get("bearish_axes_count"))
            ),
            "axes_change_2": _number(now.get("bearish_axes_count")) - (
                math.nan if lag2 is None else _number(lag2.get("bearish_axes_count"))
            ),
            "axes_mean_prior3": _mean_values(prior_rows, "bearish_axes_count"),
            "state_oscillations_last4": oscillations,
        }
        rows.append(feature_row)

    result = pd.DataFrame(rows)
    if len(result) != 53:
        raise AssertionError(f"Expected 53 CURRENT SELL_WARNING episodes, got {len(result)}")
    return result


def _mann_whitney(x: np.ndarray, y: np.ndarray) -> Tuple[float, float, float]:
    """Return U for x, two-sided normal-approx p, and Cliff's delta (x-y)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    combined = np.concatenate([x, y])
    ranks = pd.Series(combined).rank(method="average").to_numpy(dtype=float)
    n_x, n_y = len(x), len(y)
    u_x = float(ranks[:n_x].sum() - n_x * (n_x + 1) / 2.0)
    cliff = 2.0 * u_x / (n_x * n_y) - 1.0
    n = n_x + n_y
    _, tie_counts = np.unique(combined, return_counts=True)
    tie_term = float(np.sum(tie_counts**3 - tie_counts))
    variance = n_x * n_y / 12.0 * ((n + 1.0) - tie_term / (n * (n - 1.0))) if n > 1 else 0.0
    if variance <= 0:
        p_value = 1.0
    else:
        distance = max(abs(u_x - n_x * n_y / 2.0) - 0.5, 0.0)
        z = distance / math.sqrt(variance)
        p_value = math.erfc(z / math.sqrt(2.0))
    return u_x, p_value, cliff


def _bh_adjust(p_values: pd.Series) -> pd.Series:
    result = pd.Series(np.nan, index=p_values.index, dtype=float)
    valid = p_values.dropna().sort_values()
    if valid.empty:
        return result
    count = len(valid)
    adjusted = np.minimum.accumulate((valid.to_numpy() * count / np.arange(1, count + 1))[::-1])[::-1]
    result.loc[valid.index] = np.minimum(adjusted, 1.0)
    return result


def compare_features(features: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    comparisons = {
        "INCLUSIVE_LABELS": (
            features.loc[features["true_decline"] == 1],
            features.loc[features["false_warning"] == 1],
            "Groups overlap; descriptive only",
        ),
        "EXCLUSIVE_TRUE_ONLY_VS_FALSE_ONLY": (
            features.loc[features["analysis_group"] == "TRUE_ONLY"],
            features.loc[features["analysis_group"] == "FALSE_ONLY"],
            "Primary independent comparison",
        ),
    }
    for basis, (true_group, false_group, note) in comparisons.items():
        for feature, (category, role, ranking_eligible) in FEATURES.items():
            x = pd.to_numeric(true_group[feature], errors="coerce").dropna().to_numpy(dtype=float)
            y = pd.to_numeric(false_group[feature], errors="coerce").dropna().to_numpy(dtype=float)
            if len(x) and len(y):
                u_value, p_value, cliff = _mann_whitney(x, y)
            else:
                u_value = p_value = cliff = math.nan
            rows.append({
                "comparison_basis": basis,
                "feature_category": category,
                "feature_role": role,
                "feature": feature,
                "ranking_eligible": int(ranking_eligible),
                "available_at_episode_entry": int(feature not in POST_ENTRY_FEATURES),
                "n_true": len(x),
                "n_false": len(y),
                "true_mean": float(np.mean(x)) if len(x) else math.nan,
                "false_mean": float(np.mean(y)) if len(y) else math.nan,
                "true_median": float(np.median(x)) if len(x) else math.nan,
                "false_median": float(np.median(y)) if len(y) else math.nan,
                "median_difference_true_minus_false": (
                    float(np.median(x) - np.median(y)) if len(x) and len(y) else math.nan
                ),
                "mann_whitney_u_true": u_value,
                "p_value_two_sided_normal_approx": p_value,
                "cliffs_delta_true_minus_false": cliff,
                "absolute_cliffs_delta": abs(cliff) if math.isfinite(cliff) else math.nan,
                "interpretation_note": note,
            })
    result = pd.DataFrame(rows)
    result["stock_effects_true_minus_false"] = ""
    result["stocks_evaluable"] = 0
    result["stocks_same_pooled_direction"] = 0
    for index, row in result.iterrows():
        if row["comparison_basis"] != "EXCLUSIVE_TRUE_ONLY_VS_FALSE_ONLY":
            continue
        effects = []
        for stock_code, stock_rows in features.groupby("stock_code"):
            x = pd.to_numeric(
                stock_rows.loc[stock_rows["analysis_group"] == "TRUE_ONLY", row["feature"]],
                errors="coerce",
            ).dropna().to_numpy(dtype=float)
            y = pd.to_numeric(
                stock_rows.loc[stock_rows["analysis_group"] == "FALSE_ONLY", row["feature"]],
                errors="coerce",
            ).dropna().to_numpy(dtype=float)
            if len(x) and len(y):
                _, _, stock_cliff = _mann_whitney(x, y)
                effects.append((str(stock_code).zfill(6), stock_cliff))
        pooled = row["cliffs_delta_true_minus_false"]
        same_direction = sum(
            effect == 0 or pooled == 0 or math.copysign(1.0, effect) == math.copysign(1.0, pooled)
            for _, effect in effects
        )
        result.at[index, "stock_effects_true_minus_false"] = ";".join(
            f"{code}:{effect:.6f}" for code, effect in effects
        )
        result.at[index, "stocks_evaluable"] = len(effects)
        result.at[index, "stocks_same_pooled_direction"] = same_direction
    result["fdr_bh_q_value"] = np.nan
    result["effect_rank"] = np.nan
    for basis, indexes in result.groupby("comparison_basis").groups.items():
        result.loc[indexes, "fdr_bh_q_value"] = _bh_adjust(
            result.loc[indexes, "p_value_two_sided_normal_approx"]
        )
        eligible = result.loc[indexes].query("ranking_eligible == 1").sort_values(
            "absolute_cliffs_delta", ascending=False
        )
        result.loc[eligible.index, "effect_rank"] = np.arange(1, len(eligible) + 1)
    return result.sort_values(["comparison_basis", "effect_rank", "feature_category", "feature"], na_position="last")


def _rate(frame: pd.DataFrame, column: str) -> Tuple[int, int, float]:
    valid = pd.to_numeric(frame[column], errors="coerce").dropna()
    return int(valid.sum()), len(valid), float(valid.mean() * 100.0) if len(valid) else math.nan


def _segment_line(features: pd.DataFrame, mask: pd.Series, label: str) -> str:
    segment = features.loc[mask]
    true_n, true_d, true_rate = _rate(segment, "true_decline")
    false_n, false_d, false_rate = _rate(segment, "false_warning")
    return (
        f"- {label}: {len(segment)} episodes; TRUE_DECLINE {true_n}/{true_d} ({true_rate:.1f}%), "
        f"FALSE_WARNING {false_n}/{false_d} ({false_rate:.1f}%)"
    )


def _fmt(value: Any, digits: int = 3) -> str:
    number = _number(value)
    return "NA" if not math.isfinite(number) else f"{number:.{digits}f}"


def build_report(features: pd.DataFrame, comparison: pd.DataFrame) -> str:
    evaluable = features.loc[features["analysis_group"] != "UNLABELED"]
    group_counts = features["analysis_group"].value_counts()
    primary = comparison.loc[
        (comparison["comparison_basis"] == "EXCLUSIVE_TRUE_ONLY_VS_FALSE_ONLY")
        & (comparison["ranking_eligible"] == 1)
    ].sort_values("effect_rank")
    top5 = primary.head(5)
    inclusive = comparison.loc[comparison["comparison_basis"] == "INCLUSIVE_LABELS"].set_index("feature")

    top_lines = []
    for _, row in top5.iterrows():
        direction = "TRUE가 높음" if row["cliffs_delta_true_minus_false"] > 0 else "FALSE가 높음"
        timing = "사후 2봉 확인 필요" if not int(row["available_at_episode_entry"]) else "진입 시점 사용 가능"
        top_lines.append(
            f"{int(row['effect_rank'])}. `{row['feature']}` ({row['feature_role']}): "
            f"Cliff's delta {_fmt(row['cliffs_delta_true_minus_false'])} ({direction}), "
            f"mean TRUE {_fmt(row['true_mean'])} vs FALSE {_fmt(row['false_mean'])}, "
            f"median TRUE {_fmt(row['true_median'])} vs FALSE {_fmt(row['false_median'])}, "
            f"p={_fmt(row['p_value_two_sided_normal_approx'])}, q={_fmt(row['fdr_bh_q_value'])}, "
            f"종목 방향 일치 {int(row['stocks_same_pooled_direction'])}/{int(row['stocks_evaluable'])}, {timing}"
        )

    level_effect = primary.loc[primary["feature_role"] == "LEVEL", "absolute_cliffs_delta"].median()
    momentum_effect = primary.loc[primary["feature_role"] == "MOMENTUM", "absolute_cliffs_delta"].median()
    persistence_effect = primary.loc[primary["feature_role"] == "PERSISTENCE", "absolute_cliffs_delta"].median()

    di1 = primary.set_index("feature").loc["di_spread_change_1"]
    di2 = primary.set_index("feature").loc["di_spread_change_2"]
    chaikin = inclusive.loc["chaikin_recovery_prior3"]

    report = f"""# SELL_WARNING 진짜 하락군과 false-warning군은 강하게 분리되지 않았다

## Executive Summary

- 53개 SELL_WARNING episode 중 익일 결과를 평가할 수 있는 52개는 TRUE_DECLINE 28개, FALSE_WARNING 30개였고 9개는 두 라벨이 동시에 성립했다. 독립 비교 표본은 TRUE_ONLY 19개와 FALSE_ONLY 21개다.
- 47개 feature를 동시에 비교했고 그중 종목 간 단위 비교가 가능한 {len(primary)}개를 TOP 순위 대상으로 삼았다. 전체 47개에 FDR을 적용한 q-value는 모두 통상적 0.05 기준을 충족하지 못했다. 발견적 신호이지 규칙 변경 근거가 아니다.
- feature role별 Cliff's delta 절대값 중앙값은 LEVEL {_fmt(level_effect)}, MOMENTUM {_fmt(momentum_effect)}, PERSISTENCE {_fmt(persistence_effect)}였다. 따라서 이 표본에서는 변화속도가 레벨보다 일관되게 우수하다고 단정할 수 없다.

## 표본과 라벨

- 총 SELL_WARNING episodes: {len(features)}
- TRUE_ONLY: {int(group_counts.get('TRUE_ONLY', 0))}
- FALSE_ONLY: {int(group_counts.get('FALSE_ONLY', 0))}
- OVERLAP: {int(group_counts.get('OVERLAP', 0))}
- NEITHER: {int(group_counts.get('NEITHER', 0))}
- 익일 결과 미확정: {int(group_counts.get('UNLABELED', 0))}
- 직전 3개 bar가 모두 유효한 episode: {int(features['prior3_history_complete'].sum())}/{len(features)}

TRUE_DECLINE은 익일 종가 수익률 < 0, FALSE_WARNING은 다음 거래일까지 MFE >= +2%다. 장중 상승 후 익일 하락한 9개 episode는 두 라벨에 모두 포함된다. 중첩 집단의 Mann-Whitney 결과는 기술통계로만 제공하고, feature 순위와 추론은 중첩을 제거한 TRUE_ONLY 대 FALSE_ONLY 비교를 사용했다.

## 가장 구분력이 큰 feature TOP 5

{chr(10).join(top_lines)}

Cliff's delta는 TRUE_ONLY 값이 FALSE_ONLY보다 클 확률적 방향을 나타낸다. 절대값이 클수록 분리가 크지만, 작은 표본과 다중 탐색 때문에 크기만으로 재현성을 보장하지 않는다. `sell_duration_bars`는 episode 종료 후에만 확정되므로 진입 feature가 아니라 “두 번째 SELL 확인을 기다리는 조건”의 연구 근거로만 해석한다.

## 핵심 질문

### 1. 레벨보다 변화속도가 더 잘 구분하는가?

LEVEL, MOMENTUM, PERSISTENCE의 median absolute effect는 각각 {_fmt(level_effect)}, {_fmt(momentum_effect)}, {_fmt(persistence_effect)}다. 변화속도 feature 일부가 상위권에 올랐더라도 MOMENTUM 전체가 LEVEL보다 우세하다는 증거는 없다. 개별 변화량은 다음 독립 기간에서 재검증해야 한다.

### 2. 1봉 경고보다 2봉 이상 지속성이 더 유효한가?

{_segment_line(features, features['sell_duration_bars'] == 1, 'SELL 1봉 episode')}
{_segment_line(features, features['sell_duration_bars'] >= 2, 'SELL 2봉 이상 episode')}

episode duration은 진입 시점 이후에 확정되는 정보다. 위 비교는 사후 분류이며, 실전 규칙 후보로 평가하려면 두 번째 SELL bar 시점으로 수익률을 다시 anchor해야 한다.

### 3. Chaikin 회복 여부가 false warning을 줄이는가?

{_segment_line(features, features['chaikin_recovery_prior3'] == 1, '직전 3봉 중 Chaikin 회복 있음')}
{_segment_line(features, features['chaikin_recovery_prior3'] == 0, '직전 3봉 중 Chaikin 회복 없음')}

현재 SELL 진입 bar에는 엔진의 recovery conflict 억제가 이미 적용되므로 현재 bar 회복 사례가 없다. 비교 가능한 것은 직전 3봉 회복 흔적뿐이다. 포괄 비교에서 `chaikin_recovery_prior3`의 delta는 {_fmt(chaikin['cliffs_delta_true_minus_false'])}, p={_fmt(chaikin['p_value_two_sided_normal_approx'])}로, 독립적인 억제 규칙을 지지할 정도로 강하지 않다.

### 4. DI spread 확대가 실제 하락군에서 더 강한가?

- 1봉 변화: TRUE_ONLY median {_fmt(di1['true_median'])}, FALSE_ONLY median {_fmt(di1['false_median'])}, delta {_fmt(di1['cliffs_delta_true_minus_false'])}, p={_fmt(di1['p_value_two_sided_normal_approx'])}
- 2봉 변화: TRUE_ONLY median {_fmt(di2['true_median'])}, FALSE_ONLY median {_fmt(di2['false_median'])}, delta {_fmt(di2['cliffs_delta_true_minus_false'])}, p={_fmt(di2['p_value_two_sided_normal_approx'])}

delta의 부호가 양수일 때만 TRUE군에서 확대가 더 강하다. 통계적 보정 후 유의성이 없으므로 방향성은 탐색 결과로만 해석한다.

### 5. CAUTION 지속 후 SELL이 바로 SELL보다 더 정확한가?

{_segment_line(features, features['caution_streak_before'] > 0, '직전 CAUTION 후 SELL')}
{_segment_line(features, features['caution_streak_before'] == 0, 'CAUTION 없이 바로 SELL')}

표본 수 차이가 크고 종목 구성도 통제하지 않았으므로 비율 차이를 인과적으로 해석할 수 없다.

## TRUE_DECLINE 특징

TRUE_ONLY는 SELL 지속시간 중앙값이 5봉으로 FALSE_ONLY 2봉보다 길었고, VWAP spread 중앙값은 -2.01%로 FALSE_ONLY -0.84%보다 더 깊은 역배열이었다. SELL 지속시간의 pairwise 효과 방향은 3/3 종목에서 같았고, VWAP spread의 pooled Cliff 방향은 2/3 종목에서 같았다. 9개의 overlap episode는 장중 MFE +2%와 익일 하락이 함께 발생했으므로 “좋은 경고”와 “오탐”이 배타적인 개념이 아님을 보여준다.

## FALSE_WARNING 특징

FALSE_ONLY는 SELL 지속이 짧고 VWAP 역배열이 상대적으로 얕았다. 반면 2봉 VWAP spread 변화와 1·2봉 DI spread 확대는 TRUE_ONLY보다 오히려 더 약세 방향이었다. 즉 “진입 직전 더 빠르게 악화됐다”는 사실만으로 진짜 하락을 구분하지 못했다. MFE 기준 라벨은 변동성이 큰 종목과 시기를 false-warning으로 더 쉽게 분류할 수 있으므로, ATR 또는 실현변동성 정규화가 필요하다.

## 유효해 보이는 persistence 조건

가장 일관된 후보는 2봉 이상 SELL 지속이었다. 1봉 episode는 TRUE 43.8%/FALSE 75.0%, 2봉 이상은 TRUE 58.3%/FALSE 50.0%였다. 직전 CAUTION이 있었던 경우도 바로 SELL보다 TRUE 비율이 12.7%p 높고 FALSE 비율이 15.8%p 낮았다. 다만 2봉 지속은 사후 정보이고 “바로 SELL” 표본은 평가 가능 7개뿐이므로, 두 번째 SELL 확인 시점부터 forward return을 재계산해야 한다.

## 유효해 보이는 momentum 조건

강한 momentum 후보는 확인되지 않았다. 가장 큰 진입시점 momentum 효과는 2봉 VWAP spread 변화(delta 0.268)였지만 TRUE median +0.010%p, FALSE -0.230%p로 기대와 반대였다. DI spread 확대도 FALSE_ONLY에서 더 컸고, Chaikin 회복 흔적의 효과는 거의 0이었다. 따라서 변화속도 조건을 추가할 근거는 없다.

## 다음 실험 후보

1. 2번째 연속 SELL bar를 새 진입점으로 재-anchor한 확인형 replay와 1봉 진입을 비교한다.
2. TOP momentum feature 1~2개만 사전 고정한 뒤 다른 기간·추가 종목에서 out-of-sample 검증한다.
3. 고정 +2% 대신 종목별 ATR/실현변동성으로 MFE false-warning 라벨을 정규화해 결과의 변동성 편향을 점검한다.

## 방법과 한계

- 입력은 기존 실제 replay `events`와 `episodes` CSV뿐이며 synthetic 행을 추가하지 않았다.
- 각 episode 최초 SELL 시점과 직전 3개 completed 45m event만 feature에 사용했다. 결측/UNKNOWN은 보간하지 않았다.
- 원 단위 VWAP/OBV/Chaikin feature도 출력하지만 종목 간 가격·거래량 규모가 달라 cross-stock TOP 순위에서는 제외했다. 비율, DMI/ADX, count형 feature를 우선했다.
- Mann-Whitney U의 양측 p-value는 tie correction을 포함한 정규근사다. 다중 비교에는 Benjamini-Hochberg FDR을 표시했다.
- 3종목, 52개 평가 가능 episode이며 라벨이 가격 경로로 정의돼 인과관계를 의미하지 않는다. 효과크기 선택 자체가 이 표본에 과적합될 수 있다.
- 본 분석은 연구 산출물만 생성했다. 엔진, threshold, scheduler, renderer, notifier, 주문 경로는 수정하거나 호출하지 않았다.
"""
    return report


def run(artifact_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame, Path]:
    events_path = artifact_dir / "sell_warning_45m_replay_events.csv"
    episodes_path = artifact_dir / "sell_warning_45m_replay_episodes.csv"
    events = pd.read_csv(events_path, dtype={"stock_code": str})
    episodes = pd.read_csv(episodes_path, dtype={"stock_code": str})
    events["stock_code"] = events["stock_code"].str.zfill(6)
    episodes["stock_code"] = episodes["stock_code"].str.zfill(6)

    features = build_episode_features(events, episodes)
    comparison = compare_features(features)
    report = build_report(features, comparison)

    feature_path = artifact_dir / "sell_warning_true_vs_false_analysis.csv"
    comparison_path = artifact_dir / "sell_warning_feature_comparison.csv"
    report_path = artifact_dir / "sell_warning_feature_research_report.md"
    features.to_csv(feature_path, index=False, encoding="utf-8-sig")
    comparison.to_csv(comparison_path, index=False, encoding="utf-8-sig")
    report_path.write_text(report, encoding="utf-8")
    return features, comparison, report_path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research SELL_WARNING true-vs-false features")
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    features, comparison, report_path = run(Path(args.artifact_dir))
    print(f"Episodes analyzed: {len(features)}")
    print(features["analysis_group"].value_counts(dropna=False).to_string())
    print(f"Feature comparisons: {len(comparison)}")
    print(f"Report: {report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
