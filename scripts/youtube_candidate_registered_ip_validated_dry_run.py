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

The underlying runner remains read-only: Gmail BODY.PEEK, SQLite mode=ro/query_only,
GET-only market data, Kiwoom read-only token/ka10059, no Sheet/mail/order mutation.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any, Dict, Tuple

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
_base_run = runner.run

_identity_cache: Dict[Tuple[str, str], Any] = {}
_latest_source_name: Dict[str, Tuple[date, str]] = {}
_source_quarantines: Dict[Tuple[str, str], Dict[str, str]] = {}
_current_technical_ticker: str | None = None
_buy_gate_holds: Dict[str, Dict[str, str]] = {}


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
    _current_technical_ticker = None

    result = _base_run(days, as_of, db_path)
    quarantines = list(_source_quarantines.values())
    holds = list(_buy_gate_holds.values())
    result["source_identity_quarantine_count"] = len(quarantines)
    result["source_identity_quarantines"] = quarantines
    result["buy_gate_identity_hold_count"] = len(holds)
    result["buy_gate_identity_holds"] = holds

    if result.get("data_quality") == "VALID" and quarantines:
        result["data_quality"] = "VALID_WITH_SOURCE_IDENTITY_QUARANTINE"
    elif result.get("data_quality") == "PARTIAL_REVIEW_ISOLATED_CANDIDATE_DATA" and quarantines:
        result["data_quality"] = "PARTIAL_REVIEW_WITH_SOURCE_IDENTITY_QUARANTINE"
    return result


runner.parse_and_resolve_mail = _validated_parse_and_resolve_mail
runner.fetch_naver_daily = _tracked_fetch_naver_daily
runner.evaluate_technical = _identity_gated_evaluate_technical
runner._flow_dict = _review_flow_dict
runner.run = _validated_run

if __name__ == "__main__":
    raise SystemExit(runner.main())
