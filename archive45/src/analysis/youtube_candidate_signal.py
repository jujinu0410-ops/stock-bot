# -*- coding: utf-8 -*-
"""YouTube Candidate Watch V1 - technical + flow confirmation sidecar.

This module is intentionally isolated from portfolio management and order execution.
It turns an already-discovered YouTube candidate into a review signal using four axes:
1) YouTube candidate membership (handled by youtube_candidate_ingest)
2) trend/pullback readiness
3) ATR rebound timing (0.4 early / 0.5 alert / 0.6 confirmation)
4) foreign/institution flow confirmation

No function in this module places orders or mutates held-position risk settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, List, Mapping, Optional


EARLY_REBOUND_ATR = 0.4
BUY_REBOUND_ATR = 0.5
CONFIRM_REBOUND_ATR = 0.6
OVERHEAT_ATR = 1.5
RSI_OVERHEAT = 70.0

TECH_WAIT = "WAIT"
TECH_READY = "READY"
TECH_EARLY_READY = "EARLY_READY"
TECH_BUY_CANDIDATE = "BUY_CANDIDATE"
TECH_DATA_HOLD = "DATA_HOLD"

FLOW_POSITIVE = "POSITIVE"
FLOW_NEUTRAL = "NEUTRAL"
FLOW_NEGATIVE = "NEGATIVE"
FLOW_UNAVAILABLE = "UNAVAILABLE"

FINAL_NO_ALERT = "NO_ALERT"
FINAL_BUY_ALERT_STRONG = "BUY_ALERT_STRONG"
FINAL_BUY_ALERT = "BUY_ALERT"
FINAL_WATCH_ONLY = "WATCH_ONLY"
FINAL_WATCH_ONLY_DATA_GAP = "WATCH_ONLY_DATA_GAP"

REGIME_POSITIVE = "POSITIVE"
REGIME_NEUTRAL = "NEUTRAL"
REGIME_NEGATIVE = "NEGATIVE"
REGIME_UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class TechnicalInput:
    close: float
    current_price: float
    reference_low: float
    atr14: float
    ma20: float
    ma60: float
    ma20_slope_5d: float
    rsi14: float
    pullback_ready: bool
    data_valid: bool = True
    suspended: bool = False


@dataclass(frozen=True)
class TechnicalAssessment:
    status: str
    rebound_atr: Optional[float]
    early_trigger_04: Optional[float]
    buy_trigger_05: Optional[float]
    confirm_trigger_06: Optional[float]
    confirmed_06: bool
    overheat: bool
    reason: str


@dataclass(frozen=True)
class FlowDay:
    dt: str
    foreign_amount: float
    institution_amount: float
    turnover_amount: float


@dataclass(frozen=True)
class FlowAssessment:
    status: str
    foreign_5d: Optional[float]
    institution_5d: Optional[float]
    combined_5d: Optional[float]
    combined_20d: Optional[float]
    avg_turnover_20d: Optional[float]
    flow_strength_5d: Optional[float]
    direction_20d: str
    recent_buy_turn: bool
    reason: str


@dataclass(frozen=True)
class MarketRegimeAssessment:
    status: str
    foreign_amount: Optional[float]
    institution_amount: Optional[float]
    program_amount: Optional[float]
    program_alignment: Optional[bool]
    reason: str


@dataclass(frozen=True)
class FinalSignalAssessment:
    final_signal: str
    tech_status: str
    flow_status: str
    market_regime: str
    reason: str


class FlowDataError(RuntimeError):
    pass


def evaluate_technical(
    x: TechnicalInput,
    early_rebound_atr: float = EARLY_REBOUND_ATR,
    buy_rebound_atr: float = BUY_REBOUND_ATR,
    confirm_rebound_atr: float = CONFIRM_REBOUND_ATR,
    overheat_atr: float = OVERHEAT_ATR,
    rsi_overheat: float = RSI_OVERHEAT,
) -> TechnicalAssessment:
    """Evaluate the simplified YouTube-candidate technical state.

    0.4 ATR is an early-ready marker, 0.5 ATR is the operational alert threshold,
    and 0.6 ATR is retained only as a later confirmation marker.
    """
    if (
        not x.data_valid
        or x.suspended
        or x.atr14 <= 0
        or x.reference_low <= 0
        or x.current_price <= 0
        or x.ma20 <= 0
        or x.ma60 <= 0
    ):
        return TechnicalAssessment(
            TECH_DATA_HOLD, None, None, None, None, False, False,
            "invalid/stale/suspended technical data",
        )

    early_trigger = x.reference_low + early_rebound_atr * x.atr14
    buy_trigger = x.reference_low + buy_rebound_atr * x.atr14
    confirm_trigger = x.reference_low + confirm_rebound_atr * x.atr14
    rebound_atr = (x.current_price - x.reference_low) / x.atr14
    confirmed = x.current_price >= confirm_trigger

    trend_ok = (
        x.close > x.ma60
        and x.ma20 > x.ma60
        and x.ma20_slope_5d > 0
    )
    if not trend_ok:
        return TechnicalAssessment(
            TECH_WAIT, rebound_atr, early_trigger, buy_trigger, confirm_trigger,
            confirmed, False, "trend filter not satisfied",
        )

    if not x.pullback_ready:
        return TechnicalAssessment(
            TECH_WAIT, rebound_atr, early_trigger, buy_trigger, confirm_trigger,
            confirmed, False, "pullback not confirmed",
        )

    overheat = (
        x.current_price > x.ma20 + overheat_atr * x.atr14
        or x.rsi14 >= rsi_overheat
    )

    if x.current_price < early_trigger:
        status = TECH_READY
        reason = "pullback ready; rebound below 0.4 ATR"
    elif x.current_price < buy_trigger:
        status = TECH_EARLY_READY
        reason = "0.4 ATR early rebound reached; waiting for 0.5 ATR"
    elif overheat:
        status = TECH_READY
        reason = "0.5 ATR reached but overheat guard blocks buy candidate"
    else:
        status = TECH_BUY_CANDIDATE
        reason = "0.5 ATR rebound reached with trend/pullback/overheat guards satisfied"

    return TechnicalAssessment(
        status,
        rebound_atr,
        early_trigger,
        buy_trigger,
        confirm_trigger,
        confirmed,
        overheat,
        reason,
    )


def _normalize_flow_days(rows: Iterable[FlowDay | Mapping[str, Any]]) -> List[FlowDay]:
    out: List[FlowDay] = []
    for row in rows:
        if isinstance(row, FlowDay):
            item = row
        else:
            item = FlowDay(
                dt=str(row.get("dt") or row.get("stk_date") or ""),
                foreign_amount=float(row.get("foreign_amount", row.get("foreign_net_buy", 0)) or 0),
                institution_amount=float(row.get("institution_amount", row.get("inst_net_buy", 0)) or 0),
                turnover_amount=float(row.get("turnover_amount", row.get("acc_trde_prica", 0)) or 0),
            )
        if item.dt:
            out.append(item)
    out.sort(key=lambda r: r.dt)
    return out


def evaluate_flow(
    rows: Iterable[FlowDay | Mapping[str, Any]],
    *,
    source_verified: bool,
    short_days: int = 5,
    long_days: int = 20,
) -> FlowAssessment:
    """Classify foreign/institution flow without a points score.

    `source_verified=False` is deliberately fail-closed. This prevents legacy OHLC
    rows whose foreign/institution columns are hard-coded zeros from being mislabeled
    as NEUTRAL flow.
    """
    days = _normalize_flow_days(rows)
    if not source_verified:
        return FlowAssessment(
            FLOW_UNAVAILABLE, None, None, None, None, None, None,
            "UNKNOWN", False, "flow source is not verified",
        )
    if len(days) < long_days:
        return FlowAssessment(
            FLOW_UNAVAILABLE, None, None, None, None, None, None,
            "UNKNOWN", False, f"need {long_days} flow days; got {len(days)}",
        )

    long_slice = days[-long_days:]
    short_slice = long_slice[-short_days:]

    foreign_5d = sum(x.foreign_amount for x in short_slice)
    institution_5d = sum(x.institution_amount for x in short_slice)
    combined_5d = foreign_5d + institution_5d
    combined_20d = sum(x.foreign_amount + x.institution_amount for x in long_slice)
    avg_turnover_20d = sum(max(0.0, x.turnover_amount) for x in long_slice) / long_days
    strength = combined_5d / avg_turnover_20d if avg_turnover_20d > 0 else None

    if combined_20d > 0:
        direction_20d = "UP"
    elif combined_20d < 0:
        direction_20d = "DOWN"
    else:
        direction_20d = "FLAT"

    recent3 = sum(x.foreign_amount + x.institution_amount for x in long_slice[-3:])
    prior2 = sum(x.foreign_amount + x.institution_amount for x in long_slice[-5:-3])
    recent_buy_turn = combined_20d < 0 and combined_5d > 0 and recent3 > 0 and prior2 <= 0

    if (foreign_5d < 0 and institution_5d < 0) or (combined_5d < 0 and combined_20d < 0):
        status = FLOW_NEGATIVE
        reason = "5d flow is negative with persistent/broad selling"
    elif combined_5d > 0 and (combined_20d >= 0 or recent_buy_turn):
        status = FLOW_POSITIVE
        reason = "5d combined flow is positive and 20d direction supports it or a recent buy turn is visible"
    else:
        status = FLOW_NEUTRAL
        reason = "foreign/institution or short/long flow directions are mixed"

    return FlowAssessment(
        status,
        foreign_5d,
        institution_5d,
        combined_5d,
        combined_20d,
        avg_turnover_20d,
        strength,
        direction_20d,
        recent_buy_turn,
        reason,
    )


def classify_market_regime(
    foreign_amount: Optional[float],
    institution_amount: Optional[float],
    program_amount: Optional[float] = None,
) -> MarketRegimeAssessment:
    """Market flow is context only; it never blocks a stock signal by itself."""
    if foreign_amount is None or institution_amount is None:
        return MarketRegimeAssessment(
            REGIME_UNAVAILABLE, foreign_amount, institution_amount, program_amount,
            None, "market foreign/institution flow unavailable",
        )

    if foreign_amount > 0 and institution_amount > 0:
        status = REGIME_POSITIVE
        reason = "foreign and institution are both net buyers"
    elif foreign_amount < 0 and institution_amount < 0:
        status = REGIME_NEGATIVE
        reason = "foreign and institution are both net sellers"
    else:
        status = REGIME_NEUTRAL
        reason = "foreign and institution directions are mixed"

    if program_amount is None or program_amount == 0:
        alignment = None
    else:
        flow_sign = 1 if (foreign_amount + institution_amount) > 0 else -1 if (foreign_amount + institution_amount) < 0 else 0
        program_sign = 1 if program_amount > 0 else -1
        alignment = flow_sign != 0 and flow_sign == program_sign

    return MarketRegimeAssessment(
        status, foreign_amount, institution_amount, program_amount, alignment, reason,
    )


def combine_final_signal(
    tech: TechnicalAssessment,
    flow: FlowAssessment,
    market: Optional[MarketRegimeAssessment] = None,
) -> FinalSignalAssessment:
    market_status = market.status if market else REGIME_UNAVAILABLE

    if tech.status != TECH_BUY_CANDIDATE:
        return FinalSignalAssessment(
            FINAL_NO_ALERT, tech.status, flow.status, market_status,
            f"technical state is {tech.status}; no buy alert",
        )

    if flow.status == FLOW_POSITIVE:
        if tech.confirmed_06:
            final = FINAL_BUY_ALERT_STRONG
            reason = "0.6 ATR confirmed technical buy candidate + positive foreign/institution flow"
        else:
            final = FINAL_BUY_ALERT
            reason = "0.5 ATR buy candidate + positive flow; waiting for 0.6 ATR confirmation"
    elif flow.status == FLOW_NEUTRAL:
        final = FINAL_BUY_ALERT
        reason = "technical buy candidate + neutral/mixed flow"
    elif flow.status == FLOW_NEGATIVE:
        final = FINAL_WATCH_ONLY
        reason = "technical buy candidate but foreign/institution flow is negative"
    else:
        final = FINAL_WATCH_ONLY_DATA_GAP
        reason = "technical buy candidate but verified flow data is unavailable"

    if market:
        reason += f"; market regime={market.status} (context only)"

    return FinalSignalAssessment(final, tech.status, flow.status, market_status, reason)


def _parse_signed_number(value: Any) -> float:
    """Parse Kiwoom signed numeric strings such as '+1,234' or '--335'."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace(" ", "")
    if not s:
        return 0.0
    sign = -1.0 if s.startswith("-") else 1.0
    s = s.lstrip("+-")
    try:
        return sign * float(s)
    except ValueError:
        return 0.0


