from __future__ import annotations

import math
import sys
from typing import Any, Dict, Optional

import pandas as pd

from daily_v8.v8_runner import resolve_v8_engine_path

BB_WINDOW = 26
BB_STD_MULTIPLIER = 2.0
BREACH_TICKS = 3
REGIME_THRESHOLD_PCT = 5.0


def _to_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _tick_unit(price: float) -> int:
    p = max(float(price), 0.0)
    if p < 2000:
        return 1
    if p < 5000:
        return 5
    if p < 20000:
        return 10
    if p < 50000:
        return 50
    if p < 200000:
        return 100
    if p < 500000:
        return 500
    return 1000


def _round_outward(price: float, direction: str) -> int:
    unit = _tick_unit(price)
    if direction == "up":
        return int(math.ceil(float(price) / unit) * unit)
    return int(math.floor(float(price) / unit) * unit)


def _outer_tick_trigger(price: float, direction: str, ticks: int = BREACH_TICKS) -> int:
    value = _round_outward(price, direction)
    for _ in range(max(0, int(ticks))):
        if direction == "up":
            value += _tick_unit(value + 1e-6)
        else:
            value -= _tick_unit(max(value - 1e-6, 0.0))
    return int(value)


def _atr14_series(frame: pd.DataFrame) -> pd.Series:
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    close = frame["Close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(14, min_periods=14).mean()


def _change_pct(series: pd.Series, bars_back: int = 5) -> Optional[float]:
    clean = series.dropna()
    if len(clean) <= bars_back:
        return None
    now = float(clean.iloc[-1])
    before = float(clean.iloc[-1 - bars_back])
    if before == 0:
        return None
    return (now / before - 1.0) * 100.0


def _regime(atr_change: Optional[float], width_change: Optional[float]) -> str:
    if atr_change is None or width_change is None:
        return "INSUFFICIENT_HISTORY"
    threshold = REGIME_THRESHOLD_PCT
    if atr_change >= threshold and width_change >= threshold:
        return "EXPANDING_ALIGNED"
    if atr_change <= -threshold and width_change <= -threshold:
        return "CONTRACTING_ALIGNED"
    if (atr_change >= threshold and width_change <= -threshold) or (
        atr_change <= -threshold and width_change >= threshold
    ):
        return "DIVERGENT"
    return "STABLE_OR_MIXED"


def compute_bollinger_metrics(frame: pd.DataFrame, *, label: str) -> Dict[str, Any]:
    if frame is None or frame.empty or len(frame) < BB_WINDOW:
        return {
            "label": label,
            "status": "INSUFFICIENT_BARS",
            "row_count": 0 if frame is None else int(len(frame)),
            "bb_window": BB_WINDOW,
            "bb_std_multiplier": BB_STD_MULTIPLIER,
        }

    ordered = frame.sort_index().copy()
    close = ordered["Close"].astype(float)
    middle = close.rolling(BB_WINDOW).mean()
    std = close.rolling(BB_WINDOW).std()
    upper = middle + BB_STD_MULTIPLIER * std
    lower = middle - BB_STD_MULTIPLIER * std
    width = upper - lower
    atr14 = _atr14_series(ordered)

    last_close = float(close.iloc[-1])
    last_middle = float(middle.iloc[-1])
    last_upper = float(upper.iloc[-1])
    last_lower = float(lower.iloc[-1])
    last_width = float(width.iloc[-1])
    latest_atr = _to_float(atr14.iloc[-1])

    upper_trigger = _outer_tick_trigger(last_upper, "up", BREACH_TICKS)
    lower_trigger = _outer_tick_trigger(last_lower, "down", BREACH_TICKS)
    if last_close >= upper_trigger:
        signal = "UPPER_BREAK_3TICK"
    elif last_close <= lower_trigger:
        signal = "LOWER_BREAK_3TICK"
    else:
        signal = "INSIDE_OR_UNCONFIRMED"

    width_pct = (last_width / last_middle * 100.0) if last_middle else None
    position_pct = (
        (last_close - last_lower) / last_width * 100.0 if last_width > 0 else None
    )
    atr_pct = (latest_atr / last_close * 100.0) if latest_atr and last_close else None
    atr_change = _change_pct(atr14, 5)
    width_change = _change_pct(width, 5)

    return {
        "label": label,
        "status": "READY",
        "row_count": int(len(ordered)),
        "asof": str(ordered.index[-1]),
        "close": last_close,
        "bb_window": BB_WINDOW,
        "bb_std_multiplier": BB_STD_MULTIPLIER,
        "bb_middle": last_middle,
        "bb_upper": last_upper,
        "bb_lower": last_lower,
        "bb_width": last_width,
        "bb_width_pct": width_pct,
        "bb_position_pct": position_pct,
        "upper_3tick_trigger": upper_trigger,
        "lower_3tick_trigger": lower_trigger,
        "signal": signal,
        "atr14_local": latest_atr,
        "atr_pct_local": atr_pct,
        "atr14_change_5bars_pct": atr_change,
        "bb_width_change_5bars_pct": width_change,
        "volatility_regime": _regime(atr_change, width_change),
    }


def _daily_frame_from_evidence(evidence: Dict[str, Any]) -> pd.DataFrame:
    candles = ((evidence.get("market") or {}).get("candles") or [])
    rows = []
    for candle in candles:
        stamp = pd.to_datetime(str(candle.get("date") or ""), errors="coerce")
        if pd.isna(stamp):
            continue
        values = {
            "Open": _to_float(candle.get("open")),
            "High": _to_float(candle.get("high")),
            "Low": _to_float(candle.get("low")),
            "Close": _to_float(candle.get("close")),
            "Volume": _to_float(candle.get("volume")),
        }
        if any(values[key] is None for key in ("Open", "High", "Low", "Close")):
            continue
        rows.append({"Date": stamp, **values})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("Date").sort_index()


def _intraday_45m_frame(stock_code: str) -> tuple[pd.DataFrame, str, str]:
    try:
        from src.analysis.intraday_analysis import Intraday45mAnalyzer
    except Exception as exc:
        return pd.DataFrame(), "NONE", f"IMPORT_FAILED:{type(exc).__name__}"

    analyzer = Intraday45mAnalyzer()
    try:
        frame_15m, source, error = analyzer.fetch_canonical_15m_data(str(stock_code))
    except Exception as exc:
        return pd.DataFrame(), "NONE", f"FETCH_FAILED:{type(exc).__name__}"
    if frame_15m is None or frame_15m.empty:
        return pd.DataFrame(), str(source or "NONE"), str(error or "NO_INTRADAY_DATA")

    frame = frame_15m.copy()
    needed = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in frame.columns for column in needed):
        return pd.DataFrame(), str(source or "NONE"), "MISSING_OHLCV_COLUMNS"
    frame_45m = frame[needed].resample("45min").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Open", "High", "Low", "Close"])
    return frame_45m, str(source or "NONE"), str(error or "NONE")


