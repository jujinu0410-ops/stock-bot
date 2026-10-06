from __future__ import annotations

from typing import Any


def _cell(value: Any) -> str:
    if value in (None, ""):
        return "MISSING"
    return str(value).replace("\n", " ").replace("|", r"\|")


def inject_research_extension(markdown: str, research: dict[str, Any]) -> str:
    """Insert current Naver research metadata inside section 05 of the unified MD.

    The historical offline package exposed target price/opinion and PDF evidence.
    The base renderer predates Naver Stock's current JSON API, so this keeps the
    00~07 structure while preserving the richer current source fields without
    rewriting the stable renderer.
    """
    marker = "\n# 06. DART 정기보고서 원문 핵심자료"
    if marker not in markdown:
        return markdown

    reports = research.get("reports") or []
    lines = [
        "",
        "## 현재 Naver Stock API 확장 metadata",
        "",
        f"- source endpoint: {_cell(research.get('source_endpoint'))}",
        f"- source total count: {_cell(research.get('source_total_count'))}",
        f"- lookback days: {_cell(research.get('lookback_days'))}",
        f"- max collected reports: {_cell(research.get('max_reports'))}",
        f"- latest report date: {_cell(research.get('latest_report_date'))}",
        f"- broker count: {_cell(research.get('broker_count'))}",
        "",
    ]
    if not reports:
        lines.append("- NOT_AVAILABLE / NO_REPORTS_FOUND")
    else:
        headers = [
            "날짜", "증권사", "제목", "목표가", "이전목표가", "투자의견",
            "작성시점주가", "report ID", "detail status", "PDF status",
            "PDF hash", "PDF bytes", "content hash", "본문요약",
        ]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join("---" for _ in headers) + "|")
        for report in reports:
            summary = report.get("content_text")
            if isinstance(summary, str) and len(summary) > 500:
                summary = summary[:500] + "…"
            row = [
                report.get("date"), report.get("broker"), report.get("title"),
                report.get("target_price"), report.get("previous_target_price"),
                report.get("opinion"), report.get("price_at_write_date"),
                report.get("report_id"), report.get("detail_status"),
                report.get("pdf_status"), report.get("pdf_hash"), report.get("pdf_bytes"),
                report.get("content_hash"), summary,
            ]
            lines.append("| " + " | ".join(_cell(value) for value in row) + " |")

    extension = "\n".join(lines) + "\n"
    return markdown.replace(marker, extension + marker, 1)
