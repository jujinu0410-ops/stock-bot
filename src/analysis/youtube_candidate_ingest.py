# -*- coding: utf-8 -*-
"""
YouTube Candidate Watch V0 - ingestion core (sidecar / fail-closed)

Scope of this module:
- Find the final "언급종목" section from an Economic Intelligence mail body.
- Extract conservative stock-name/code tokens from that section only.
- Resolve names to canonical 6-digit KRX stock codes using stock_info-like rows.
- Dedupe by canonical stock code.
- Build idempotent candidate upserts with 30-calendar-day TTL.

Explicit non-goals:
- No order API calls.
- No portfolio stop/target mutation.
- No technical BUY_ALERT judgement.
- No Google Sheet or Gmail writes.

Canonical ticker in this ingestion layer is the 6-digit stock code.  Market-specific
GoogleFinance symbols are intentionally deferred to the market-data layer so a KOSDAQ
classification error can never corrupt candidate identity.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class CandidateParseError(ValueError):
    """Raised when the mentions section cannot be parsed safely."""


@dataclass(frozen=True)
class RawMention:
    raw_text: str
    explicit_code: Optional[str] = None


@dataclass(frozen=True)
class ResolvedMention:
    ticker: str  # canonical 6-digit stock code
    name: str
    raw_text: str
    market_type: Optional[str] = None


@dataclass(frozen=True)
class UnresolvedMention:
    raw_text: str
    reason: str


@dataclass(frozen=True)
class CandidateRecord:
    ticker: str
    name: str
    first_seen_date: date
    last_seen_date: date
    mention_count_30d: int
    source_subject_latest: str
    expires_at: date
    holding_status: str = "NOT_HELD"
    candidate_status: str = "ACTIVE"
    last_signal_at: Optional[datetime] = None


@dataclass(frozen=True)
class ParseResult:
    report_date: date
    raw_mentions: Tuple[RawMention, ...]
    resolved: Tuple[ResolvedMention, ...]
    unresolved: Tuple[UnresolvedMention, ...]
    source_subject: str = ""


# The handover document says the source is the *last* 언급종목 section.
# Be permissive about decorations but strict about the semantic heading.
_MENTION_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[📌🔎🏢📈📊⭐✅▶▷►•·*-]\s*)*"
    r"(?:(?:오늘의|주요|핵심|최종)\s*)?언급\s*종목"
    r"(?:\s*(?:목록|리스트))?(?:\s*\([^)]{1,40}\))?(?:\s*TOP\s*\d{1,2})?"
    r"\s*(?P<tail>.*)$",
    re.IGNORECASE,
)

_REPORT_DATE_RE = re.compile(r"분석\s*기준일\s*:\s*(20\d{2})[-./](\d{1,2})[-./](\d{1,2})")
_SUBJECT_MD_RE = re.compile(r"(?<!\d)(\d{1,2})월\s*(\d{1,2})일")
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")

# Footer/next-section markers.  Because the source section should be last, these are
# only a safety brake for accidental template additions after it.
_STOP_LINE_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:※|주의|면책|Disclaimer|Generated\s+by|데이터\s*출처|끝\s*$)",
    re.IGNORECASE,
)

# Labels that may appear inside a mentions section but are not stock names.
_INLINE_LABEL_RE = re.compile(
    r"^\s*(?:종목|언급종목|주요\s*종목|관련\s*종목|기업|언급\s*기업)\s*[:：]\s*",
    re.IGNORECASE,
)

_SPLIT_RE = re.compile(r"\s*(?:,|，|;|；|·|ㆍ|/|\||→|▶|►)\s*")
_BULLET_RE = re.compile(r"^\s*(?:[-*+•▪◦‣▶▷►]+|\d{1,2}[.)]|\(\d{1,2}\))\s*")

# Things that should never become a raw stock candidate by themselves.
_GENERIC_TOKENS = {
    "종목", "없음", "해당없음", "해당 없음", "n/a", "na", "없습니다", "미확인",
    "반도체", "ai", "금리", "통화정책", "외국인수급", "외국인 수급", "시장",
    "코스피", "코스닥", "etf", "fomc", "미국", "한국", "경제",
}


def _clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_report_date(subject: str, body: str, fallback: Optional[date] = None) -> date:
    """Resolve the report date, preferring the explicit body date over subject text."""
    body = _clean_text(body)
    m = _REPORT_DATE_RE.search(body)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = _SUBJECT_MD_RE.search(_clean_text(subject))
    if m:
        if fallback is None:
            raise CandidateParseError("subject has month/day but no year fallback")
        return date(fallback.year, int(m.group(1)), int(m.group(2)))

    if fallback is not None:
        return fallback
    raise CandidateParseError("report date not found")


def extract_mentions_section(body: str) -> str:
    """
    Return only the final mentions section.

    Fail-closed rule: if the section heading is absent or the resulting section is blank,
    raise CandidateParseError.  Never interpret that as "0 candidates".
    """
    body = _clean_text(body)
    lines = body.split("\n")
    matches: List[Tuple[int, str]] = []
    for idx, line in enumerate(lines):
        m = _MENTION_HEADING_RE.match(line)
        if m:
            tail = (m.group("tail") or "").strip()
            # Strip a pure separator after the heading, while preserving inline names.
            tail = re.sub(r"^[\s:：\-–—]+", "", tail).strip()
            matches.append((idx, tail))

    if not matches:
        raise CandidateParseError("mentions section heading not found")

    start_idx, inline_tail = matches[-1]
    out: List[str] = []
    if inline_tail:
        out.append(inline_tail)

    for line in lines[start_idx + 1 :]:
        if _STOP_LINE_RE.match(line):
            break
        # A new major Markdown heading after the mentions section is a safe stop.
        if re.match(r"^\s*#{1,6}\s+\S", line):
            break
        out.append(line)

    section = "\n".join(out).strip()
    if not section:
        raise CandidateParseError("mentions section is empty")
    return section


def _strip_markup(token: str) -> str:
    token = _clean_text(token).strip()
    token = _MD_LINK_RE.sub(r"\1", token)
    token = token.replace("**", "").replace("__", "").replace("`", "")
    token = _BULLET_RE.sub("", token).strip()
    token = _INLINE_LABEL_RE.sub("", token).strip()
    # remove common annotations while keeping an explicit 6-digit code available
    token = re.sub(r"\s*\[(?:KOSPI|KOSDAQ|KRX)\]\s*", " ", token, flags=re.I)
    token = re.sub(r"\s+(?:상승|하락|강세|약세|수혜|관련주|관심)$", "", token).strip()
    return token.strip(" \t-–—:：·•|/")


def _candidate_name_from_token(token: str) -> str:
    # Remove an explicit stock code and surrounding brackets/parentheses.
    name = _CODE_RE.sub("", token)
    name = re.sub(r"[\(\)\[\]{}]", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -–—:：")
    return name


def extract_raw_mentions(section: str) -> List[RawMention]:
    """Extract conservative mention tokens from the already-isolated section."""
    section = _clean_text(section)
    results: List[RawMention] = []

    for raw_line in section.split("\n"):
        line = _strip_markup(raw_line)
        if not line:
            continue
        if re.fullmatch(r"[:|\-–—\s]+", line):
            continue
        # Markdown table delimiter row.
        if re.fullmatch(r"(?:\s*:?-{3,}:?\s*\|?)+", line):
            continue

        # If an explanatory colon exists, prefer the left side when it looks like a
        # compact stock token; otherwise retain the whole line and let resolution fail.
        colon_parts = re.split(r"[:：]", line, maxsplit=1)
        if len(colon_parts) == 2 and 1 <= len(colon_parts[0].strip()) <= 30:
            left = colon_parts[0].strip()
            # "관련 종목: A, B" was already stripped by _INLINE_LABEL_RE.  For an actual
            # stock line such as "삼성전자: HBM", keep only the stock token.
            if left and left.lower() not in _GENERIC_TOKENS:
                line = left

        chunks = [c for c in _SPLIT_RE.split(line) if c.strip()]
        for chunk in chunks:
            token = _strip_markup(chunk)
            if not token:
                continue
            explicit = None
            m = _CODE_RE.search(token)
            if m:
                explicit = m.group(1)

            name = _candidate_name_from_token(token)
            generic_key = re.sub(r"\s+", " ", name).strip().lower()
            if generic_key in _GENERIC_TOKENS and not explicit:
                continue
            if not explicit:
                # Too long usually means prose, not a stock token.  Resolution will be
                # strict later, but filtering obvious prose reduces noise.
                if len(name) < 2 or len(name) > 40:
                    continue
                if len(name.split()) > 5:
                    continue
            results.append(RawMention(raw_text=name or token, explicit_code=explicit))

    if not results:
        raise CandidateParseError("mentions section parsed but no candidate tokens were extracted")
    return results


def _norm_name(name: str) -> str:
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("㈜", "").replace("(주)", "").replace("주식회사", "")
    name = re.sub(r"[^0-9A-Za-z가-힣]", "", name).upper()
    return name


def _norm_code(value: Any) -> str:
    s = re.sub(r"\D", "", str(value or ""))
    return s.zfill(6) if 1 <= len(s) <= 6 else ""


def build_stock_registry(stock_rows: Iterable[Mapping[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Build code/name indexes from rows shaped like stock_info.

    Required fields: stock_code, stock_name.  market_type is optional and is carried only
    as metadata; it is not used to construct the canonical candidate identity.
    """
    by_code: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    ambiguous_names = set()

    for row in stock_rows:
        code = _norm_code(row.get("stock_code"))
        name = str(row.get("stock_name") or "").strip()
        if not code or not name:
            continue
        item = {
            "stock_code": code,
            "stock_name": name,
            "market_type": row.get("market_type"),
        }
        by_code[code] = item
        key = _norm_name(name)
        if not key:
            continue
        if key in by_name and by_name[key]["stock_code"] != code:
            ambiguous_names.add(key)
        else:
            by_name[key] = item

    for key in ambiguous_names:
        by_name.pop(key, None)
    return by_code, by_name


