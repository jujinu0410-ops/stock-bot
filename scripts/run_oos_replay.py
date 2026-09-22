"""OOS Historical Replay and Entry Comparison Harness for SellWarning45mEngine.

Research-only: does not modify engine, threshold, scheduler, notifier, or orders.
Pre-fixes 17 public target stocks (excluding 004960, 267260, 086450).
Generates events, episodes, summary, regime CSVs and a draft report.
"""

from __future__ import annotations

import json
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

from src.analysis.technical_analysis import calculate_wilder_atr
from scripts.replay_sell_warning_45m import (
    build_episodes,
    completed_cutoffs,
    fetch_yfinance_15m,
    realized_45m_bars,
    replay_stock,
)

KST = ZoneInfo("Asia/Seoul")
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts"

# 17 Pre-fixed public target stocks (10 KOSPI, 7 KOSDAQ)
# Excludes the original 3 research stocks: 004960 (한신공영), 267260 (HD현대일렉트릭), 086450 (동국제약)
TARGET_STOCKS: Tuple[Tuple[str, str, str], ...] = (
    # KOSPI (10)
    ("005930", "삼성전자", "KOSPI"),
    ("000660", "SK하이닉스", "KOSPI"),
    ("005380", "현대차", "KOSPI"),
    ("000270", "기아", "KOSPI"),
    ("005490", "POSCO홀딩스", "KOSPI"),
    ("035420", "NAVER", "KOSPI"),
    ("035720", "카카오", "KOSPI"),
    ("051910", "LG화학", "KOSPI"),
    ("207940", "삼성바이오로직스", "KOSPI"),
    ("105560", "KB금융", "KOSPI"),
    # KOSDAQ (7)
    ("247540", "에코프로비엠", "KOSDAQ"),
    ("086520", "에코프로", "KOSDAQ"),
    ("196170", "알테오젠", "KOSDAQ"),
    ("028300", "HLB", "KOSDAQ"),
    ("141080", "리가켐바이오", "KOSDAQ"),
    ("000250", "삼천당제약", "KOSDAQ"),
    ("068760", "셀트리온제약", "KOSDAQ"),
)

OUTCOME_COLUMNS = (
    "return_next_45m_pct",
    "return_next_90m_pct",
    "return_day_close_pct",
    "return_next_open_pct",
    "return_next_close_pct",
    "mae_1d_pct",
    "mfe_1d_pct",
)


def _number(value: Any) -> float:
    try:
        val = float(value)
        return val if math.isfinite(val) else math.nan
    except (TypeError, ValueError):
        return math.nan


def _pct(value: float, base: float) -> float:
    if not math.isfinite(value) or not math.isfinite(base) or base == 0:
        return math.nan
    return (value / base - 1.0) * 100.0


def fetch_daily_bars(ticker: str, period: str = "2y") -> Optional[pd.DataFrame]:
    try:
        frame = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
        if frame is None or frame.empty or not {"High", "Low", "Close"}.issubset(frame.columns):
            return None
        frame = frame.loc[:, ["Open", "High", "Low", "Close", "Volume"]].dropna().copy()
        index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="raise"))
        if index.tz is not None:
            index = index.tz_convert(KST).tz_localize(None)
        frame.index = index.normalize()
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        # Compute 20-day SMA for trend regime
        frame["ma20"] = frame["Close"].rolling(window=20).mean()
        return frame
    except Exception:
        return None