class KiwoomInvestorFlowReader:
    """Read-only ka10059 adapter for stock-level investor amount flow.

    It requests amount mode (`amt_qty_tp=1`) and net-buy mode (`trde_tp=0`).
    The raw monetary unit returned by Kiwoom is preserved consistently for both
    investor flow and turnover, so FLOW_STRENGTH remains unitless.
    """

    def __init__(self, access_token: str, session: Any = None, base_url: str = "https://api.kiwoom.com"):
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("access_token is required")
        self.access_token = token
        self.base_url = base_url.rstrip("/")
        if session is None:
            import requests
            session = requests
        self.session = session

    def fetch_stock_flow(self, stock_code: str, as_of: date, max_pages: int = 5) -> List[FlowDay]:
        code = str(stock_code).replace("A", "").strip().zfill(6)
        url = f"{self.base_url}/api/dostk/stkinfo"
        body = {
            "dt": as_of.strftime("%Y%m%d"),
            "stk_cd": code,
            "amt_qty_tp": "1",
            "trde_tp": "0",
            "unit_tp": "1",
        }

        rows: List[FlowDay] = []
        next_key = ""
        seen_keys = set()

        for _ in range(max_pages):
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {self.access_token}",
                "api-id": "ka10059",
            }
            if next_key:
                headers["cont-yn"] = "Y"
                headers["next-key"] = next_key

            response = self.session.post(url, headers=headers, json=body, timeout=10)
            if getattr(response, "status_code", None) != 200:
                raise FlowDataError(f"ka10059 HTTP {getattr(response, 'status_code', 'UNKNOWN')}")
            payload = response.json()
            if not isinstance(payload, dict) or str(payload.get("return_code", "0")) not in ("0", "None"):
                raise FlowDataError(f"ka10059 business error: {payload}")

            items = payload.get("stk_invsr_orgn") or []
            for item in items:
                dt = str(item.get("dt") or "").strip()
                if not dt:
                    continue
                rows.append(
                    FlowDay(
                        dt=dt,
                        foreign_amount=_parse_signed_number(item.get("frgnr_invsr")),
                        institution_amount=_parse_signed_number(item.get("orgn")),
                        turnover_amount=abs(_parse_signed_number(item.get("acc_trde_prica"))),
                    )
                )

            cont_yn = (getattr(response, "headers", {}) or {}).get("cont-yn") or payload.get("cont-yn")
            new_key = (getattr(response, "headers", {}) or {}).get("next-key") or payload.get("next-key") or ""
            if str(cont_yn).upper() != "Y" or not new_key or new_key in seen_keys:
                break
            seen_keys.add(new_key)
            next_key = str(new_key)

        dedup = {r.dt: r for r in rows}
        return [dedup[k] for k in sorted(dedup)]
