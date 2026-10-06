from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_v8.quant_briefing import validate_numeric_strategy
from daily_v8.strategy_engine import DataStaleError
from daily_v8.v8_runner import run_v8_headless
from mobile_v8.detail_markdown import render_chatgpt_markdown
from mobile_v8.evidence_collector import ParityCollectionError, collect_offline_parity
from mobile_v8.financial_dependency import enrich_financial_dependency
from mobile_v8.portfolio_special_bollinger import (
    build_bollinger_atr_overlay,
    install_v8_bollinger_20_patch,
    render_bollinger_overlay_markdown,
)
from mobile_v8.portfolio_special_cloud import (
    load_holdings_from_stockbot_bundle,
    load_portfolio_config_values,
    read_gcs_json,
    upload_markdown_to_drive,
    write_gcs_json,
)
from mobile_v8.portfolio_special_selector import (
    choose_holding,
    filter_eligible_holdings,
    mark_analysis_success,
    parse_portfolio_config,
    sync_state,
)
from mobile_v8.research_collector import collect_naver_research
from mobile_v8.research_markdown import inject_research_extension
from mobile_v8.source_parity_enrichment import enrich_source_parity
from mobile_v8.source_parity_markdown import inject_source_parity_extensions
from mobile_v8.strategy_adapter import compute_mobile_strategy

KST = ZoneInfo("Asia/Seoul")

DEFAULT_BUCKET = "stock-monitoring-prod-stockbot-state"
DEFAULT_CURRENT_OBJECT = "stockbot-state/current.json"
DEFAULT_STATE_OBJECT = "v8-portfolio-special/state.json"
DEFAULT_PORTFOLIO_SHEET_ID = "1VZR4-khOQjcWvLvZWoHwjXeDFuTamuaXp7wk7JNbxbM"
DEFAULT_RAW_FOLDER_ID = "15WiLFWoSNubr3nMLMnmBvIKNx1oARsV3"


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", str(value or "").strip())
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
    return _clean_corp_name((evidence.get("company") or {}).get("corp_name")) or current or code


def _apply_market_type_from_evidence(bundle: object, evidence: dict) -> str:
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
        raise RuntimeError(f"STRATEGY_MARKET_MISMATCH:{resolved_market}:{symbol or 'MISSING'}")


def _selection_reason_text(reason: dict) -> str:
    if reason.get("manual_priority"):
        return "수동 우선순위"
    if reason.get("new_holding"):
        return "신규 보유종목 우선"
    if reason.get("never_analyzed"):
        return "아직 특집분석하지 않은 보유종목 중 투자금액 우선"
    days = reason.get("days_since_last_analysis")
    if days is not None:
        return f"특집분석 후 경과기간 우선 ({days:.1f}일)"
    return "보유종목 순환"


def _holder_context_markdown(holding: dict, bundle: object, reason: dict, now: datetime) -> str:
    avg = float(holding.get("avg_buy_price") or 0.0)
    qty = int(holding.get("quantity") or 0)
    invested = float(holding.get("invested_cost") or avg * qty)
    current = float(getattr(bundle, "current_price", 0.0) or 0.0)
    pnl_pct = ((current / avg) - 1.0) * 100.0 if avg > 0 and current > 0 else None
    break_even_rise = ((avg / current) - 1.0) * 100.0 if avg > 0 and current > 0 else None
    pnl_amount = (current - avg) * qty if avg > 0 and current > 0 else None

    def won(value: float | None) -> str:
        return "MISSING" if value is None else f"{value:,.0f}원"

    def pct(value: float | None) -> str:
        return "MISSING" if value is None else f"{value:+.2f}%"

    return "\n".join(
        [
            "# V8 보유종목 특집 — 보유자 분석 컨텍스트",
            "",
            f"- 생성시각: {now.isoformat()}",
            f"- 선정사유: {_selection_reason_text(reason)}",
            f"- 종목: {holding.get('stock_name')} ({holding.get('stock_code')})",
            f"- 보유수량: {qty:,}주",
            f"- 현재 평균매입단가: {won(avg)}",
            f"- 총 매입원가: {won(invested)}",
            f"- V8 현재가: {won(current if current > 0 else None)}",
            f"- 평단 대비 평가손익률: {pct(pnl_pct)}",
            f"- 평단 대비 평가손익액: {won(pnl_amount)}",
            f"- 현재가에서 본전까지 필요한 상승률: {pct(break_even_rise)}",
            f"- PORTFOLIO_CONFIG 매매모드: {holding.get('portfolio_mode') or 'UNKNOWN'}",
            "",
            "## Spark 보유전략 분석 지시",
            "",
            "이 종목은 신규 추천 후보가 아니라 이미 보유 중인 종목이다. 신규매수 관점보다 현재 평단과 보유수량을 기준으로 향후 대응을 우선 판단한다.",
            "",
            "1. 평단 대비 현재 위치와 본전 회복 난이도를 평가한다.",
            "2. 최근 8Q 실적·OCF·부채·DART 위험과 증권사 전망을 교차검증한다.",
            "3. Naver 250D, ATR, Bollinger(26,2.0), 추세, Strategy 가격대를 함께 이용해 보유 지속/축소/추가매수 대기를 판단한다.",
            "4. 추가매수는 단순히 손실률이 크다는 이유로 허용하지 말고 실적·추세·가격의 세 조건이 함께 유리할 때만 검토한다.",
            "5. 평단 회복만을 목표로 무리한 물타기를 권하지 않는다.",
            "6. V8 계산값과 원자료가 충돌하면 원자료를 우선하고 충돌 사실을 명시한다.",
            "7. MISSING/PARTIAL 값은 추정으로 채우지 않는다.",
            "8. 결론은 HOLD / ADD_WAIT / ADD_ALLOWED / REDUCE / EXIT_REVIEW 중 하나로 명확히 제시한다.",
            "9. Bollinger-ATR 오버레이의 상·하단 3호가 돌파, ATR 목표/손절과의 상대위치, 변동성 확대/축소를 최종 전략에 반드시 반영한다.",
            "10. 마지막에 '다음 거래일에 할 일'을 1~3개 행동으로 짧게 제시한다.",
            "",
            "---",
            "",
        ]
    )


