# -*- coding: utf-8 -*-
"""Fast validation wrapper for the first YouTube Candidate Watch integrated dry run.

This wrapper keeps the core evaluator unchanged but removes two expensive auxiliary
operations from the first real-data validation:
1) no full-market public stock-master download; registry gaps stay UNRESOLVED,
2) Gmail IMAP is server-filtered by the ASCII subject token "Intelligence" before
   BODY.PEEK[] so unrelated Inbox mail is never downloaded.

All access remains read-only.
"""
from __future__ import annotations

from datetime import timedelta
from email import policy
from email.parser import BytesParser
import imaplib
from typing import Any, Dict, List

import scripts.youtube_candidate_dry_run as mailmod
import scripts.youtube_candidate_integrated_dry_run as runner


def _no_public_registry():
    return []


def _fast_fetch_intelligence_mails(days: int, as_of_date) -> List[Dict[str, Any]]:
    if not mailmod.GMAIL_USER or not mailmod.GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_USER/GMAIL_APP_PASSWORD is not configured")
    if days <= 0:
        raise ValueError("days must be positive")

    since_date = as_of_date - timedelta(days=days - 1)
    since_arg = since_date.strftime("%d-%b-%Y")
    rows: List[Dict[str, Any]] = []

    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
        imap.login(mailmod.GMAIL_USER, mailmod.GMAIL_APP_PASSWORD)
        typ, _ = imap.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError("Gmail INBOX could not be opened read-only")

        # ASCII token is deliberate: IMAP search remains portable while shrinking
        # the fetch set from the whole Inbox to the Economic Intelligence reports.
        typ, data = imap.search(None, "SINCE", since_arg, "SUBJECT", '"Intelligence"')
        if typ != "OK":
            raise RuntimeError("Gmail filtered search failed")

        ids = data[0].split() if data and data[0] else []
        for msg_id in ids:
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
            subject = mailmod._decode_header_value(msg.get("Subject"))
            if not subject.startswith(mailmod.SUBJECT_PREFIX):
                continue

            rows.append({
                "imap_id": msg_id.decode("ascii", errors="ignore"),
                "message_id": str(msg.get("Message-ID") or ""),
                "subject": subject,
                "received_date": mailmod._parse_email_date(msg),
                "body": mailmod._extract_message_text(msg),
            })

    return rows


runner.fetch_public_registry = _no_public_registry
runner.fetch_intelligence_mails = _fast_fetch_intelligence_mails

if __name__ == "__main__":
    raise SystemExit(runner.main())