def resolve_mentions(
    raw_mentions: Sequence[RawMention],
    stock_rows: Iterable[Mapping[str, Any]],
) -> Tuple[List[ResolvedMention], List[UnresolvedMention]]:
    """Resolve and dedupe mentions by canonical six-digit stock code."""
    by_code, by_name = build_stock_registry(stock_rows)
    resolved_by_code: Dict[str, ResolvedMention] = {}
    unresolved: List[UnresolvedMention] = []

    for mention in raw_mentions:
        item: Optional[Dict[str, Any]] = None
        if mention.explicit_code:
            item = by_code.get(_norm_code(mention.explicit_code))
            if item is None:
                unresolved.append(UnresolvedMention(mention.raw_text, "EXPLICIT_CODE_NOT_IN_REGISTRY"))
                continue
        else:
            key = _norm_name(mention.raw_text)
            item = by_name.get(key)
            if item is None:
                unresolved.append(UnresolvedMention(mention.raw_text, "NAME_NOT_RESOLVED"))
                continue

        code = item["stock_code"]
        resolved_by_code.setdefault(
            code,
            ResolvedMention(
                ticker=code,
                name=item["stock_name"],
                raw_text=mention.raw_text,
                market_type=item.get("market_type"),
            ),
        )

    return list(resolved_by_code.values()), unresolved


