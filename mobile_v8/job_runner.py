from __future__ import annotations

import hashlib
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_v8.quant_briefing import validate_numeric_strategy
from daily_v8.strategy_engine import DataStaleError
from daily_v8.v8_runner import run_v8_headless
from mobile_v8.consistency_guard import (
    append_consistency_appendix,
    apply_mobile_consistency_guard,
)
from mobile_v8.detail_markdown import render_chatgpt_markdown
from mobile_v8.evidence_collector import ParityCollectionError, collect_offline_parity
from mobile_v8.financial_dependency import enrich_financial_dependency
from mobile_v8.mailer import send_mobile_v8_email
from mobile_v8.report_renderer import render_mobile_v8_html
from mobile_v8.research_collector import collect_naver_research
from mobile_v8.research_markdown import inject_research_extension
from mobile_v8.source_parity_enrichment import enrich_source_parity
from mobile_v8.source_parity_markdown import inject_source_parity_extensions
from mobile_v8.stock_resolver import resolve_stock_input
from mobile_v8.strategy_adapter import compute_mobile_strategy

KST = ZoneInfo("Asia/Seoul")


def _require_stock_input() -> str:
    value = (os.environ.get("STOCK_INPUT") or "").strip()
    if not value:
        raise RuntimeError("STOCK_INPUT is required")
    if len(value) > 80:
        raise RuntimeError("STOCK_INPUT is too long")
    return value


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", value.strip())
    return cleaned.strip("._") or "stock"


def _clean_corp_name(value: object) -> str:
    text = str(value or "").strip()
    if not text or text == "MISSING":
        return ""
    text = re.sub(r"(?:\(주\)|㈜)", "", text).strip()
    text = re.sub(r"^주식회사\s*", "", text).strip()
    text = re.sub(r"\s*주식회사$", "", text).strip()
    return text


def _resolve_display_name(bundle: object, evidence: dict) -> str:
    code = str(getattr(bundle, "stock_code", "") or evidence.get("stock_code") or "").strip()
    current = str(getattr(bundle, "stock_name", "") or "").strip()
    if current and current != code and not re.fullmatch(r"\d{6}", current):
        return current
    company = evidence.get("company") or {}
    corp_name = _clean_corp_name(company.get("corp_name"))
    return corp_name or current or code


def _apply_market_type_from_evidence(bundle: object, evidence: dict) -> str:
    """Set market_type only for the mobile execution path.

    DART corp_cls is authoritative enough to distinguish KOSPI(Y) and KOSDAQ(K).
    This prevents the shared Strategy Engine's unknown-market fallback from accepting
    the wrong Yahoo suffix merely because that symbol happens to return data.
    """
    current = str(getattr(bundle, "market_type", "") or "").upper().strip()
    if current in {"KOSPI", "KOSDAQ"}:
        return current

    corp_cls = str((evidence.get("company") or {}).get("corp_cls") or "").upper().strip()
    resolved = {"Y": "KOSPI", "K": "KOSDAQ"}.get(corp_cls, "")
    if resolved:
        setattr(bundle, "market_type", resolved)
    return resolved


def _validate_strategy_market(strategy: object, resolved_market: str) -> None:
    if not resolved_market:
        return
    symbol = str(getattr(strategy, "market_symbol", "") or "")
    expected_suffix = ".KQ" if resolved_market == "KOSDAQ" else ".KS"
    if not symbol.endswith(expected_suffix):
        raise RuntimeError(
            f"STRATEGY_MARKET_MISMATCH:{resolved_market}:{symbol or 'MISSING'}"
        )