def get_atr_and_regime_at_timestamp(
    daily_frame: Optional[pd.DataFrame],
    timestamp_str: str,
    entry_price: float,
) -> Dict[str, Any]:
    res = {
        "atr14": math.nan,
        "atr_pct": math.nan,
        "trend_regime_20d": "UNKNOWN",
    }
    if daily_frame is None or daily_frame.empty:
        return res
    ts = pd.Timestamp(timestamp_str)
    completed = daily_frame.loc[daily_frame.index < ts.normalize()].copy()
    if len(completed) < 20:
        return res
    
    # Compute ATR14
    canonical = pd.DataFrame({
        "high_price": completed["High"].astype(float),
        "low_price": completed["Low"].astype(float),
        "close_price": completed["Close"].astype(float),
    }, index=completed.index)
    atr_series = calculate_wilder_atr(canonical, period=14)
    atr14 = _number(atr_series.iloc[-1])
    if math.isfinite(atr14) and atr14 > 0 and entry_price > 0:
        res["atr14"] = atr14
        res["atr_pct"] = (atr14 / entry_price) * 100.0
    
    # 20-day MA Trend regime
    last_close = float(completed["Close"].iloc[-1])
    last_ma20 = float(completed["ma20"].iloc[-1])
    if math.isfinite(last_close) and math.isfinite(last_ma20):
        res["trend_regime_20d"] = "BULL" if last_close >= last_ma20 else "BEAR"

    return res


def build_entry_comparison_table(
    events: pd.DataFrame,
    episodes: pd.DataFrame,
    daily_frames: Dict[str, Optional[pd.DataFrame]],
) -> pd.DataFrame:
    sell_episodes = episodes.loc[
        (episodes["variant"] == "CURRENT") & (episodes["state"] == "SELL_WARNING")
    ].sort_values(["stock_code", "start_timestamp"]).copy()

    event_groups = {
        str(code).zfill(6): group.sort_values("bar_timestamp").reset_index(drop=True)
        for code, group in events.groupby("stock_code", sort=False)
    }

    rows: List[Dict[str, Any]] = []
    for _, ep in sell_episodes.iterrows():
        code = str(ep["stock_code"]).zfill(6)
        group = event_groups.get(code)
        if group is None:
            continue
        pos_list = group.index[group["bar_timestamp"] == ep["start_timestamp"]].tolist()
        if len(pos_list) != 1:
            continue
        pos = pos_list[0]
        first = group.iloc[pos]
        second = group.iloc[pos + 1] if pos + 1 < len(group) else None

        has_second = bool(
            second is not None
            and str(second.get("state")) == "SELL_WARNING"
            and pd.Timestamp(second.get("bar_timestamp")) <= pd.Timestamp(ep["end_timestamp"])
        )

        first_close = _number(first["close"])
        second_close = _number(second["close"]) if has_second else math.nan

        first_meta = get_atr_and_regime_at_timestamp(
            daily_frames.get(code), first["bar_timestamp"], first_close
        )
        second_meta = (
            get_atr_and_regime_at_timestamp(daily_frames.get(code), second["bar_timestamp"], second_close)
            if has_second
            else {"atr14": math.nan, "atr_pct": math.nan, "trend_regime_20d": "UNKNOWN"}
        )

        row: Dict[str, Any] = {
            "stock_code": code,
            "stock_name": ep["stock_name"],
            "episode_start": ep["start_timestamp"],
            "episode_end": ep["end_timestamp"],
            "episode_duration_bars": int(ep["duration_bars"]),
            "second_entry_exists": int(has_second),
            "first_entry_timestamp": first["bar_timestamp"],
            "first_entry_close": first_close,
            "second_entry_timestamp": second["bar_timestamp"] if has_second else None,
            "second_entry_close": second_close,
            "trend_regime_20d": first_meta["trend_regime_20d"],
            "adx_1st": _number(first.get("adx")),
            "adx_regime": "HIGH_TREND" if _number(first.get("adx")) >= 25.0 else "LOW_CHOP",
        }

        # Outcomes
        for col in OUTCOME_COLUMNS:
            row[f"first_{col}"] = _number(first.get(col))
            row[f"second_{col}"] = _number(second.get(col)) if has_second else math.nan

        # Delay cost
        delay = _pct(second_close, first_close) if has_second else math.nan
        row["delay_close_return_pct"] = delay

        # True decline flag (return_next_close_pct < 0)
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

        # ATR false warning flags
        for prefix, meta in (("first", first_meta), ("second", second_meta)):
            atr_pct = meta["atr_pct"]
            mfe = row[f"{prefix}_mfe_1d_pct"]
            row[f"{prefix}_atr14"] = meta["atr14"]
            row[f"{prefix}_atr_pct"] = atr_pct
            # F0: MFE >= 2.0%
            row[f"{prefix}_f0_false"] = int(mfe >= 2.0) if math.isfinite(mfe) else None
            # F1: MFE >= 0.8 * ATR
            row[f"{prefix}_f1_false"] = (
                int(mfe >= 0.8 * atr_pct)
                if math.isfinite(mfe) and math.isfinite(atr_pct) and atr_pct > 0
                else None
            )
            # F2: MFE >= 1.0 * ATR
            row[f"{prefix}_f2_false"] = (
                int(mfe >= 1.0 * atr_pct)
                if math.isfinite(mfe) and math.isfinite(atr_pct) and atr_pct > 0
                else None
            )

        rows.append(row)

    return pd.DataFrame(rows)


