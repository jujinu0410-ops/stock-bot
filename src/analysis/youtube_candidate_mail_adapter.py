# -*- coding: utf-8 -*-
"""Compatibility adapter for live Economic Intelligence candidate-mail formats.

The ingestion core predates the current V8 mail layout.  Current production-like
reports use a heading such as::

    📌 오늘 언급·주목 종목 (V8 분석 후보군)

and render each candidate as a vertical card::

    삼성전기
    (009150)
    [직접 언급]
    2개 채널 교차 언급
    이유: ...

This adapter recognizes that explicit-code card format conservatively and returns
the same ParseResult/ResolvedMention types used by youtube_candidate_ingest.
Explicit six-digit codes printed in the report are canonical candidate identity;
a stale/incomplete local stock registry must not discard them.  Registry data is
used only to canonicalize the display name/market when available.

If the live-card format is absent, parsing falls back to the existing ingestion
core so older inline/list mail formats remain supported.
"""
from __future__ import annotations

from datetime import date
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.analysis import youtube_candidate_ingest as core
from src.analysis.youtube_candidate_ingest import (
    CandidateParseError,
    ParseResult,
    RawMention,
    ResolvedMention,
    UnresolvedMention,
)


_LIVE_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[📌🔎🏢📈📊⭐✅▶▷►•·*-]\s*)*"
    r"(?:(?:오늘(?:의)?|주요|핵심|최종)\s*)?"
    r"언급\s*(?:(?:[·ㆍ/&+]\s*)?주목\s*)?종목"
    r"(?:\s*(?:목록|리스트))?(?:\s*\([^)]{1,80}\))?(?:\s*TOP\s*\d{1,2})?"
    r"\s*(?P<tail>.*)$",
    re.IGNORECASE,
)