def install_v8_bollinger_20_patch() -> None:
    """Special-job-only override: make the legacy V8 T-score use BB(26, 2.0).

    The pinned private V8 repository stays immutable. Only the portfolio-special
    process replaces the already-computed 1.7-sigma columns before signal scoring.
    """

    v8_path = str(resolve_v8_engine_path())
    if v8_path not in sys.path:
        sys.path.insert(0, v8_path)
    from src.analysis import technical_analysis as ta

    current = ta.TechnicalAnalysis.calculate_indicators
    if getattr(current, "_portfolio_special_bb20", False):
        return
    original = current

    def calculate_indicators_bb20(self):
        frame = original(self)
        if "close_price" in frame.columns and len(frame) >= BB_WINDOW:
            close = frame["close_price"].astype(float)
            middle = close.rolling(BB_WINDOW).mean()
            std = close.rolling(BB_WINDOW).std()
            frame["bb_middle"] = middle
            frame["bb_upper"] = middle + BB_STD_MULTIPLIER * std
            frame["bb_lower"] = middle - BB_STD_MULTIPLIER * std
        return frame

    calculate_indicators_bb20._portfolio_special_bb20 = True
    calculate_indicators_bb20._legacy_original = original
    ta.TechnicalAnalysis.calculate_indicators = calculate_indicators_bb20