def compute_metrics(df: pd.DataFrame, prefix: str) -> Dict[str, Any]:
    valid = df if prefix == "first" else df.loc[df["second_entry_exists"] == 1]
    count = len(valid)
    if count == 0:
        return {
            "count": 0,
            "next_45m_neg_pct": math.nan,
            "day_close_neg_pct": math.nan,
            "next_close_neg_pct": math.nan,
            "avg_mae_1d_pct": math.nan,
            "avg_mfe_1d_pct": math.nan,
            "f0_false_pct": math.nan,
            "f1_false_pct": math.nan,
            "f2_false_pct": math.nan,
        }

    n45 = valid[f"{prefix}_return_next_45m_pct"].dropna()
    dc = valid[f"{prefix}_return_day_close_pct"].dropna()
    nc = valid[f"{prefix}_return_next_close_pct"].dropna()
    mae = valid[f"{prefix}_mae_1d_pct"].dropna()
    mfe = valid[f"{prefix}_mfe_1d_pct"].dropna()
    f0 = valid[f"{prefix}_f0_false"].dropna()
    f1 = valid[f"{prefix}_f1_false"].dropna()
    f2 = valid[f"{prefix}_f2_false"].dropna()

    return {
        "count": count,
        "next_45m_neg_pct": float((n45 < 0).mean() * 100.0) if not n45.empty else math.nan,
        "day_close_neg_pct": float((dc < 0).mean() * 100.0) if not dc.empty else math.nan,
        "next_close_neg_pct": float((nc < 0).mean() * 100.0) if not nc.empty else math.nan,
        "avg_mae_1d_pct": float(mae.mean()) if not mae.empty else math.nan,
        "avg_mfe_1d_pct": float(mfe.mean()) if not mfe.empty else math.nan,
        "f0_false_pct": float(f0.mean() * 100.0) if not f0.empty else math.nan,
        "f1_false_pct": float(f1.mean() * 100.0) if not f1.empty else math.nan,
        "f2_false_pct": float(f2.mean() * 100.0) if not f2.empty else math.nan,
    }


