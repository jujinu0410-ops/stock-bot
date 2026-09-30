# -*- coding: utf-8 -*-
"""Review-layer helpers for YouTube Candidate Watch.

This module stays read-only.  It refines human-facing flow explanations,
adds a fail-closed market-regime reader, decides whether a signal is mail-worthy,
and renders a preview without sending mail.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
import time

from src.analysis.youtube_candidate_signal import (
    FLOW_NEGATIVE,
    FLOW_NEUTRAL,
    FLOW_POSITIVE,
    FLOW_UNAVAILABLE,
    FINAL_BUY_ALERT,
    FINAL_BUY_ALERT_STRONG,
    REGIME_UNAVAILABLE,
    classify_market_regime,
)

MAIL_WORTHY_SIGNALS = {FINAL_BUY_ALERT, FINAL_BUY_ALERT_STRONG}


class MarketRegimeDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketRegimeSnapshot:
    market: str
    market_code: str
    status: str
    foreign_amount: Optional[float]
    institution_amount: Optional[float]
    program_amount: Optional[float]
    row_count: int
    complete: bool
    source: str
    reason: str


@dataclass(frozen=True)
class NotificationDecision:
    would_send: bool
    reason: str


def _n(value: Any) -> float:
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


def describe_flow(flow: Mapping[str, Any]) -> str:
    """Return a concise human-facing explanation without overstating broad selling."""
    status = str(flow.get("status") or FLOW_UNAVAILABLE)
    f5 = flow.get("foreign_5d")
    i5 = flow.get("institution_5d")
    c5 = flow.get("combined_5d")
    c20 = flow.get("combined_20d")

    if status == FLOW_UNAVAILABLE or None in (f5, i5, c5, c20):
        return "검증된 외국인·기관 수급 데이터가 없어 수급 판정을 보류한다."

    f5 = float(f5)
    i5 = float(i5)
    c5 = float(c5)
    c20 = float(c20)

    if status == FLOW_NEGATIVE:
        if f5 < 0 < i5:
            return (
                f"외국인 5일 순매도({f5:+,.0f})를 기관 순매수({i5:+,.0f})가 대부분 흡수했지만, "
                f"5일 합산({c5:+,.0f})과 20일 누적({c20:+,.0f})이 아직 음수다."
            )
        if i5 < 0 < f5:
            return (
                f"기관 5일 순매도({i5:+,.0f})를 외국인 순매수({f5:+,.0f})가 일부 상쇄했지만, "
                f"5일 합산({c5:+,.0f})과 20일 누적({c20:+,.0f})이 아직 음수다."
            )
        if f5 < 0 and i5 < 0:
            return (
                f"외국인({f5:+,.0f})과 기관({i5:+,.0f})이 5일 동반 순매도이고, "
                f"20일 누적도 {c20:+,.0f}로 음수다."
            )
        return f"5일 합산 수급({c5:+,.0f})과 20일 누적({c20:+,.0f})이 모두 음수다."

    if status == FLOW_POSITIVE:
        return (
            f"5일 외국인·기관 합산이 {c5:+,.0f}, 20일 누적이 {c20:+,.0f}로 "
            "기술적 반등을 수급이 확인한다."
        )

    if status == FLOW_NEUTRAL:
        return (
            f"외국인({f5:+,.0f})·기관({i5:+,.0f}) 또는 5일({c5:+,.0f})·20일({c20:+,.0f}) "
            "방향이 엇갈려 수급 확인 강도가 낮다."
        )

    return "수급 상태를 해석할 수 없다."


def decide_notification(
    current_signal: str,
    *,
    previous_signal: Optional[str] = None,
    trading_days_since_last: Optional[int] = None,
    new_ready_cycle: bool = False,
    cooldown_trading_days: int = 5,
) -> NotificationDecision:
    """Apply the V1 one-shot + cooldown rule without mutating history."""
    if current_signal not in MAIL_WORTHY_SIGNALS:
        return NotificationDecision(False, f"{current_signal} is not a buy-interest mail state")

    if previous_signal not in MAIL_WORTHY_SIGNALS:
        return NotificationDecision(True, "first entry into a buy-interest mail state")

    if not new_ready_cycle:
        return NotificationDecision(False, "same READY cycle; duplicate mail suppressed")

    if trading_days_since_last is None:
        return NotificationDecision(False, "new READY cycle detected but cooldown age is unknown")

    if trading_days_since_last < cooldown_trading_days:
        return NotificationDecision(
            False,
            f"new READY cycle but cooldown {trading_days_since_last}/{cooldown_trading_days} trading days not met",
        )

    return NotificationDecision(True, "new READY cycle and cooldown requirement satisfied")


def market_code_from_market_type(market_type: Optional[str]) -> Optional[tuple[str, str]]:
    text = str(market_type or "").upper().replace(" ", "")
    if "KOSPI" in text or "코스피" in text:
        return "KOSPI", "001"
    if "KOSDAQ" in text or "코스닥" in text:
        return "KOSDAQ", "101"
    return None


class KiwoomAfterCloseMarketRegimeReader:
    """Aggregate ka10066 stock rows into market foreign/institution net flow.

    The reader is fail-closed: if pagination does not finish, no market regime is
    returned.  Program flow is intentionally left unavailable in V1.
    """

    def __init__(self, access_token: str, session: Any, base_url: str = "https://api.kiwoom.com"):
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("access_token is required")
        self.access_token = token
        self.session = session
        self.base_url = base_url.rstrip("/")

    def fetch(self, market: str, market_code: str, *, max_pages: int = 60, page_delay: float = 0.22) -> MarketRegimeSnapshot:
        url = f"{self.base_url}/api/dostk/mrkcond"
        body = {
            "mrkt_tp": market_code,
            "amt_qty_tp": "1",
            "trde_tp": "0",
            "stex_tp": "3",
        }
        next_key = ""
        rows: list[Mapping[str, Any]] = []
        complete = False

        for page in range(max_pages):
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {self.access_token}",
                "api-id": "ka10066",
            }
            if next_key:
                headers["cont-yn"] = "Y"
                headers["next-key"] = next_key

            response = self.session.post(url, headers=headers, json=body, timeout=12)
            if getattr(response, "status_code", None) != 200:
                raise MarketRegimeDataError(f"ka10066 HTTP {getattr(response, 'status_code', 'UNKNOWN')}")
            payload = response.json()
            if not isinstance(payload, dict) or str(payload.get("return_code", "0")) not in ("0", "None"):
                raise MarketRegimeDataError(f"ka10066 business error: {payload}")

            page_rows = payload.get("opaf_invsr_trde") or []
            if isinstance(page_rows, list):
                rows.extend(x for x in page_rows if isinstance(x, Mapping))

            cont = str(response.headers.get("cont-yn") or response.headers.get("Cont-Yn") or "").upper()
            nk = str(response.headers.get("next-key") or response.headers.get("Next-Key") or "").strip()
            if cont != "Y":
                complete = True
                break
            if not nk or nk == next_key:
                raise MarketRegimeDataError("ka10066 continuation key missing/repeated")
            next_key = nk
            if page_delay > 0:
                time.sleep(page_delay)

        if not complete:
            raise MarketRegimeDataError(f"ka10066 pagination incomplete after {max_pages} pages")
        if not rows:
            raise MarketRegimeDataError("ka10066 returned no market rows")

        foreign = sum(_n(x.get("frgnr_invsr")) for x in rows)
        institution = sum(_n(x.get("orgn")) for x in rows)
        assessment = classify_market_regime(foreign, institution, None)
        return MarketRegimeSnapshot(
            market=market,
            market_code=market_code,
            status=assessment.status,
            foreign_amount=foreign,
            institution_amount=institution,
            program_amount=None,
            row_count=len(rows),
            complete=True,
            source="KIWOOM_KA10066_AGGREGATED",
            reason=assessment.reason,
        )


def render_candidate_preview(candidate: Mapping[str, Any], market: Optional[MarketRegimeSnapshot] = None) -> str:
    """Render a plain-text mail preview.  This function never sends anything."""
    tech = candidate.get("technical") or {}
    flow = candidate.get("flow") or {}
    final = candidate.get("final") or {}
    signal = str(final.get("signal") or "NO_ALERT")
    market_status = market.status if market else str(final.get("market_regime") or REGIME_UNAVAILABLE)
    flow_text = describe_flow(flow)

    lines = [
        f"[{signal}] {candidate.get('name')} {candidate.get('ticker')}",
        f"기술: {tech.get('status')} / rebound={tech.get('rebound_atr')}",
        f"가격: {tech.get('current_price')} / ATR14={tech.get('atr14')}",
        f"0.5ATR: {tech.get('buy_trigger_05')} / 0.6ATR: {tech.get('confirm_trigger_06')}",
        f"수급: {flow.get('status')} — {flow_text}",
        f"시장수급: {market_status}" + (f" ({market.market})" if market else ""),
        f"최근 YouTube 언급: {candidate.get('last_seen_date')} / 30일 언급 {candidate.get('mention_count_30d')}회",
        "자동매수 지시가 아니라 매수 검토용 preview다.",
    ]
    return "\n".join(lines)