def _price_relation(value: Optional[float], reference: Optional[float]) -> str:
    if value is None or reference is None:
        return "MISSING"
    if abs(value - reference) < 1e-9:
        return "EQUAL"
    return "ABOVE" if value > reference else "BELOW"


def build_bollinger_atr_overlay(
    *,
    stock_code: str,
    holding: Dict[str, Any],
    evidence: Dict[str, Any],
    strategy: object,
    intraday_frame: Optional[pd.DataFrame] = None,
    intraday_source: Optional[str] = None,
) -> Dict[str, Any]:
    daily = compute_bollinger_metrics(_daily_frame_from_evidence(evidence), label="DAILY")

    source = intraday_source or "TEST_FRAME"
    error = "NONE"
    frame_45m = intraday_frame
    if frame_45m is None:
        frame_45m, source, error = _intraday_45m_frame(stock_code)
    intraday = compute_bollinger_metrics(frame_45m, label="45M")
    intraday["source"] = source
    intraday["source_error"] = error

    avg = _to_float(holding.get("avg_buy_price"))
    current = _to_float(getattr(strategy, "current_price", None))
    pnl_pct = ((current / avg) - 1.0) * 100.0 if avg and current and avg > 0 else None

    target1 = _to_float(getattr(strategy, "target_1", None))
    target2 = _to_float(getattr(strategy, "target_2", None))
    stop = _to_float(getattr(strategy, "stop_price", None))
    trailing = _to_float(getattr(strategy, "trailing_distance", None))
    daily_upper = _to_float(daily.get("bb_upper"))
    daily_lower = _to_float(daily.get("bb_lower"))

    upper_vs_t1 = _price_relation(daily_upper, target1)
    upper_vs_t2 = _price_relation(daily_upper, target2)
    lower_vs_stop = _price_relation(daily_lower, stop)

    daily_signal = str(daily.get("signal") or "")
    intraday_signal = str(intraday.get("signal") or "")
    upper_cross = "UPPER_BREAK_3TICK" in {daily_signal, intraday_signal}
    lower_cross = "LOWER_BREAK_3TICK" in {daily_signal, intraday_signal}

    if upper_cross and lower_cross:
        holder_action = "TIMEFRAME_CONFLICT_REVIEW"
    elif lower_cross:
        holder_action = "LOSS_MINIMIZATION" if (pnl_pct is not None and pnl_pct < 0) else "TREND_STOP_PROTECT"
    elif upper_cross:
        holder_action = "RECOVERY_TRAILING" if (pnl_pct is not None and pnl_pct < 0) else "PROFIT_TRAILING"
    else:
        holder_action = "NO_BB_TRIGGER"

    if daily_upper is None or target1 is None:
        upper_guidance = "MISSING"
    elif daily_upper <= target1:
        upper_guidance = (
            "BB_UPPER_FIRST: BB 상단이 ATR 1차 목표보다 가깝다. 상단 3호가 돌파 시 고정 익절보다 "
            "트레일링 전환을 우선 검토한다."
        )
    elif target2 is not None and daily_upper <= target2:
        upper_guidance = (
            "CONVERGENCE_ZONE: BB 상단이 ATR 1차와 2차 목표 사이에 있다. ATR 1차 구간부터 "
            "일부 이익보호 후 상단 돌파 시 잔여분 트레일링을 검토한다."
        )
    else:
        upper_guidance = (
            "ATR_TARGET_FIRST: ATR 목표가 BB 상단보다 먼저 온다. ATR 목표 도달 시 일부 이익보호를 "
            "검토하고, 잔여분은 BB 상단/돌파 여부로 추세를 연장 평가한다."
        )

    if daily_lower is None or stop is None:
        lower_guidance = "MISSING"
    elif daily_lower >= stop:
        lower_guidance = (
            "BB_LOWER_FIRST: BB 하단이 ATR 손절선보다 높다. 하단 3호가 이탈은 ATR 손절보다 먼저 오는 "
            "추세 훼손 신호로 취급한다."
        )
    else:
        lower_guidance = (
            "ATR_STOP_FIRST: ATR 손절선이 BB 하단보다 높다. ATR 방어선이 우선이며, BB 하단 이탈은 "
            "추가적인 추세 훼손 확인 신호다."
        )

    provisional_daily_trail = (
        current - trailing if upper_cross and current is not None and trailing is not None else None
    )
    intraday_atr = _to_float(intraday.get("atr14_local"))
    intraday_close = _to_float(intraday.get("close"))
    provisional_45m_trail = (
        intraday_close - 0.8 * intraday_atr
        if upper_cross and intraday_close is not None and intraday_atr is not None
        else None
    )

    return {
        "canonical_rule": "BB(26,2.0), 종가 기준 3호가 바깥 돌파 확인",
        "daily": daily,
        "intraday_45m": intraday,
        "strategy_atr": {
            "atr14": _to_float(getattr(strategy, "atr14", None)),
            "atr_pct": _to_float(getattr(strategy, "atr_pct", None)),
            "target_1": target1,
            "target_2": target2,
            "stop_price": stop,
            "trailing_distance_0_8atr": trailing,
        },
        "holder": {
            "avg_buy_price": avg,
            "current_price": current,
            "pnl_pct": pnl_pct,
            "action": holder_action,
            "upper_cross_any_timeframe": upper_cross,
            "lower_cross_any_timeframe": lower_cross,
            "provisional_daily_trail_floor": provisional_daily_trail,
            "provisional_45m_trail_floor": provisional_45m_trail,
        },
        "comparison": {
            "daily_bb_upper_vs_atr_target1": upper_vs_t1,
            "daily_bb_upper_vs_atr_target2": upper_vs_t2,
            "daily_bb_lower_vs_atr_stop": lower_vs_stop,
            "daily_bb_upper_minus_atr_target1": (
                daily_upper - target1 if daily_upper is not None and target1 is not None else None
            ),
            "daily_bb_lower_minus_atr_stop": (
                daily_lower - stop if daily_lower is not None and stop is not None else None
            ),
            "upper_guidance": upper_guidance,
            "lower_guidance": lower_guidance,
        },
        "principles": [
            "45분봉은 전술 조기신호, 일봉은 구조적 확인신호로 해석한다.",
            "상단 3호가 돌파는 고정 목표가 매도 신호가 아니라 트레일링 전환 신호다.",
            "하단 3호가 이탈은 추세 훼손 신호다. 손실구간이면 손실 최소화, 수익구간이면 이익보호를 우선한다.",
            "ATR 확대/축소와 BB 폭 확대/축소가 같은 방향이면 변동성 레짐 신뢰도를 높이고, 서로 반대면 보수적으로 해석한다.",
            "이 오버레이는 자동주문이 아니라 보유전략 판단용이다.",
        ],
    }