def build_summary_csv(comparison: pd.DataFrame, stock_meta: Dict[str, Tuple[str, str]]) -> pd.DataFrame:
    rows = []
    codes = list(comparison["stock_code"].unique()) + ["ALL"]

    for code in codes:
        sub = comparison if code == "ALL" else comparison.loc[comparison["stock_code"] == code]
        name = "전체 (ALL)" if code == "ALL" else stock_meta.get(code, (code, "UNKNOWN"))[0]
        exchange = "ALL" if code == "ALL" else stock_meta.get(code, ("UNKNOWN", "UNKNOWN"))[1]

        first_m = compute_metrics(sub, "first")
        second_m = compute_metrics(sub, "second")

        persistent = sub.loc[sub["second_entry_exists"] == 1]
        delays = persistent["delay_close_return_pct"].dropna()
        avg_delay = float(delays.mean()) if not delays.empty else math.nan

        # Missed true declines: 1st had next close < 0, but 2nd did NOT exist
        missed_declines = int(
            ((sub["second_entry_exists"] == 0) & (sub["first_true_decline"] == 1)).sum()
        )

        rows.append({
            "stock_code": code,
            "stock_name": name,
            "exchange": exchange,
            "first_sell_count": first_m["count"],
            "second_consecutive_sell_count": second_m["count"],
            "retention_rate_pct": (second_m["count"] / first_m["count"] * 100.0) if first_m["count"] > 0 else math.nan,
            # 1st SELL outcomes
            "first_next_45m_neg_pct": first_m["next_45m_neg_pct"],
            "first_day_close_neg_pct": first_m["day_close_neg_pct"],
            "first_next_close_neg_pct": first_m["next_close_neg_pct"],
            "first_avg_mae_1d_pct": first_m["avg_mae_1d_pct"],
            "first_avg_mfe_1d_pct": first_m["avg_mfe_1d_pct"],
            "first_f0_false_pct": first_m["f0_false_pct"],
            "first_f1_false_pct": first_m["f1_false_pct"],
            "first_f2_false_pct": first_m["f2_false_pct"],
            # 2nd consecutive SELL outcomes
            "second_next_45m_neg_pct": second_m["next_45m_neg_pct"],
            "second_day_close_neg_pct": second_m["day_close_neg_pct"],
            "second_next_close_neg_pct": second_m["next_close_neg_pct"],
            "second_avg_mae_1d_pct": second_m["avg_mae_1d_pct"],
            "second_avg_mfe_1d_pct": second_m["avg_mfe_1d_pct"],
            "second_f0_false_pct": second_m["f0_false_pct"],
            "second_f1_false_pct": second_m["f1_false_pct"],
            "second_f2_false_pct": second_m["f2_false_pct"],
            # Trade-offs
            "avg_delay_return_pct": avg_delay,
            "missed_true_declines_count": missed_declines,
        })

    return pd.DataFrame(rows)


