from __future__ import annotations

from typing import Any

from mobile_v8.consistency_guard import append_consistency_appendix


def _cell(value: Any) -> str:
    if value in (None, ""):
        return "MISSING"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "NONE"
    return str(value).replace("\n", " ").replace("|", r"\|")


def _csv(value: Any) -> str:
    if value in (None, ""):
        return "MISSING"
    return str(value).replace("\r", " ").replace("\n", " ").replace(",", " ")


def _inject_disclosure_audit(markdown: str, evidence: dict[str, Any]) -> str:
    marker = "\n# 02. 최근 8분기 재무 상세"
    if marker not in markdown:
        return markdown
    disclosures = evidence.get("disclosures") or {}
    chains = disclosures.get("chains") or []
    effective = disclosures.get("latest_effective_filings") or []

    lines = [
        "",
        "## Disclosure chains",
        "",
        f"- chain method: {_cell(disclosures.get('chain_method'))}",
        f"- chain note: {_cell(disclosures.get('chain_note'))}",
        "",
        "| chain | event type | status | confidence | original receipt | latest effective | versions | source manifests |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in chains:
        lines.append("| " + " | ".join(_cell(row.get(key)) for key in (
            "chain", "event_type", "status", "confidence", "original_receipt",
            "latest_effective", "versions", "source_manifests",
        )) + " |")

    lines += [
        "",
        "## Latest effective filings",
        "",
        "| chain | effective receipt | state | source manifest |",
        "|---|---|---|---|",
    ]
    for row in effective:
        lines.append("| " + " | ".join(_cell(row.get(key)) for key in (
            "chain", "effective_receipt", "state", "source_manifest",
        )) + " |")
    extension = "\n".join(lines) + "\n"
    return markdown.replace(marker, extension + marker, 1)


def _restore_offline_market_shape(markdown: str, evidence: dict[str, Any]) -> str:
    """Keep the legacy 03/04 market-data contract while using current live sources.

    The workstation V8 export carried a trading_value CSV column and explicit
    conflict/freshness metadata even when those values were unavailable.  The
    mobile package must preserve that shape rather than silently dropping fields.
    Missing values remain MISSING; nothing is inferred.
    """
    market = evidence.get("market") or {}

    section3 = "\n# 03. 최근 250거래일 시장데이터"
    section4 = "\n# 04. 시장데이터 설명과 품질"
    start = markdown.find(section3)
    end = markdown.find(section4, start + 1) if start >= 0 else -1
    if start >= 0 and end > start:
        lines = [
            "",
            "# 03. 최근 250거래일 시장데이터",
            "",
            f"- 행 수: {_cell(market.get('count'))}",
            "- 정렬: 오래된 날짜 → 최신 날짜",
            "- 기술지표 계산용 OHLCV 원자료",
            "```csv",
            "date,open,high,low,close,volume,trading_value",
        ]
        for candle in market.get("candles") or []:
            lines.append(",".join(_csv(candle.get(key)) for key in (
                "date", "open", "high", "low", "close", "volume", "trading_value",
            )))
        lines += ["```", ""]
        markdown = markdown[:start] + "\n".join(lines) + markdown[end:]

    section4_end_marker = "\n# 05. 증권사 리서치 상세"
    start = markdown.find(section4)
    end = markdown.find(section4_end_marker, start + 1) if start >= 0 else -1
    if start >= 0 and end > start:
        section = markdown[start:end]
        if "\n- conflict count:" not in section:
            duplicate_line = f"- duplicate count: {_cell(market.get('duplicate_count'))}"
            replacement = (
                duplicate_line
                + f"\n- conflict count: {_cell(market.get('conflict_count'))}"
            )
            section = section.replace(duplicate_line, replacement, 1)
        if "\n- freshness:" not in section:
            quality_line = f"- quality: {_cell(market.get('quality'))}"
            replacement = quality_line + f"\n- freshness: {_cell(market.get('freshness'))}"
            section = section.replace(quality_line, replacement, 1)
        markdown = markdown[:start] + section + markdown[end:]

    return markdown


def _inject_market_quote(markdown: str, evidence: dict[str, Any]) -> str:
    marker = "\n# 05. 증권사 리서치 상세"
    if marker not in markdown:
        return markdown
    market = evidence.get("market") or {}
    quote = market.get("quote") or {}
    lines = [
        "",
        "## Quote",
        "",
    ]
    keys = [
        ("stock_code", "stock_code"),
        ("stock_name", "stock_name"),
        ("source_timestamp", "source_timestamp"),
        ("fetched_at", "fetched_at"),
        ("price", "price"),
        ("previous_close", "previous_close"),
        ("change", "change"),
        ("change_rate", "change_rate"),
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("volume", "volume"),
        ("trading_value", "trading_value"),
        ("quote_status", "quote_status"),
        ("market_status", "market_status"),
        ("parse_status", "parse_status"),
        ("basic_status", "basic_status"),
        ("polling_status", "polling_status"),
        ("parse issues", "parse_issues"),
        ("basic source manifest", "basic_source_manifest"),
        ("polling source manifest", "polling_source_manifest"),
    ]
    for label, key in keys:
        lines.append(f"- {label}: {_cell(quote.get(key))}")
    lines += [
        "",
        "## Lineage",
        "",
        "- source: naver public quote APIs",
        f"- source manifests: {_cell(market.get('quote_source_manifests'))}",
        f"- lineage: {_cell(market.get('lineage'))}",
        "",
    ]
    extension = "\n".join(lines)
    return markdown.replace(marker, extension + marker, 1)


def inject_source_parity_extensions(markdown: str, evidence: dict[str, Any]) -> str:
    markdown = _inject_disclosure_audit(markdown, evidence)
    markdown = _restore_offline_market_shape(markdown, evidence)
    markdown = _inject_market_quote(markdown, evidence)
    if evidence.get("analysis_consistency"):
        markdown = append_consistency_appendix(markdown, evidence)
    return markdown
