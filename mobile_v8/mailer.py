from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage


def send_mobile_v8_email(
    *,
    subject: str,
    body_html: str,
    report_hash: str,
    attachment_name: str,
    attachment_text: str,
) -> str:
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("RECIPIENT_GMAIL") or gmail_user
    if not gmail_user or not gmail_password:
        raise RuntimeError("Missing GMAIL_USER or GMAIL_APP_PASSWORD")
    if not re.fullmatch(r"[0-9a-f]{64}", str(report_hash or "")):
        raise ValueError("report_hash must be a SHA-256 hex digest")
    if not attachment_text:
        raise ValueError("attachment_text is empty")

    domain = gmail_user.rsplit("@", 1)[-1] if "@" in gmail_user else "gmail.com"
    message_id = f"<mobile-v8-{report_hash[:32]}@{domain}>"

    message = EmailMessage()
    message["From"] = gmail_user
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["X-Mobile-V8-Report-Hash"] = report_hash
    message.set_content(
        "V8 정량분석 결과입니다. HTML을 지원하는 메일 앱에서 본문 요약을 확인하거나, "
        "첨부된 Markdown 파일을 ChatGPT에 업로드해 상세 분석을 요청할 수 있습니다."
    )
    message.add_alternative(body_html, subtype="html")
    message.add_attachment(
        attachment_text.encode("utf-8"),
        maintype="text",
        subtype="markdown",
        filename=attachment_name,
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=45) as server:
        server.login(gmail_user, gmail_password)
        refused = server.send_message(message)
    if refused:
        raise RuntimeError("Gmail refused one or more recipients")
    return message_id
