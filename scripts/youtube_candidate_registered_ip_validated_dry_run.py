# -*- coding: utf-8 -*-
"""Registered-IP exact-flow dry run with candidate identity safety gates.

Identity policy is deliberately two-stage:

1) Ingestion quarantine
   - A source mail code/name pair is removed immediately ONLY when a secondary
     lookup proves that the mail's company name resolves to a different ticker.
     Example: `한화솔루션 (098300)` resolves to 009830/009835, so 098300 is isolated.
   - Cosmetic/transliteration aliases that cannot be resolved conclusively are kept
     in the candidate pool; one uncertain alias must not block unrelated candidates.

2) BUY_CANDIDATE gate
   - Immediately before the operational 0.5 ATR state can request exact Kiwoom flow,
     the latest source name for that ticker must pass identity verification.
   - If identity is still unverified, the technical state is downgraded to DATA_HOLD
     for this run, so no ka10059 query and no buy alert can occur for that ticker.

3) Kiwoom 429 resilience
   - Token and ka10059 calls retry HTTP 429 with Retry-After when provided.
   - Without Retry-After, a short bounded exponential backoff is used.
   - Exhausted retries remain fail-closed; no alert is emitted from missing flow.

The underlying runner remains read-only: Gmail BODY.PEEK, SQLite mode=ro/query_only,
GET-only market data, Kiwoom read-only token/ka10059, no Sheet/mail/order mutation.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import time
from typing import Any, Dict, List, Tuple

import scripts.youtube_candidate_registered_ip_dry_run as runner
from src.analysis.youtube_candidate_identity import verify_candidate_identity
from src.analysis.youtube_candidate_ingest import ParseResult
from src.analysis.youtube_candidate_mail_adapter import parse_and_resolve_mail as _parse_base
from src.analysis.youtube_candidate_review import describe_flow
from src.analysis.youtube_candidate_signal import TECH_BUY_CANDIDATE


# Save original callables before monkey-patching the validation sidecar.
_base_fetch_naver_daily = runner.fetch_naver_daily
_base_evaluate_technical = runner.evaluate_technical
_base_flow_dict = runner._flow_dict
_base_get_kiwoom_token = runner.get_kiwoom_token
_base_flow_reader = runner.KiwoomInvestorFlowReader
_base_run = runner.run

_identity_cache: Dict[Tuple[str, str], Any] = {}
_latest_source_name: Dict[str, Tuple[date, str]] = {}
_source_quarantines: Dict[Tuple[str, str], Dict[str, str]] = {}
_current_technical_ticker: str | None = None
_buy_gate_holds: Dict[str, Dict[str, str]] = {}
_rate_limit_events: List[Dict[str, Any]] = []


class _Retry429Session:
    """Proxy a requests-like session and retry only HTTP 429 responses.

    Kiwoom's public tooling uses Retry-After for rate-limit responses.  We honor
    that header when present (capped to 60 seconds); otherwise use 1/2/4/8-second
    bounded backoff.  Non-429 responses are returned immediately and unchanged.
    """

    def __init__(
        self,
        session: Any,
        *,
        max_attempts: int = 5,
        max_retry_after: float = 60.0,
        sleep_fn=time.sleep,
    ) -> None:
        self._session = session
        self.max_attempts = max(1, int(max_attempts))
        self.max_retry_after = max(0.0, float(max_retry_after))
        self._sleep = sleep_fn

    def __getattr__(self, name: str):
        return getattr(self._session, name)

    def _delay_seconds(self, response: Any, attempt: int) -> float:
        headers = getattr(response, "headers", {}) or {}
        raw = None
        try:
            raw = headers.get("Retry-After") or headers.get("retry-after")
        except Exception:
            raw = None
        if raw not in (None, ""):
            try:
                delay = float(str(raw).strip())
                if delay >= 0:
                    return min(delay, self.max_retry_after)
            except (TypeError, ValueError):
                pass
        fallback = float(2 ** max(0, attempt - 1))
        return min(fallback, 8.0, self.max_retry_after)

    def post(self, *args, **kwargs):
        last = None
        for attempt in range(1, self.max_attempts + 1):
            last = self._session.post(*args, **kwargs)
            if getattr(last, "status_code", None) != 429:
                return last
            if attempt >= self.max_attempts:
                return last
            delay = self._delay_seconds(last, attempt)
            url = str(args[0] if args else kwargs.get("url") or "")
            _rate_limit_events.append({
                "attempt": attempt,
                "delay_seconds": delay,
                "endpoint": url.rsplit("/", 1)[-1] if url else "UNKNOWN",
            })
            if delay > 0:
                self._sleep(delay)
        return last


def _wrap_retry_session(session: Any):
    if isinstance(session, _Retry429Session):
        return session
    return _Retry429Session(session)


def _retrying_get_kiwoom_token(session):
    return _base_get_kiwoom_token(_wrap_retry_session(session))


class _RetryingKiwoomInvestorFlowReader(_base_flow_reader):
    def __init__(self, access_token: str, session: Any = None, base_url: str = "https://api.kiwoom.com"):
        wrapped = _wrap_retry_session(session) if session is not None else session
        super().__init__(access_token, session=wrapped, base_url=base_url)


def _remember_latest_name(ticker: str, report_date: date, source_name: str) -> None:
    current = _latest_source_name.get(ticker)
    if current is None or report_date >= current[0]:
        _latest_source_name[ticker] = (report_date, source_name)


def _identity(ticker: str, source_name: str):
    key = (ticker, source_name)
    assessment = _identity_cache.get(key)
    if assessment is None:
        assessment = verify_candidate_identity(ticker, source_name)
        _identity_cache[key] = assessment
    return assessment


def _validated_parse_and_resolve_mail(subject, body, stock_rows, fallback_date=None):
    parsed = _parse_base(subject, body, stock_rows, fallback_date)
    if not parsed.resolved:
        return parsed

    kept = []
    unresolved = list(parsed.unresolved)

    for mention in parsed.resolved:
        source_name = str(mention.raw_text or mention.name or "").strip()
        _remember_latest_name(mention.ticker, parsed.report_date, source_name)
        assessment = _identity(mention.ticker, source_name)

        if assessment.status == "SOURCE_IDENTITY_MISMATCH":
            qkey = (mention.ticker, source_name)
            _source_quarantines[qkey] = {
                "report_date": parsed.report_date.isoformat(),
                "subject": parsed.source_subject,
                "ticker": mention.ticker,
                "source_name": source_name,
                "verified_name": str(assessment.verified_name or ""),
                "status": assessment.status,
                "reason": assessment.reason,
            }
            continue

        kept.append(mention)

    return ParseResult(
        report_date=parsed.report_date,
        raw_mentions=parsed.raw_mentions,
        resolved=tuple(kept),
        unresolved=tuple(unresolved),
        source_subject=parsed.source_subject,
    )


def _tracked_fetch_naver_daily(code: str, count: int = 100):
    global _current_technical_ticker
    _current_technical_ticker = str(code).replace("A", "").strip().zfill(6)
    return _base_fetch_naver_daily(code, count=count)


def _identity_gated_evaluate_technical(ti, *args, **kwargs):
    tech = _base_evaluate_technical(ti, *args, **kwargs)
    if tech.status != TECH_BUY_CANDIDATE:
        return tech

    ticker = str(_current_technical_ticker or "").strip()
    latest = _latest_source_name.get(ticker)
    if not ticker or latest is None:
        reason = "BUY gate identity unavailable: latest source ticker/name not found"
        _buy_gate_holds[ticker or "UNKNOWN"] = {
            "ticker": ticker or "UNKNOWN",
            "source_name": "",
            "status": "DATA_HOLD",
            "reason": reason,
        }
        return replace(tech, status="DATA_HOLD", reason=reason)

    _, source_name = latest
    assessment = _identity(ticker, source_name)
    if assessment.valid:
        return tech

    reason = f"BUY gate identity {assessment.status}: {assessment.reason}"
    _buy_gate_holds[ticker] = {
        "ticker": ticker,
        "source_name": source_name,
        "verified_name": str(assessment.verified_name or ""),
        "status": assessment.status,
        "reason": assessment.reason,
    }
    return replace(tech, status="DATA_HOLD", reason=reason)


def _review_flow_dict(flow, query_status: str, error=None):
    out = _base_flow_dict(flow, query_status, error)
    if flow is not None:
        out["reason"] = describe_flow(out)
    return out


def _validated_run(days, as_of, db_path):
    global _current_technical_ticker
    _identity_cache.clear()
    _latest_source_name.clear()
    _source_quarantines.clear()
    _buy_gate_holds.clear()
    _rate_limit_events.clear()
    _current_technical_ticker = None

    result = _base_run(days, as_of, db_path)
    quarantines = list(_source_quarantines.values())
    holds = list(_buy_gate_holds.values())
    result["source_identity_quarantine_count"] = len(quarantines)
    result["source_identity_quarantines"] = quarantines
    result["buy_gate_identity_hold_count"] = len(holds)
    result["buy_gate_identity_holds"] = holds
    result["kiwoom_http_429_retry_count"] = len(_rate_limit_events)
    result["kiwoom_http_429_retry_wait_seconds"] = sum(
        float(x.get("delay_seconds") or 0) for x in _rate_limit_events
    )
    result["kiwoom_http_429_retry_events"] = list(_rate_limit_events)

    if result.get("data_quality") == "VALID" and quarantines:
        result["data_quality"] = "VALID_WITH_SOURCE_IDENTITY_QUARANTINE"
    elif result.get("data_quality") == "PARTIAL_REVIEW_ISOLATED_CANDIDATE_DATA" and quarantines:
        result["data_quality"] = "PARTIAL_REVIEW_WITH_SOURCE_IDENTITY_QUARANTINE"
    return result


runner.parse_and_resolve_mail = _validated_parse_and_resolve_mail
runner.fetch_naver_daily = _tracked_fetch_naver_daily
runner.evaluate_technical = _identity_gated_evaluate_technical
runner._flow_dict = _review_flow_dict
runner.get_kiwoom_token = _retrying_get_kiwoom_token
runner.KiwoomInvestorFlowReader = _RetryingKiwoomInvestorFlowReader
runner.run = _validated_run

if __name__ == "__main__":
    raise SystemExit(runner.main())