def _won(value: Any) -> str:
    number = _to_float(value)
    return "MISSING" if number is None else f"{number:,.0f}원"


def _pct(value: Any, *, signed: bool = False) -> str:
    number = _to_float(value)
    if number is None:
        return "MISSING"
    return f"{number:+.2f}%" if signed else f"{number:.2f}%"


def _metric_lines(metric: Dict[str, Any]) -> list[str]:
    if metric.get("status") != "READY":
        return [f"- 상태: {metric.get('status')} ({metric.get('row_count', 0)}봉)"]
    return [
        f"- 기준종가: {_won(metric.get('close'))}",
        f"- BB 하단 / 중심 / 상단: {_won(metric.get('bb_lower'))} / {_won(metric.get('bb_middle'))} / {_won(metric.get('bb_upper'))}",
        f"- 3호가 확인선: 하단 {_won(metric.get('lower_3tick_trigger'))} / 상단 {_won(metric.get('upper_3tick_trigger'))}",
        f"- 현재 밴드 위치: {_pct(metric.get('bb_position_pct'))} (0%=하단, 100%=상단)",
        f"- 밴드폭: {_pct(metric.get('bb_width_pct'))} / 5봉 변화 {_pct(metric.get('bb_width_change_5bars_pct'), signed=True)}",
        f"- 로컬 ATR14: {_won(metric.get('atr14_local'))} ({_pct(metric.get('atr_pct_local'))}) / 5봉 변화 {_pct(metric.get('atr14_change_5bars_pct'), signed=True)}",
        f"- 변동성 레짐: {metric.get('volatility_regime')}",
        f"- 3호가 돌파 판정: {metric.get('signal')}",
    ]


