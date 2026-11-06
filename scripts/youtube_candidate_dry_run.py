# -*- coding: utf-8 -*-
"""
Read-only dry-run runner for YouTube Candidate Watch V0.

Safety contract:
- Gmail is opened READ-ONLY through IMAP.
- stock_system.db is queried only with SELECT statements.
- No Google Sheet write, no Gmail send, no portfolio mutation, no order API call.
- Parser/resolution problems are surfaced as DATA_REVIEW instead of being interpreted
  as an empty candidate universe.

Typical local run:
    python scripts/youtube_candidate_dry_run.py

Optional:
    python scripts/youtube_candidate_dry_run.py --days 35 --json-out logs/youtube_candidate_dry_run.json
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from html import unescape
from html.parser import HTMLParser
import imaplib
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import GMAIL_APP_PASSWORD, GMAIL_USER
from src.analysis.youtube_candidate_ingest import (
    CandidateParseError,
    ParseResult,
    aggregate_candidate_pool,
    parse_and_resolve_mail,
)
from src.database.db_manager import DatabaseManager

KST = ZoneInfo("Asia/Seoul")
SUBJECT_PREFIX = "[경제 Intelligence]"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def get_text(self) -> str:
        return unescape("\n".join(self.parts))


def _decode_header_value(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _payload_text(part: Any) -> str:
    try:
        content = part.get_content()
        if isinstance(content, str):
            return content
    except Exception:
        pass

    raw = part.get_payload(decode=True)
    if not raw:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        return raw.decode("utf-8", errors="replace")


def _extract_message_text(msg: Any) -> str:
    plain: List[str] = []
    html: List[str] = []

    parts: Iterable[Any] = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disp:
            continue
        ctype = (part.get_content_type() or "").lower()
        if ctype == "text/plain":
            text = _payload_text(part)
            if text.strip():
                plain.append(text)
        elif ctype == "text/html":
            text = _payload_text(part)
            if text.strip():
                html.append(text)

    if plain:
        return "\n".join(plain)

    if html:
        parser = _HTMLTextExtractor()
        try:
            parser.feed("\n".join(html))
            return parser.get_text()
        except Exception:
            # Last-resort plain conversion; still read-only and fail-closed downstream.
            return re.sub(r"<[^>]+>", " ", "\n".join(html))

    return ""


def _parse_email_date(msg: Any) -> Optional[date]:
    from email.utils import parsedate_to_datetime

    raw = msg.get("Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST).date()
    except Exception:
        return None


def fetch_intelligence_mails(days: int, as_of_date: date) -> List[Dict[str, Any]]:
    """Fetch matching Inbox messages without changing read/unread state."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_USER/GMAIL_APP_PASSWORD is not configured")
    if days <= 0:
        raise ValueError("days must be positive")

    since_date = as_of_date - timedelta(days=days - 1)
    since_arg = since_date.strftime("%d-%b-%Y")
    rows: List[Dict[str, Any]] = []

    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
        imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        typ, _ = imap.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError("Gmail INBOX could not be opened read-only")

        typ, data = imap.search(None, "SINCE", since_arg)
        if typ != "OK":
            raise RuntimeError("Gmail SINCE search failed")

        ids = data[0].split() if data and data[0] else []
        for msg_id in ids:
            # PEEK prevents a message from being marked as read even if a provider ignores
            # readonly semantics for a specific fetch variant.
            typ, fetched = imap.fetch(msg_id, "(BODY.PEEK[])")
            if typ != "OK" or not fetched:
                continue

            raw_bytes = None
            for item in fetched:
                if isinstance(item, tuple) and isinstance(item[1], (bytes, bytearray)):
                    raw_bytes = bytes(item[1])
                    break
            if not raw_bytes:
                continue

            msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)
            subject = _decode_header_value(msg.get("Subject"))
            if not subject.startswith(SUBJECT_PREFIX):
                continue

            body = _extract_message_text(msg)
            rows.append(
                {
                    "imap_id": msg_id.decode("ascii", errors="ignore"),
                    "message_id": str(msg.get("Message-ID") or ""),
                    "subject": subject,
                    "received_date": _parse_email_date(msg),
                    "body": body,
                }
            )

    return rows