def _build_v8_markdown(holding: dict, reason: dict, now: datetime) -> tuple[str, str, str]:
    stock_code = str(holding["stock_code"])

    # Portfolio-special only: restore canonical Bollinger 26/2.0 before the
    # private V8 engine computes its technical score. The pinned engine files
    # remain immutable and the ordinary mobile V8 path is unaffected.
    install_v8_bollinger_20_patch()

    bundle = run_v8_headless(stock_code)
    if not bundle or isinstance(bundle, str):
        raise RuntimeError(f"V8_HEADLESS_{bundle or 'FAILED'}")
    if not getattr(bundle, "quarterly_8q_table", ""):
        raise RuntimeError("V8_8Q_MISSING")

    try:
        evidence = collect_offline_parity(
            stock_code=str(bundle.stock_code),
            stock_name=str(bundle.stock_name),
            generated_at=now,
        )
        evidence = enrich_financial_dependency(evidence)
        evidence = enrich_source_parity(evidence, stock_code=str(bundle.stock_code), fetched_at=now)
        evidence["research"] = collect_naver_research(str(bundle.stock_code))
    except ParityCollectionError as exc:
        raise RuntimeError(f"PARITY_COLLECTION_FAILED:{exc}") from exc

    if evidence.get("status") != "PARITY_READY":
        issues = ",".join(evidence.get("issues") or ["UNKNOWN"])
        raise RuntimeError(f"PARITY_INCOMPLETE:{issues}")

    resolved_market = _apply_market_type_from_evidence(bundle, evidence)
    try:
        strategy = compute_mobile_strategy(
            str(bundle.stock_code), bundle=bundle, evidence=evidence, now=now
        )
    except DataStaleError as exc:
        raise RuntimeError(str(exc)) from exc
    if strategy is None:
        raise RuntimeError("STRATEGY_FAILED")
    _validate_strategy_market(strategy, resolved_market)
    problems = validate_numeric_strategy(strategy)
    if problems:
        raise RuntimeError("STRATEGY_INVALID:" + ",".join(problems))

    overlay = build_bollinger_atr_overlay(
        stock_code=stock_code,
        holding=holding,
        evidence=evidence,
        strategy=strategy,
    )

    display_name = _resolve_display_name(bundle, evidence)
    evidence["stock_name"] = display_name
    detail = render_chatgpt_markdown(
        evidence=evidence,
        bundle=bundle,
        strategy=strategy,
        generated_at=now,
    )
    detail = inject_source_parity_extensions(detail, evidence)
    detail = inject_research_extension(detail, evidence["research"])
    detail = detail.replace(
        "- 최종 결론은 매수/대기/제외 중 하나로 명확히 제시하십시오.",
        "- 본 파일은 보유종목 특집용입니다. 최종 결론은 상단 보유자 분석 지시의 HOLD / ADD_WAIT / ADD_ALLOWED / REDUCE / EXIT_REVIEW 체계를 우선하십시오.",
    )
    markdown = (
        _holder_context_markdown(holding, bundle, reason, now)
        + render_bollinger_overlay_markdown(overlay)
        + detail
    )
    return markdown, display_name, resolved_market