def render_bollinger_overlay_markdown(overlay: Dict[str, Any]) -> str:
    daily = overlay["daily"]
    intraday = overlay["intraday_45m"]
    atr = overlay["strategy_atr"]
    holder = overlay["holder"]
    compare = overlay["comparison"]

    lines = [
        "# Bollinger(26, 2.0) + ATR 보유전략 오버레이",
        "",
        f"- 표준: {overlay['canonical_rule']}",
        "- 기존 V8 특집 실행의 Bollinger T-score도 이 실행에서 2.0σ로 재계산한다. 원본 private V8 파일은 변경하지 않는다.",
        "",
        "## 일봉",
        "",
        *_metric_lines(daily),
        "",
        "## 45분봉",
        "",
        f"- 원천: {intraday.get('source', 'MISSING')} / 오류: {intraday.get('source_error', 'MISSING')}",
        *_metric_lines(intraday),
        "",
        "## 기존 ATR 전략과의 가격 비교",
        "",
        f"- Strategy ATR14: {_won(atr.get('atr14'))} ({_pct(atr.get('atr_pct'))})",
        f"- ATR 1차 / 2차 목표: {_won(atr.get('target_1'))} / {_won(atr.get('target_2'))}",
        f"- ATR 손절선 / 트레일링 폭(0.8ATR): {_won(atr.get('stop_price'))} / {_won(atr.get('trailing_distance_0_8atr'))}",
        f"- 일봉 BB상단 vs ATR 1차/2차: {compare.get('daily_bb_upper_vs_atr_target1')} / {compare.get('daily_bb_upper_vs_atr_target2')}",
        f"- 일봉 BB하단 vs ATR 손절선: {compare.get('daily_bb_lower_vs_atr_stop')}",
        f"- 상단 해석: {compare.get('upper_guidance')}",
        f"- 하단 해석: {compare.get('lower_guidance')}",
        "",
        "## 보유자 전술 판정",
        "",
        f"- 평단 / 현재가 / 손익률: {_won(holder.get('avg_buy_price'))} / {_won(holder.get('current_price'))} / {_pct(holder.get('pnl_pct'), signed=True)}",
        f"- Bollinger-ATR 행동상태: {holder.get('action')}",
        f"- 상단 3호가 돌파(일봉 또는 45분봉): {holder.get('upper_cross_any_timeframe')}",
        f"- 하단 3호가 이탈(일봉 또는 45분봉): {holder.get('lower_cross_any_timeframe')}",
        f"- 상단 돌파 시 임시 일봉 trail floor(현재가-0.8ATR): {_won(holder.get('provisional_daily_trail_floor'))}",
        f"- 상단 돌파 시 임시 45분봉 trail floor(현재가-0.8×45m ATR): {_won(holder.get('provisional_45m_trail_floor'))}",
        "",
        "## Spark 적용 원칙",
        "",
    ]
    lines.extend(f"- {text}" for text in overlay["principles"])
    lines.extend(
        [
            "- 기존 V8 본문의 1.7σ 표현 또는 판단이 남아 있으면 이 섹션의 BB(26,2.0) 값을 우선한다.",
            "- 상·하단을 단순 매도/매수선으로 보지 말고 평단 손익상태, ATR 목표/손절, 변동성 레짐과 함께 결론을 낸다.",
            "",
            "---",
            "",
        ]
    )
    return "\n".join(lines)