def load_registry_and_holdings(db: DatabaseManager) -> Tuple[List[Dict[str, Any]], List[str]]:
    stock_rows = [
        dict(row)
        for row in db.execute_query(
            "SELECT stock_code, stock_name, market_type FROM stock_info ORDER BY stock_code"
        )
    ]
    held_tickers = [
        str(row["stock_code"]).zfill(6)
        for row in db.execute_query(
            "SELECT stock_code FROM portfolio_positions WHERE quantity > 0 ORDER BY stock_code"
        )
    ]
    return stock_rows, held_tickers


def run_dry_run(days: int = 35, as_of_date: Optional[date] = None) -> Dict[str, Any]:
    as_of_date = as_of_date or datetime.now(KST).date()
    db = DatabaseManager()
    stock_rows, held_tickers = load_registry_and_holdings(db)
    mails = fetch_intelligence_mails(days=days, as_of_date=as_of_date)

    parsed: List[ParseResult] = []
    parse_failures: List[Dict[str, str]] = []
    unresolved: List[Dict[str, str]] = []

    for mail in mails:
        fallback = mail.get("received_date") or as_of_date
        try:
            result = parse_and_resolve_mail(
                subject=mail["subject"],
                body=mail["body"],
                stock_rows=stock_rows,
                fallback_date=fallback,
            )
            parsed.append(result)
            for item in result.unresolved:
                unresolved.append(
                    {
                        "report_date": result.report_date.isoformat(),
                        "subject": result.source_subject,
                        "raw_text": item.raw_text,
                        "reason": item.reason,
                    }
                )
        except CandidateParseError as exc:
            parse_failures.append(
                {
                    "subject": mail["subject"],
                    "received_date": fallback.isoformat(),
                    "reason": str(exc),
                }
            )

    pool = aggregate_candidate_pool(
        parse_results=parsed,
        as_of_date=as_of_date,
        held_tickers=held_tickers,
        ttl_days=30,
    )

    data_quality = "VALID"
    if not mails or not parsed or parse_failures or unresolved:
        data_quality = "DATA_REVIEW"

    candidates = [
        {
            "ticker": row.ticker,
            "name": row.name,
            "first_seen_date": row.first_seen_date.isoformat(),
            "last_seen_date": row.last_seen_date.isoformat(),
            "mention_count_30d": row.mention_count_30d,
            "expires_at": row.expires_at.isoformat(),
            "holding_status": row.holding_status,
            "candidate_status": row.candidate_status,
            "source_subject_latest": row.source_subject_latest,
        }
        for row in pool
    ]

    return {
        "run_mode": "DRY_RUN_READ_ONLY",
        "as_of_date": as_of_date.isoformat(),
        "lookback_days_fetched": days,
        "candidate_ttl_days": 30,
        "data_quality": data_quality,
        "mail_count": len(mails),
        "parsed_mail_count": len(parsed),
        "parse_failure_count": len(parse_failures),
        "unresolved_count": len(unresolved),
        "candidate_count": len(candidates),
        "held_candidate_count": sum(1 for x in candidates if x["holding_status"] == "HELD"),
        "nonheld_candidate_count": sum(1 for x in candidates if x["holding_status"] != "HELD"),
        "parse_failures": parse_failures,
        "unresolved": unresolved,
        "candidates": candidates,
        "safety": {
            "gmail_readonly": True,
            "db_select_only": True,
            "sheet_write": False,
            "email_send": False,
            "portfolio_mutation": False,
            "order_api": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube Candidate Watch read-only dry run")
    parser.add_argument("--days", type=int, default=35, help="Gmail fetch lookback (default: 35)")
    parser.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD; default: today KST")
    parser.add_argument("--json-out", type=Path, default=None, help="optional JSON output file")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    result = run_dry_run(days=args.days, as_of_date=as_of)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")

    # DATA_REVIEW is deliberately non-zero so a future scheduler cannot silently treat
    # an incomplete candidate set as trustworthy.
    return 0 if result["data_quality"] == "VALID" else 2


if __name__ == "__main__":
    raise SystemExit(main())