def parse_and_resolve_mail(
    subject: str,
    body: str,
    stock_rows: Iterable[Mapping[str, Any]],
    fallback_date: Optional[date] = None,
) -> ParseResult:
    report_date = parse_report_date(subject, body, fallback=fallback_date)
    section = extract_mentions_section(body)
    raw_mentions = extract_raw_mentions(section)
    resolved, unresolved = resolve_mentions(raw_mentions, stock_rows)

    # Fail-closed: a parse that produced text but resolved absolutely nothing is not a
    # valid "zero candidates" run.  Callers should surface this as a data-quality error.
    if not resolved:
        raise CandidateParseError("candidate tokens found but none resolved to stock codes")

    return ParseResult(
        report_date=report_date,
        raw_mentions=tuple(raw_mentions),
        resolved=tuple(resolved),
        unresolved=tuple(unresolved),
        source_subject=subject,
    )


def aggregate_candidate_pool(
    parse_results: Sequence[ParseResult],
    as_of_date: date,
    held_tickers: Optional[Iterable[str]] = None,
    ttl_days: int = 30,
    existing_by_ticker: Optional[Mapping[str, CandidateRecord]] = None,
) -> List[CandidateRecord]:
    """
    Rebuild the active candidate pool from the rolling mail window.

    This is the preferred V0 operation because it is naturally idempotent:
    - duplicate copies of one report day count once per ticker/day,
    - missed scheduler runs are healed on the next rebuild,
    - mention_count_30d is a real rolling count rather than an ever-growing counter.
    """
    if ttl_days <= 0:
        raise ValueError("ttl_days must be positive")

    held = {_norm_code(x) for x in (held_tickers or []) if _norm_code(x)}
    existing_by_ticker = dict(existing_by_ticker or {})
    window_start = as_of_date - timedelta(days=ttl_days - 1)

    # ticker -> report_date -> (mention, subject)
    events: Dict[str, Dict[date, Tuple[ResolvedMention, str]]] = {}
    for result in parse_results:
        if result.report_date > as_of_date or result.report_date < window_start:
            continue
        for mention in result.resolved:
            events.setdefault(mention.ticker, {})[result.report_date] = (mention, result.source_subject)

    rows: List[CandidateRecord] = []
    for ticker in sorted(events):
        dated = events[ticker]
        dates = sorted(dated)
        first_in_window = dates[0]
        last_seen = dates[-1]
        latest_mention, latest_subject = dated[last_seen]
        existing = existing_by_ticker.get(ticker)

        first_seen = first_in_window
        last_signal_at = None
        if existing is not None:
            first_seen = min(existing.first_seen_date, first_in_window)
            last_signal_at = existing.last_signal_at

        holding_status = "HELD" if ticker in held else "NOT_HELD"
        rows.append(
            CandidateRecord(
                ticker=ticker,
                name=latest_mention.name,
                first_seen_date=first_seen,
                last_seen_date=last_seen,
                mention_count_30d=len(dates),
                source_subject_latest=latest_subject,
                expires_at=last_seen + timedelta(days=ttl_days),
                holding_status=holding_status,
                candidate_status="HELD" if holding_status == "HELD" else "ACTIVE",
                last_signal_at=last_signal_at,
            )
        )

    return rows