def run_once() -> dict:
    now = datetime.now(KST)
    bucket = os.environ.get("V8_SPECIAL_STATE_BUCKET", DEFAULT_BUCKET).strip()
    current_object = os.environ.get("V8_SPECIAL_CURRENT_OBJECT", DEFAULT_CURRENT_OBJECT).strip()
    state_object = os.environ.get("V8_SPECIAL_STATE_OBJECT", DEFAULT_STATE_OBJECT).strip()
    sheet_id = os.environ.get("V8_SPECIAL_PORTFOLIO_SHEET_ID", DEFAULT_PORTFOLIO_SHEET_ID).strip()
    raw_folder_id = os.environ.get("V8_SPECIAL_RAW_FOLDER_ID", DEFAULT_RAW_FOLDER_ID).strip()
    priority_codes = [x.strip() for x in os.environ.get("V8_SPECIAL_PRIORITY_CODES", "").split(",") if x.strip()]

    holdings = load_holdings_from_stockbot_bundle(bucket, current_object, now)
    config = parse_portfolio_config(load_portfolio_config_values(sheet_id))
    eligible, excluded = filter_eligible_holdings(holdings, config)

    state = read_gcs_json(bucket, state_object, default={})
    state, initial, new_codes = sync_state(state, holdings, now)
    state["last_selection_snapshot"] = {
        "at": now.isoformat(),
        "eligible_codes": [item["stock_code"] for item in eligible],
        "excluded": excluded,
        "initial_state": initial,
    }
    # Persist observation before analysis so newly added holdings remain identifiable
    # even when a later V8/Drive step fails.
    write_gcs_json(bucket, state_object, state)

    remaining = list(eligible)
    analysis_failures: list[dict] = []
    selected = None
    reason = None
    built = None

    while remaining:
        candidate, candidate_reason = choose_holding(
            remaining, state, new_codes, now, priority_codes
        )
        try:
            built = _build_v8_markdown(candidate, candidate_reason, now)
            selected = candidate
            reason = candidate_reason
            break
        except Exception as exc:
            analysis_failures.append(
                {
                    "stock_code": candidate["stock_code"],
                    "stock_name": candidate["stock_name"],
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                    "at": now.isoformat(),
                }
            )
            remaining = [
                item for item in remaining
                if item["stock_code"] != candidate["stock_code"]
            ]

    state["last_analysis_failures"] = analysis_failures
    write_gcs_json(bucket, state_object, state)

    if selected is None or reason is None or built is None:
        summary = "; ".join(
            f"{item['stock_code']}:{item['error']}" for item in analysis_failures
        )
        raise RuntimeError(f"ALL_ELIGIBLE_ANALYSIS_FAILED:{summary or 'UNKNOWN'}")

    markdown, display_name, market_type = built
    filename = (
        f"{now:%Y%m%d}_{selected['stock_code']}_{_safe_filename(display_name)}"
        "_V8보유종목특집_통합원자료.md"
    )
    drive_file = upload_markdown_to_drive(raw_folder_id, filename, markdown)

    state = mark_analysis_success(
        state,
        selected["stock_code"],
        now,
        drive_file_id=drive_file["id"],
        drive_filename=drive_file["name"],
    )
    write_gcs_json(bucket, state_object, state)

    return {
        "stock_code": selected["stock_code"],
        "stock_name": display_name,
        "avg_buy_price": selected["avg_buy_price"],
        "quantity": selected["quantity"],
        "selection_reason": _selection_reason_text(reason),
        "market_type": market_type,
        "drive_file_id": drive_file["id"],
        "drive_filename": drive_file["name"],
        "drive_web_link": drive_file["webViewLink"],
        "eligible_count": len(eligible),
        "excluded": excluded,
        "generated_at": now.isoformat(),
    }


def main() -> int:
    try:
        result = run_once()
        print("PORTFOLIO_V8_SPECIAL=SUCCESS")
        print(f"STOCK={result['stock_name']}({result['stock_code']})")
        print(f"SELECTION_REASON={result['selection_reason']}")
        print(f"AVG_BUY_PRICE={result['avg_buy_price']}")
        print(f"QUANTITY={result['quantity']}")
        print(f"MARKET_TYPE={result['market_type']}")
        print(f"DRIVE_FILE={result['drive_filename']}")
        print(f"DRIVE_FILE_ID={result['drive_file_id']}")
        print(f"ELIGIBLE_COUNT={result['eligible_count']}")
        return 0
    except Exception as exc:
        print("PORTFOLIO_V8_SPECIAL=FAIL")
        print(f"ERROR_CLASS={type(exc).__name__}")
        print(f"ERROR={exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
