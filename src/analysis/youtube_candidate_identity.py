# -*- coding: utf-8 -*-
"""Ticker/name identity guard for YouTube Candidate Watch.

Candidate mails occasionally contain a valid-looking six-digit code paired with the
wrong company name.  Because explicit mail codes are canonical identity for ingest,
that source error can otherwise survive parsing.  This module provides a read-only
secondary check against Naver's stock basic endpoint immediately before a candidate
is allowed to proceed from the technical BUY_CANDIDATE gate to investor-flow review.

Fail closed: network/schema errors or name mismatches are never treated as a valid
identity.  This guard does not rewrite the source ticker; it only quarantines it.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Optional

import requests


NAVER_BASIC_URL = "https://m.stock.naver.com/api/stock/{code}/basic"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


@dataclass(frozen=True)
class IdentityAssessment:
    valid: bool
    ticker: str
    source_name: str
    verified_name: Optional[str]
    status: str
    reason: str


class IdentityDataError(RuntimeError):
    pass


def _ticker(code: str) -> str:
    raw = str(code or "").strip().upper()
    if raw.startswith("A") and len(raw) == 7:
        raw = raw[1:]
    if not (len(raw) == 6 and raw.isdigit()):
        raise IdentityDataError(f"INVALID_TICKER: {code}")
    return raw


def normalize_company_name(value: str) -> str:
    """Normalize cosmetic differences while preserving the company identity."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = re.sub(r"\(주\)|㈜|주식회사", "", text)
    text = re.sub(r"[\s·ㆍ._\-]+", "", text)
    return text


def verify_candidate_identity(
    code: str,
    source_name: str,
    *,
    session: Optional[Any] = None,
) -> IdentityAssessment:
    ticker = _ticker(code)
    source = str(source_name or "").strip()
    if not source:
        return IdentityAssessment(False, ticker, source, None, "DATA_HOLD", "source company name is empty")

    sess = session or requests.Session()
    url = NAVER_BASIC_URL.format(code=ticker)
    try:
        response = sess.get(url, headers=DEFAULT_HEADERS, timeout=8)
    except Exception as exc:
        return IdentityAssessment(False, ticker, source, None, "DATA_HOLD", f"identity lookup failed: {exc}")

    if getattr(response, "status_code", None) != 200:
        return IdentityAssessment(
            False, ticker, source, None, "DATA_HOLD",
            f"identity lookup HTTP {getattr(response, 'status_code', 'UNKNOWN')}",
        )

    try:
        payload = response.json()
    except Exception as exc:
        return IdentityAssessment(False, ticker, source, None, "DATA_HOLD", f"identity JSON decode failed: {exc}")

    if not isinstance(payload, dict):
        return IdentityAssessment(False, ticker, source, None, "DATA_HOLD", "identity response is not an object")

    verified = str(payload.get("stockName") or "").strip()
    if not verified:
        return IdentityAssessment(False, ticker, source, None, "DATA_HOLD", "identity stockName missing")

    source_norm = normalize_company_name(source)
    verified_norm = normalize_company_name(verified)
    if not source_norm or source_norm != verified_norm:
        return IdentityAssessment(
            False,
            ticker,
            source,
            verified,
            "SOURCE_IDENTITY_MISMATCH",
            f"mail name '{source}' does not match ticker {ticker} verified name '{verified}'",
        )

    return IdentityAssessment(True, ticker, source, verified, "VERIFIED", "ticker/name identity verified")