def build_regime_csv(comparison: pd.DataFrame, stock_meta: Dict[str, Tuple[str, str]]) -> pd.DataFrame:
    rows = []
    # Add exchange to comparison frame
    comp = comparison.copy()
    comp["exchange"] = comp["stock_code"].map(lambda c: stock_meta.get(c, ("UNKNOWN", "UNKNOWN"))[1])

    regimes_to_evaluate = [
        ("EXCHANGE", "KOSPI", comp["exchange"] == "KOSPI"),
        ("EXCHANGE", "KOSDAQ", comp["exchange"] == "KOSDAQ"),
        ("TREND_20D_MA", "BULL (Close >= 20d MA)", comp["trend_regime_20d"] == "BULL"),
        ("TREND_20D_MA", "BEAR (Close < 20d MA)", comp["trend_regime_20d"] == "BEAR"),
        ("VOLATILITY_ADX", "HIGH_TREND (ADX >= 25)", comp["adx_regime"] == "HIGH_TREND"),
        ("VOLATILITY_ADX", "LOW_CHOP (ADX < 25)", comp["adx_regime"] == "LOW_CHOP"),
        ("COMBINED_REGIME", "BEAR_MARKET + HIGH_ADX", (comp["trend_regime_20d"] == "BEAR") & (comp["adx_regime"] == "HIGH_TREND")),
        ("COMBINED_REGIME", "BULL_MARKET + LOW_ADX", (comp["trend_regime_20d"] == "BULL") & (comp["adx_regime"] == "LOW_CHOP")),
    ]

    for category, name, mask in regimes_to_evaluate:
        sub = comp.loc[mask]
        first_m = compute_metrics(sub, "first")
        second_m = compute_metrics(sub, "second")

        persistent = sub.loc[sub["second_entry_exists"] == 1]
        delays = persistent["delay_close_return_pct"].dropna()
        avg_delay = float(delays.mean()) if not delays.empty else math.nan

        missed_declines = int(
            ((sub["second_entry_exists"] == 0) & (sub["first_true_decline"] == 1)).sum()
        )

        rows.append({
            "regime_category": category,
            "regime_name": name,
            "first_sell_count": first_m["count"],
            "second_consecutive_sell_count": second_m["count"],
            "retention_rate_pct": (second_m["count"] / first_m["count"] * 100.0) if first_m["count"] > 0 else math.nan,
            # 1st SELL
            "first_next_close_neg_pct": first_m["next_close_neg_pct"],
            "first_avg_mae_1d_pct": first_m["avg_mae_1d_pct"],
            "first_avg_mfe_1d_pct": first_m["avg_mfe_1d_pct"],
            "first_f0_false_pct": first_m["f0_false_pct"],
            "first_f1_false_pct": first_m["f1_false_pct"],
            "first_f2_false_pct": first_m["f2_false_pct"],
            # 2nd consecutive SELL
            "second_next_close_neg_pct": second_m["next_close_neg_pct"],
            "second_avg_mae_1d_pct": second_m["avg_mae_1d_pct"],
            "second_avg_mfe_1d_pct": second_m["avg_mfe_1d_pct"],
            "second_f0_false_pct": second_m["f0_false_pct"],
            "second_f1_false_pct": second_m["f1_false_pct"],
            "second_f2_false_pct": second_m["f2_false_pct"],
            # Trade-off
            "avg_delay_return_pct": avg_delay,
            "missed_true_declines_count": missed_declines,
        })

    return pd.DataFrame(rows)


