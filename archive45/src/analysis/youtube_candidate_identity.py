# -*- coding: utf-8 -*-
"""Ticker/name identity guard for YouTube Candidate Watch.

Candidate mails occasionally contain a valid-looking six-digit code paired with the
wrong company name.  Because explicit mail codes are canonical identity for ingest,
that source error can otherwise survive parsing.  This module cross-checks the code
against Naver before a candidate is allowed to proceed to the investor-flow stage.

Validation order:
1. Naver stock basic endpoint: exact/cosmetic-normalized stockName match.
2. If names differ (e.g. PSK홀딩스 vs 피에스케이홀딩스, 포스코 vs POSCO),
   Naver autocomplete must resolve the *source name* back to the same six-digit code.

Fail closed: a missing/invalid code, lookup failure, or source name that resolves to a
different ticker is never treated as valid.  The guard never rewrites the source code.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, List, Optional, Set

import requests


NAVER_BASIC_URL = "https://m.stock.naver.com/api/stock/{code}/basic"
NAVER_AUTOCOMPLETE_URL = "https://m.stock.naver.com/front-api/search/autoComplete"
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


def _collect_six_digit_codes(value: Any) -> Set[str]:
    """Recursively collect six-digit `code`-like values from autocomplete JSON.

    Naver's undocumented response envelope has changed over time.  Traversing the
    JSON structurally is safer than depending on one exact result.items path while
    still requiring a strict six-digit stock code match.
    """
    found: Set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"code", "itemcode", "stockcode", "symbol"}:
                raw = str(item or "").strip().upper()
                if raw.startswith("A") and len(raw) == 7:
                    raw = raw[1:]
                if len(raw) == 6 and raw.isdigit():
                    found.add(raw)
            found.update(_collect_six_digit_codes(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_collect_six_digit_codes(item))
    return found


def _autocomplete_codes(sess: Any, source_name: str) -> List[str]:
    response = sess.get(
        NAVER_AUTOCOMPLETE_URL,
        params={"query": source_name, "target": "stock"},
        headers=DEFAULT_HEADERS,
        timeout=8,
    )
    if getattr(response, "status_code", None) != 200:
        raise IdentityDataError(
            f"autocomplete HTTP {getattr(response, 'status_code', 'UNKNOWN')}"
        )
    payload = response.json()
    return sorted(_collect_six_digit_codes(payload))


def verify_candidate_identity(
    code: str,
    source_name: str,
    *,
    session: Optional[Any] = None,
) -> IdentityAssessment:
    ticker = _ticker(code)
    source = str(source_name or "").strip()
    if not source or (len(source) == 6 and source.isdigit()):
        return IdentityAssessment(False, ticker, source, None, "DATA_HOLD", "source company name is missing")

    sess = session or requests.Session()
    verified: Optional[str] = None
    basic_error: Optional[str] = None

    try:
        response = sess.get(
            NAVER_BASIC_URL.format(code=ticker),
            headers=DEFAULT_HEADERS,
            timeout=8,
        )
        if getattr(response, "status_code", None) == 200:
            payload = response.json()
            if isinstance(payload, dict):
                verified = str(payload.get("stockName") or "").strip() or None
            if verified and normalize_company_name(source) == normalize_company_name(verified):
                return IdentityAssessment(
                    True, ticker, source, verified, "VERIFIED", "ticker/name identity verified"
                )
        else:
            basic_error = f"basic HTTP {getattr(response, 'status_code', 'UNKNOWN')}"
    except Exception as exc:
        basic_error = f"basic lookup failed: {exc}"

    # Legitimate Korean/English/transliteration aliases are accepted only when a
    # second endpoint resolves the *mail's own company name* back to the same code.
    try:
        alias_codes = _autocomplete_codes(sess, source)
    except Exception as exc:
        alias_codes = []
        alias_error = str(exc)
    else:
        alias_error = None

    if ticker in alias_codes:
        return IdentityAssessment(
            True,
            ticker,
            source,
            verified or source,
            "VERIFIED_ALIAS",
            "source company alias resolves to the same ticker",
        )

    if alias_codes:
        detail = f"source name resolves to {','.join(alias_codes[:5])}, not {ticker}"
        if verified:
            detail += f"; ticker {ticker} basic name is '{verified}'"
        return IdentityAssessment(
            False,
            ticker,
            source,
            verified,
            "SOURCE_IDENTITY_MISMATCH",
            detail,
        )

    reason_bits = []
    if verified:
        reason_bits.append(f"mail name '{source}' != ticker {ticker} basic name '{verified}'")
    if basic_error:
        reason_bits.append(basic_error)
    if alias_error:
        reason_bits.append(f"autocomplete failed: {alias_error}")
    if not reason_bits:
        reason_bits.append("source name could not be resolved to the supplied ticker")
    return IdentityAssessment(
        False,
        ticker,
        source,
        verified,
        "DATA_HOLD",
        "; ".join(reason_bits),
    )