_EXACT_CODE_LINE_RE = re.compile(r"^\s*[\[(]?\s*(\d{6})\s*[\])]??\s*$")
_SAME_LINE_CODE_RE = re.compile(r"^(?P<name>.+?)\s*[\[(]\s*(?P<code>\d{6})\s*[\])]\s*$")
_INFO_LINE_RE = re.compile(r"^\s*※\s*매수\s*추천.*후속\s*분석\s*후보", re.IGNORECASE)
_CARD_TAG_RE = re.compile(r"^\s*\[[^\]]{1,30}\]\s*$")
_CARD_META_RE = re.compile(
    r"^\s*(?:"
    r"\d+\s*개\s*채널|"
    r"이유\s*[:：]|"
    r"근거\s*(?:\([^)]*\))?\s*[:：]|"
    r"방향\s*$|종목명\s*$|언급\s*맥락|핵심\s*모멘텀|"
    r"매수\s*추천|투자\s*판단"
    r")",
    re.IGNORECASE,
)
_STOP_HEADING_RE = re.compile(
    r"^\s*(?:📺|🧾|📝|🔚|Generated\s+by|본\s*리포트는|분석\s*기준\s*:)",
    re.IGNORECASE,
)
_NO_NEW_CANDIDATES_RE = re.compile(
    r"(?:오늘\s*)?(?:신규\s*)?(?:주목\s*)?종목\s*(?:없음|없습니다)",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")


def _strip_line_markup(line: str) -> str:
    s = _clean(line).strip()
    s = re.sub(r"^\s*(?:[-*+•▪◦‣▶▷►]+)\s*", "", s)
    s = s.replace("**", "").replace("__", "").replace("`", "")
    return s.strip()


def _plausible_name(line: str) -> bool:
    s = _strip_line_markup(line)
    if not s or len(s) < 2 or len(s) > 50:
        return False
    if _INFO_LINE_RE.match(s) or _CARD_TAG_RE.match(s) or _CARD_META_RE.match(s):
        return False
    if _NO_NEW_CANDIDATES_RE.search(s):
        return False
    if _EXACT_CODE_LINE_RE.match(s):
        return False
    if s.startswith("http://") or s.startswith("https://"):
        return False
    if re.match(r"^#{1,6}\s", s):
        return False
    if re.search(r"[.!?。]|https?://", s):
        return False
    if len(s.split()) > 5:
        return False
    return bool(re.search(r"[A-Za-z가-힣]", s))


def _extract_live_section(body: str) -> Optional[List[str]]:
    lines = _clean(body).split("\n")
    matches: List[int] = []
    for i, line in enumerate(lines):
        if _LIVE_HEADING_RE.match(line):
            matches.append(i)
    if not matches:
        return None

    start = matches[-1]
    out: List[str] = []
    for raw in lines[start + 1:]:
        line = _strip_line_markup(raw)
        if not line:
            out.append("")
            continue
        if _INFO_LINE_RE.match(line):
            continue
        if _STOP_HEADING_RE.match(line):
            break
        if re.match(r"^\s*#{1,6}\s+\S", raw):
            break
        out.append(line)
    return out


def _registry_indexes(stock_rows: Iterable[Mapping[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    return core.build_stock_registry(stock_rows)


def extract_live_card_mentions(body: str) -> List[RawMention]:
    """Extract only explicit-code candidates from the current vertical-card layout."""
    lines = _extract_live_section(body)
    if lines is None:
        return []

    mentions: List[RawMention] = []
    seen_codes = set()

    for line in lines:
        m = _SAME_LINE_CODE_RE.match(_strip_line_markup(line))
        if not m:
            continue
        name = _strip_line_markup(m.group("name")).strip(" -–—:：")
        code = m.group("code")
        if _plausible_name(name) and code not in seen_codes:
            mentions.append(RawMention(raw_text=name, explicit_code=code))
            seen_codes.add(code)

    nonempty = [(idx, _strip_line_markup(v)) for idx, v in enumerate(lines) if _strip_line_markup(v)]
    for pos in range(len(nonempty) - 1):
        _, name_line = nonempty[pos]
        _, code_line = nonempty[pos + 1]
        cm = _EXACT_CODE_LINE_RE.match(code_line)
        if not cm or not _plausible_name(name_line):
            continue
        code = cm.group(1)
        if code in seen_codes:
            continue
        mentions.append(RawMention(raw_text=name_line, explicit_code=code))
        seen_codes.add(code)

    return mentions


def resolve_explicit_live_mentions(
    raw_mentions: Sequence[RawMention],
    stock_rows: Iterable[Mapping[str, Any]],
) -> Tuple[List[ResolvedMention], List[UnresolvedMention]]:
    """Resolve live-card mentions without requiring an exhaustive local registry."""
    by_code, _ = _registry_indexes(stock_rows)
    resolved: Dict[str, ResolvedMention] = {}
    unresolved: List[UnresolvedMention] = []
    for mention in raw_mentions:
        code = core._norm_code(mention.explicit_code)
        if not code:
            unresolved.append(UnresolvedMention(mention.raw_text, "INVALID_EXPLICIT_CODE"))
            continue
        item = by_code.get(code)
        name = str(item.get("stock_name") if item else mention.raw_text).strip()
        if not name:
            unresolved.append(UnresolvedMention(mention.raw_text, "EMPTY_NAME_FOR_EXPLICIT_CODE"))
            continue
        resolved.setdefault(
            code,
            ResolvedMention(
                ticker=code,
                name=name,
                raw_text=mention.raw_text,
                market_type=item.get("market_type") if item else None,
            ),
        )
    return list(resolved.values()), unresolved


def parse_and_resolve_mail(
    subject: str,
    body: str,
    stock_rows: Iterable[Mapping[str, Any]],
    fallback_date: Optional[date] = None,
) -> ParseResult:
    """Parse current live-card mail first, then fall back to the legacy core parser."""
    rows = list(stock_rows)
    report_date = core.parse_report_date(subject, body, fallback=fallback_date)

    live_section = _extract_live_section(body)
    if live_section is not None and any(_NO_NEW_CANDIDATES_RE.search(line) for line in live_section):
        # An explicit 'no new candidates today' report is a valid zero-event input.
        # It must not be treated as a parser failure and, critically, must not clear
        # the rolling 30-day candidate pool.
        return ParseResult(
            report_date=report_date,
            raw_mentions=tuple(),
            resolved=tuple(),
            unresolved=tuple(),
            source_subject=subject,
        )

    live_mentions = extract_live_card_mentions(body)
    if live_mentions:
        resolved, unresolved = resolve_explicit_live_mentions(live_mentions, rows)
        if not resolved:
            raise CandidateParseError("live candidate cards found but no explicit codes resolved")
        return ParseResult(
            report_date=report_date,
            raw_mentions=tuple(live_mentions),
            resolved=tuple(resolved),
            unresolved=tuple(unresolved),
            source_subject=subject,
        )

    normalized = _clean(body)
    normalized = re.sub(
        r"(?m)^\s*(?:📌\s*)?(?:오늘(?:의)?\s*)?언급\s*(?:[·ㆍ/&+]\s*)?주목\s*종목",
        "언급종목",
        normalized,
        count=0,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"(?m)^\s*※\s*매수\s*추천.*후속\s*분석\s*후보.*$", "", normalized)
    return core.parse_and_resolve_mail(subject, normalized, rows, fallback_date)
