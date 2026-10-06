from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pandas as pd

from daily_v8 import strategy_engine
from mobile_v8.consistency_guard import apply_mobile_consistency_guard


class _StrategyDtoProxy:
    """Expose the V8 bundle while forcing Strategy Engine to use dated OHLCV close.

    The shared Strategy Engine treats DTO current_price without a quote timestamp as
    undated and falls back to the downloaded daily close. The mobile path intentionally
    supplies Naver 250D as that downloaded frame, so no stale Yahoo close can leak into
    the calculation while all existing strategy formulas remain unchanged.
    """

    def __init__(self, base: object) -> None:
        self._base = base
        self.current_price = None
        self.current_price_asof = ""

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _to_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"NAVER_STRATEGY_INVALID_{field.upper()}") from exc
    if pd.isna(result):
        raise RuntimeError(f"NAVER_STRATEGY_INVALID_{field.upper()}")
    return result


def _naver_strategy_frame(evidence: dict[str, Any]) -> pd.DataFrame:
    market = evidence.get("market") or {}
    candles = market.get("candles") or []
    if market.get("quality") != "VALID" or len(candles) != 250:
        raise RuntimeError("NAVER_STRATEGY_250D_INVALID")

    rows: list[dict[str, Any]] = []
    for candle in candles:
        raw_date = str(candle.get("date") or "").strip()
        stamp = pd.to_datetime(raw_date, format="%Y%m%d", errors="coerce")
        if pd.isna(stamp):
            stamp = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(stamp):
            raise RuntimeError("NAVER_STRATEGY_INVALID_DATE")
        rows.append(
            {
                "Date": stamp,
                "Open": _to_float(candle.get("open"), "open"),
                "High": _to_float(candle.get("high"), "high"),
                "Low": _to_float(candle.get("low"), "low"),
                "Close": _to_float(candle.get("close"), "close"),
                "Volume": _to_float(candle.get("volume"), "volume"),
            }
        )

    frame = pd.DataFrame(rows).set_index("Date").sort_index()
    if frame.index.has_duplicates:
        raise RuntimeError("NAVER_STRATEGY_DUPLICATE_DATE")
    return frame


def _compute_wilder_atr14(df: pd.DataFrame) -> float:
    """Return standard Wilder ATR(14) on the exact mobile Naver 250D frame."""
    if df.empty:
        return 0.0
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    if len(tr) < 14:
        return float(tr.mean()) if len(tr) else 0.0

    atr = float(tr.iloc[:14].mean())
    for value in tr.iloc[14:]:
        atr = ((atr * 13.0) + float(value)) / 14.0
    return float(atr)


def compute_mobile_strategy(
    stock_code: str,
    *,
    bundle: object,
    evidence: dict[str, Any],
    now: Any,
):
    """Run shared Strategy formulas on canonical parity + Naver 250D/Wilder ATR.

    General mobile already calls the consistency guard explicitly. Portfolio-special
    also uses this adapter, so enforce the same active-path guard here when it has not
    yet been applied. This keeps both production paths aligned without touching the
    retired Daily V8 one-stock workflow or the shared Strategy formulas.
    """
    guard = evidence.get("analysis_consistency")
    if not guard:
        guard = apply_mobile_consistency_guard(
            bundle=bundle,
            evidence=evidence,
            generated_at=now,
        )

    # PARITY_READY means the canonical financial evidence is expected to be usable.
    # If it cannot be synchronized, do not fall back silently to the legacy V8 bundle.
    if evidence.get("status") == "PARITY_READY":
        financial_sync = (guard or {}).get("financial_sync") or {}
        if financial_sync.get("status") not in {"SYNCED", "OVERRIDDEN", "BANK_SYNCED"}:
            raise RuntimeError(
                "FINANCIAL_PARITY_CONSISTENCY_FAILED:"
                + str(financial_sync.get("reason") or financial_sync.get("status") or "MISSING")
            )

    frame = _naver_strategy_frame(evidence)
    market_type = str(getattr(bundle, "market_type", "") or "").upper().strip()
    expected_suffix = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}.get(market_type)
    if not expected_suffix:
        raise RuntimeError("NAVER_STRATEGY_MARKET_TYPE_MISSING")
    expected_symbol = f"{stock_code}{expected_suffix}"

    def _download(ticker: str, *args: Any, **kwargs: Any) -> pd.DataFrame:
        if str(ticker) != expected_symbol:
            return pd.DataFrame()
        return frame.copy()

    dto = _StrategyDtoProxy(bundle)
    with (
        patch.object(strategy_engine.yf, "download", side_effect=_download),
        patch.object(strategy_engine, "compute_atr14", side_effect=_compute_wilder_atr14),
    ):
        result = strategy_engine.compute_strategy(stock_code, dto=dto, now=now)

    if result is None:
        return None

    result.data_source = "naver_fchart_250d_mobile_adapter"
    result.calc_notes = dict(result.calc_notes or {})
    result.calc_notes.update(
        {
            "daily_ohlcv_source": "NAVER_FCHART_250D",
            "atr14_source": "naver_fchart_250d_computed",
            "atr14_method": "WILDER_RMA_14",
            "current_price_source": "naver_fchart_250d_latest_close",
            "mobile_strategy_adapter": "shared_formula_naver_input_wilder_atr",
        }
    )
    return result
