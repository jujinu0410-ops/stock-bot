# -*- coding: utf-8 -*-
"""Fast validation wrapper for the first YouTube Candidate Watch integrated dry run.

This wrapper keeps the core evaluator unchanged but adapts the current Economic
Intelligence V8 mail layout and removes expensive/unreliable auxiliary operations:
1) no full-market public stock-master download; explicit six-digit codes in the
   report remain canonical candidate identity,
2) Gmail IMAP is server-filtered by the ASCII subject token "Intelligence" before
   BODY.PEEK[] so unrelated Inbox mail is never downloaded,
3) when GitHub's dynamic IP is rejected by Kiwoom, the validation-only flow source
   uses Naver foreign/institution net share volume converted to an estimated amount
   (net volume x close). Mobile JSON is attempted first and the page-based Naver PC
   table supplies older sessions when the mobile endpoints expose only ~5 sessions.
   It is explicitly labelled NAVER_ESTIMATE, never Kiwoom exact flow.

FAILED/DEGRADED status notifications are not candidate reports and are excluded.
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
from src.analysis import youtube_candidate_mail_adapter as live_adapter
from src.analysis.youtube_candidate_naver_flow import fetch_naver_flow
from src.analysis.youtube_candidate_naver_pc_flow import fetch_naver_pc_flow


def _no_public_registry():
    return []


def _is_status_only_subject(subject: str) -> bool:
    upper = (subject or "").upper()
    return "FAILED" in upper or "DEGRADED" in upper or "파이프라인 실행 오류" in subject


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
            if _is_status_only_subject(subject):
                continue

            rows.append({
                "imap_id": msg_id.decode("ascii", errors="ignore"),
                "message_id": str(msg.get("Message-ID") or ""),
                "subject": subject,
                "received_date": mailmod._parse_email_date(msg),
                "body": mailmod._extract_message_text(msg),
            })

    return rows


def _naver_flow_fallback(code: str, as_of_date) -> List[Dict[str, Any]]:
    """Validation-only 20d Naver fallback; exact production source remains Kiwoom."""
    mobile_rows: List[Dict[str, Any]] = []
    try:
        mobile_rows = fetch_naver_flow(code, as_of_date, pages=2, exclude_as_of_date=True)
    except Exception:
        mobile_rows = []

    if len(mobile_rows) >= 20:
        return mobile_rows

    pc_rows: List[Dict[str, Any]] = []
    try:
        pc_rows = fetch_naver_pc_flow(code, as_of_date, pages=2, exclude_as_of_date=True)
    except Exception:
        pc_rows = []

    # PC rows provide the longer history; mobile rows override overlapping recent
    # dates because the mobile JSON schema is more structured.  If the merged set is
    # still <20 sessions, evaluate_flow() deliberately returns UNAVAILABLE.
    merged = {str(row.get("dt")): row for row in pc_rows if row.get("dt")}
    merged.update({str(row.get("dt")): row for row in mobile_rows if row.get("dt")})
    return [merged[k] for k in sorted(merged)]


_original_flow_dict = runner.flow_dict

def _label_flow_source(flow, source: str):
    if source == "PYKRX_KRX_FALLBACK":
        source = "NAVER_NET_VOLUME_X_CLOSE_ESTIMATE"
    return _original_flow_dict(flow, source)


runner.fetch_public_registry = _no_public_registry
runner.fetch_intelligence_mails = _fast_fetch_intelligence_mails
runner.parse_and_resolve_mail = live_adapter.parse_and_resolve_mail
runner.fetch_pykrx_flow = _naver_flow_fallback
runner.flow_dict = _label_flow_source

if __name__ == "__main__":
    raise SystemExit(runner.main())