def run_once(stock_input: str) -> dict:
    now = datetime.now(KST)

    resolved_code, resolved_name = resolve_stock_input(stock_input)

    bundle = run_v8_headless(resolved_code)
    if not bundle or isinstance(bundle, str):
        raise RuntimeError(f"V8_HEADLESS_{bundle or 'FAILED'}")

    if not getattr(bundle, "quarterly_8q_table", ""):
        raise RuntimeError("V8_8Q_MISSING")

    if resolved_name and (not getattr(bundle, "stock_name", None) or bundle.stock_name == bundle.stock_code):
        bundle.stock_name = resolved_name

    # Collect evidence first so the mobile path can resolve the correct exchange
    # and use the same Naver 250D layer for Strategy Engine input. The shared Daily
    # V8 path and the shared Strategy Engine implementation remain untouched.
    try:
        evidence = collect_offline_parity(
            stock_code=str(bundle.stock_code),
            stock_name=str(bundle.stock_name),
            generated_at=now,
        )
        evidence = enrich_financial_dependency(evidence)
        evidence = enrich_source_parity(
            evidence,
            stock_code=str(bundle.stock_code),
            fetched_at=now,
        )
        evidence["research"] = collect_naver_research(str(bundle.stock_code))
    except ParityCollectionError as exc:
        raise RuntimeError(f"PARITY_COLLECTION_FAILED:{exc}") from exc

    if evidence.get("status") != "PARITY_READY":
        issues = ",".join(evidence.get("issues") or ["UNKNOWN"])
        raise RuntimeError(f"PARITY_INCOMPLETE:{issues}")

    resolved_market = _apply_market_type_from_evidence(bundle, evidence)

    # Mobile-only analysis consistency layer.  It corrects known semantic conflicts
    # without changing the pinned V8 engine or the shared Daily V8 path.
    apply_mobile_consistency_guard(
        bundle=bundle,
        evidence=evidence,
        generated_at=now,
    )

    try:
        strategy = compute_mobile_strategy(
            str(bundle.stock_code),
            bundle=bundle,
            evidence=evidence,
            now=now,
        )
    except DataStaleError as exc:
        raise RuntimeError(str(exc)) from exc

    if strategy is None:
        raise RuntimeError("STRATEGY_FAILED")

    _validate_strategy_market(strategy, resolved_market)

    strategy_problems = validate_numeric_strategy(strategy)
    if strategy_problems:
        raise RuntimeError("STRATEGY_INVALID:" + ",".join(strategy_problems))

    display_name = _resolve_display_name(bundle, evidence)
    evidence["stock_name"] = display_name

    body_html = render_mobile_v8_html(
        bundle=bundle,
        strategy=strategy,
        generated_at=now,
        parity=evidence,
    )
    detail_markdown = render_chatgpt_markdown(
        evidence=evidence,
        bundle=bundle,
        strategy=strategy,
        generated_at=now,
    )
    detail_markdown = inject_source_parity_extensions(detail_markdown, evidence)
    detail_markdown = inject_research_extension(detail_markdown, evidence["research"])
    detail_markdown = append_consistency_appendix(detail_markdown, evidence)

    subject = f"[V8 분석] {display_name}({bundle.stock_code}) — {now:%Y-%m-%d}"
    attachment_name = (
        f"{bundle.stock_code}_{_safe_filename(display_name)}"
        f"_V8_ChatGPT통합원자료_{now:%Y%m%d}.md"
    )
    report_hash = hashlib.sha256(
        (subject + "\n" + body_html + "\n" + detail_markdown).encode("utf-8")
    ).hexdigest()
    message_id = send_mobile_v8_email(
        subject=subject,
        body_html=body_html,
        report_hash=report_hash,
        attachment_name=attachment_name,
        attachment_text=detail_markdown,
    )

    return {
        "stock_input": stock_input,
        "stock_name": display_name,
        "stock_code": bundle.stock_code,
        "market_type": resolved_market or str(getattr(bundle, "market_type", "") or ""),
        "market_symbol": str(getattr(strategy, "market_symbol", "") or ""),
        "subject": subject,
        "message_id": message_id,
        "attachment_name": attachment_name,
        "parity_status": evidence["status"],
        "consistency_status": (evidence.get("analysis_consistency") or {}).get("status", "MISSING"),
        "generated_at": now.isoformat(),
    }


def main() -> int:
    try:
        stock_input = _require_stock_input()
        result = run_once(stock_input)
        print("MOBILE_V8_RESULT=SUCCESS")
        print(f"STOCK_NAME={result['stock_name']}")
        print(f"STOCK_CODE={result['stock_code']}")
        print(f"MARKET_TYPE={result['market_type']}")
        print(f"MARKET_SYMBOL={result['market_symbol']}")
        print(f"PARITY_STATUS={result['parity_status']}")
        print(f"CONSISTENCY_STATUS={result['consistency_status']}")
        print(f"ATTACHMENT={result['attachment_name']}")
        print(f"MESSAGE_ID={result['message_id']}")
        return 0
    except Exception as exc:
        print("MOBILE_V8_RESULT=FAIL")
        print(f"ERROR_CLASS={type(exc).__name__}")
        print(f"ERROR={exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