def build_draft_report(
    summary_df: pd.DataFrame,
    regime_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    events_df: pd.DataFrame,
    episodes_df: pd.DataFrame,
) -> str:
    all_row = summary_df.loc[summary_df["stock_code"] == "ALL"].iloc[0]
    total_events = len(events_df)
    total_episodes = len(episodes_df.loc[episodes_df["variant"] == "CURRENT"])
    first_count = int(all_row["first_sell_count"])
    second_count = int(all_row["second_consecutive_sell_count"])

    report = f"""# Out-Of-Sample (OOS) 17종목 SellWarning45mEngine 데이터 생성 및 1st vs 2nd 비교 보고서 (초안)

## 1. 개요 및 연구 조건

- **목적**: 기존 3개 연구 종목(한신공영, HD현대일렉트릭, 동국제약)을 완전 배제하고, 사전 고정된 17개 대표 공개 종목에 대해 최근 60거래일 동안의 45분봉 `SellWarning45mEngine` Replay 및 1st SELL vs 2nd Consecutive SELL의 비교 통계 데이터셋 생성
- **대상 종목 (17개 사전 고정)**:
  - **KOSPI (10개)**: 삼성전자, SK하이닉스, 현대차, 기아, POSCO홀딩스, NAVER, 카카오, LG화학, 삼성바이오로직스, KB금융
  - **KOSDAQ (7개)**: 에코프로비엠, 에코프로, 알테오젠, HLB, 리가켐바이오, 삼천당제약, 셀트리온제약
  - **제외 종목**: 한신공영(004960), HD현대일렉트릭(267260), 동국제약(086450)
- **기간**: 최근 60거래일 (`period="60d"`, 2026-06-29 ~ 2026-09-22)
- **비변경 원칙**:
  - 엔진 판정식 및 임계값(Threshold) 일체 수정 없음
  - 결과 해석, 규칙 변경, 임의 추천 배제
  - 순수 사실 기반 통계 및 산출물 테이블 제시

---

## 2. 데이터 표본 및 집계 현황

- **총 생성 45분 완성 평가 Event**: {total_events:,} 건
- **총 유효 상태 Episode (CURRENT 기준)**: {total_episodes:,} 건
- **총 1st SELL Episode 수**: {first_count:,} 건
- **2nd Consecutive SELL 도달 Episode 수**: {second_count:,} 건 ({all_row['retention_rate_pct']:.2f}% 유지)
- **1회성 해제 Episode (단 1봉만 발생 후 해제)**: {first_count - second_count:,} 건 ({100.0 - all_row['retention_rate_pct']:.2f}%)

---

## 3. 전체 요약: 1st SELL vs 2nd Consecutive SELL 비교

| 지표 구분 | 1st SELL (첫 신호 진입) | 2nd Consecutive SELL (2회 연속 확인 진입) | 차이 (2nd - 1st) |
| :--- | :---: | :---: | :---: |
| **총 발생 건수** | {first_count} 건 | {second_count} 건 | -{first_count - second_count} 건 |
| **다음 45분 하락률 (< 0)** | {all_row['first_next_45m_neg_pct']:.2f}% | {all_row['second_next_45m_neg_pct']:.2f}% | {all_row['second_next_45m_neg_pct'] - all_row['first_next_45m_neg_pct']:+.2f}%p |
| **당일 종가 하락률 (< 0)** | {all_row['first_day_close_neg_pct']:.2f}% | {all_row['second_day_close_neg_pct']:.2f}% | {all_row['second_day_close_neg_pct'] - all_row['first_day_close_neg_pct']:+.2f}%p |
| **익일 종가 하락률 (< 0)** | {all_row['first_next_close_neg_pct']:.2f}% | {all_row['second_next_close_neg_pct']:.2f}% | {all_row['second_next_close_neg_pct'] - all_row['first_next_close_neg_pct']:+.2f}%p |
| **평균 MAE (최대 불리 이동)** | {all_row['first_avg_mae_1d_pct']:.2f}% | {all_row['second_avg_mae_1d_pct']:.2f}% | {all_row['second_avg_mae_1d_pct'] - all_row['first_avg_mae_1d_pct']:+.2f}%p |
| **평균 MFE (최대 유리 역행)** | +{all_row['first_avg_mfe_1d_pct']:.2f}% | +{all_row['second_avg_mfe_1d_pct']:.2f}% | {all_row['second_avg_mfe_1d_pct'] - all_row['first_avg_mfe_1d_pct']:+.2f}%p |
| **F0 False Warning (MFE >= 2.0%)** | {all_row['first_f0_false_pct']:.2f}% | {all_row['second_f0_false_pct']:.2f}% | {all_row['second_f0_false_pct'] - all_row['first_f0_false_pct']:+.2f}%p |
| **F1 False Warning (MFE >= 0.8*ATR)** | {all_row['first_f1_false_pct']:.2f}% | {all_row['second_f1_false_pct']:.2f}% | {all_row['second_f1_false_pct'] - all_row['first_f1_false_pct']:+.2f}%p |
| **F2 False Warning (MFE >= 1.0*ATR)** | {all_row['first_f2_false_pct']:.2f}% | {all_row['second_f2_false_pct']:.2f}% | {all_row['second_f2_false_pct'] - all_row['first_f2_false_pct']:+.2f}%p |
| **평균 지연 비용 (Delay Return)** | - | {all_row['avg_delay_return_pct']:+.2f}% | - |
| **2nd 부재로 놓친 실제 하락 수** | - | {int(all_row['missed_true_declines_count'])} 건 | - |

---

## 4. 국면별(Regime) 비교 통계

```csv
{regime_df.to_csv(index=False)}
```

---

## 5. 종목별 세부 요약

```csv
{summary_df.to_csv(index=False)}
```

---

## 6. 산출물 파일 목록

1. `artifacts/sell_warning_oos_events.csv`: 전 종목 45분봉 평가 전체 Event 데이터셋
2. `artifacts/sell_warning_oos_episodes.csv`: 동일 상태 연속성 기반 Episode 데이터셋
3. `artifacts/sell_warning_oos_summary.csv`: 17개 종목별 및 전체 1st vs 2nd SELL 비교 요약표
4. `artifacts/sell_warning_oos_regime.csv`: 시장 국면(시장 구분, 20일선 추세, ADX 강도 등)별 비교표
5. `artifacts/sell_warning_oos_replay_report.md`: 본 보고서 초안
"""
    return report


