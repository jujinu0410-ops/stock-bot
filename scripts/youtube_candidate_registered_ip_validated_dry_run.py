# -*- coding: utf-8 -*-
"""Registered-IP exact-flow dry run with source ticker/name identity validation.

This is the preferred manual validation entrypoint on the user's Kiwoom-registered
Windows IP.  It wraps youtube_candidate_registered_ip_dry_run without changing its
read-only guarantees and validates every structured mail candidate's six-digit code
against Naver before the rolling pool is rebuilt.

The validator uses the *mail source name* (`raw_text`), not a local DB registry label.
This matters because some legacy stock_info rows contain the ticker itself as the
stock_name.  A source code/name mismatch is quarantined and can never reach the
0.5 ATR buy gate or Kiwoom ka10059 flow query.
"""
from __future__ import annotations

from typing import Dict, Tuple

import scripts.youtube_candidate_registered_ip_dry_run as runner
from src.analysis.youtube_candidate_identity import verify_candidate_identity
from src.analysis.youtube_candidate_ingest import ParseResult, UnresolvedMention
from src.analysis.youtube_candidate_mail_adapter import parse_and_resolve_mail as _parse_base


_identity_cache: Dict[Tuple[str, str], object] = {}


def _validated_parse_and_resolve_mail(subject, body, stock_rows, fallback_date=None):
    parsed = _parse_base(subject, body, stock_rows, fallback_date)
    if not parsed.resolved:
        return parsed

    kept = []
    unresolved = list(parsed.unresolved)
    for mention in parsed.resolved:
        source_name = str(mention.raw_text or mention.name or "").strip()
        key = (mention.ticker, source_name)
        assessment = _identity_cache.get(key)
        if assessment is None:
            assessment = verify_candidate_identity(mention.ticker, source_name)
            _identity_cache[key] = assessment
        if assessment.valid:
            kept.append(mention)
        else:
            unresolved.append(
                UnresolvedMention(
                    source_name,
                    f"{assessment.status}: {assessment.reason}",
                )
            )

    return ParseResult(
        report_date=parsed.report_date,
        raw_mentions=parsed.raw_mentions,
        resolved=tuple(kept),
        unresolved=tuple(unresolved),
        source_subject=parsed.source_subject,
    )


runner.parse_and_resolve_mail = _validated_parse_and_resolve_mail

if __name__ == "__main__":
    raise SystemExit(runner.main())