def upsert_candidate_record(
    existing: Optional[CandidateRecord],
    mention: ResolvedMention,
    seen_date: date,
    source_subject: str,
    ttl_days: int = 30,
    holding_status: str = "NOT_HELD",
) -> CandidateRecord:
    """
    Build the next candidate record without mutating external state.

    Idempotency rule: repeated copies of the same daily report do not increment the
    mention counter again when last_seen_date == seen_date.  This is important because
    duplicate Intelligence emails can occur.
    """
    if ttl_days <= 0:
        raise ValueError("ttl_days must be positive")

    expires_at = seen_date + timedelta(days=ttl_days)
    if existing is None:
        return CandidateRecord(
            ticker=mention.ticker,
            name=mention.name,
            first_seen_date=seen_date,
            last_seen_date=seen_date,
            mention_count_30d=1,
            source_subject_latest=source_subject,
            expires_at=expires_at,
            holding_status=holding_status,
            candidate_status="HELD" if holding_status == "HELD" else "ACTIVE",
        )

    if existing.ticker != mention.ticker:
        raise ValueError("existing record ticker does not match mention")

    increment = 0 if existing.last_seen_date == seen_date else 1
    new_status = "HELD" if holding_status == "HELD" else "ACTIVE"
    return replace(
        existing,
        name=mention.name,
        last_seen_date=max(existing.last_seen_date, seen_date),
        mention_count_30d=max(0, int(existing.mention_count_30d)) + increment,
        source_subject_latest=source_subject,
        expires_at=expires_at,
        holding_status=holding_status,
        candidate_status=new_status,
    )


def expire_candidate(record: CandidateRecord, as_of_date: date) -> CandidateRecord:
    """Mark expired candidates without deleting history."""
    if record.holding_status == "HELD":
        return replace(record, candidate_status="HELD")
    if as_of_date > record.expires_at:
        return replace(record, candidate_status="EXPIRED")
    return replace(record, candidate_status="ACTIVE")


def build_dry_run_rows(
    parse_result: ParseResult,
    existing_by_ticker: Optional[Mapping[str, CandidateRecord]] = None,
    source_subject: str = "",
    held_tickers: Optional[Iterable[str]] = None,
    ttl_days: int = 30,
) -> List[CandidateRecord]:
    """Pure dry-run helper: compute candidate records but write nothing."""
    existing_by_ticker = dict(existing_by_ticker or {})
    held = {_norm_code(x) for x in (held_tickers or []) if _norm_code(x)}
    rows: List[CandidateRecord] = []
    for mention in parse_result.resolved:
        status = "HELD" if mention.ticker in held else "NOT_HELD"
        rows.append(
            upsert_candidate_record(
                existing=existing_by_ticker.get(mention.ticker),
                mention=mention,
                seen_date=parse_result.report_date,
                source_subject=source_subject,
                ttl_days=ttl_days,
                holding_status=status,
            )
        )
    return rows