def main():
    print(f"Starting OOS Replay on {len(TARGET_STOCKS)} pre-fixed target stocks...")
    artifact_dir = DEFAULT_ARTIFACT_DIR
    artifact_dir.mkdir(parents=True, exist_ok=True)

    stock_meta = {code: (name, ex) for code, name, ex in TARGET_STOCKS}
    daily_frames: Dict[str, Optional[pd.DataFrame]] = {}

    event_frames: List[pd.DataFrame] = []

    for code, name, ex in TARGET_STOCKS:
        suffix = "KQ" if ex == "KOSDAQ" else "KS"
        ticker = f"{code}.{suffix}"
        print(f"Fetching 15m & daily data for {code} {name} ({ticker})...")
        
        # 1. Fetch 15m intraday
        fetched = fetch_yfinance_15m(code, name, period="60d")
        if fetched.frame is None or fetched.frame.empty:
            print(f"  Warning: No 15m data for {code}")
            continue

        # 2. Fetch daily
        daily = fetch_daily_bars(ticker, period="2y")
        daily_frames[code] = daily

        # 3. Replay stock
        events = replay_stock(code, name, fetched.frame, fetched.source)
        if not events.empty:
            event_frames.append(events)
            print(f"  Generated {len(events)} 45m events.")

    if not event_frames:
        print("ERROR: No events generated.")
        return 1

    events_df = pd.concat(event_frames, ignore_index=True)
    events_df = events_df.sort_values(["stock_code", "bar_timestamp"]).reset_index(drop=True)

    # Build episodes
    episodes_current = build_episodes(events_df, "state", "CURRENT")
    episodes_strict = build_episodes(events_df, "state_obv_strict", "OBV_STRICT")
    episodes_df = pd.concat([episodes_current, episodes_strict], ignore_index=True)

    # Build entry comparison
    comparison_df = build_entry_comparison_table(events_df, episodes_df, daily_frames)

    # Build summary and regime frames
    summary_df = build_summary_csv(comparison_df, stock_meta)
    regime_df = build_regime_csv(comparison_df, stock_meta)

    # Save 4 CSV artifacts
    events_path = artifact_dir / "sell_warning_oos_events.csv"
    episodes_path = artifact_dir / "sell_warning_oos_episodes.csv"
    summary_path = artifact_dir / "sell_warning_oos_summary.csv"
    regime_path = artifact_dir / "sell_warning_oos_regime.csv"
    report_path = artifact_dir / "sell_warning_oos_replay_report.md"

    print("Saving CSV artifacts...")
    events_df.to_csv(events_path, index=False, encoding="utf-8-sig")
    episodes_df.to_csv(episodes_path, index=False, encoding="utf-8-sig")
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    regime_df.to_csv(regime_path, index=False, encoding="utf-8-sig")

    # Build draft report
    print("Generating draft report...")
    report_text = build_draft_report(summary_df, regime_df, comparison_df, events_df, episodes_df)
    report_path.write_text(report_text, encoding="utf-8")

    print(f"\nOOS Replay completed successfully!")
    print(f"  Events: {len(events_df):,} rows -> {events_path}")
    print(f"  Episodes: {len(episodes_df):,} rows -> {episodes_path}")
    print(f"  Summary: {len(summary_df):,} rows -> {summary_path}")
    print(f"  Regime: {len(regime_df):,} rows -> {regime_path}")
    print(f"  Draft Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
