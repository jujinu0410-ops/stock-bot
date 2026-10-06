from __future__ import annotations

import html
from datetime import datetime
from typing import Any


def _esc(value: Any) -> str:
    if value in (None, "", [], {}):
        return "데이터 없음"
    return html.escape(str(value))


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.0f}원"
    except (TypeError, ValueError):
        return _esc(value)


def _eok(value: Any) -> str:
    try:
        return f"{float(value) / 1e8:,.1f}억"
    except (TypeError, ValueError):
        return _esc(value)


def _number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return _esc(value)


def _card(title: str, body: str) -> str:
    return (
        "<section style='background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;"
        "padding:16px;margin:0 0 14px 0'>"
        f"<h2 style='font-size:17px;margin:0 0 10px 0;color:#111827'>{html.escape(title)}</h2>"
        f"{body}</section>"
    )


def _row(label: str, value: Any) -> str:
    return (
        "<tr>"
        f"<td style='padding:7px 8px;color:#6b7280;vertical-align:top;width:42%'>{html.escape(label)}</td>"
        f"<td style='padding:7px 8px;color:#111827;font-weight:600;vertical-align:top'>{_esc(value)}</td>"
        "</tr>"
    )


def _table(rows: list[tuple[str, Any]]) -> str:
    return "<table style='width:100%;border-collapse:collapse'>" + "".join(_row(k, v) for k, v in rows) + "</table>"


def render_mobile_v8_html(
    *,
    bundle: Any,
    strategy: Any,
    generated_at: datetime,
    parity: dict[str, Any] | None = None,
) -> str:
    warnings = list(getattr(bundle, "quality_warnings", []) or [])
    consistency = (parity or {}).get("analysis_consistency") or {}
    financial_sync = consistency.get("financial_sync") or {}
    for item in consistency.get("warnings") or []:
        warnings.append(f"CONSISTENCY: {item}")
    if financial_sync.get("status") == "OVERRIDDEN":
        warnings = [item for item in warnings if not str(item).startswith("FUNDAMENTAL:")]
        warnings.insert(0, "FUNDAMENTAL: OVERRIDDEN_BY_DART_PARITY")
    warning_html = "<br>".join(f"• {_esc(item)}" for item in warnings) if warnings else "특이 경고 없음"

    financial = getattr(bundle, "financial_summary", {}) or {}
    q8 = _esc(getattr(bundle, "quarterly_8q_table", ""))
    q8 = q8.replace("\n", "<br>")
    display_name = (
        str((parity or {}).get("stock_name") or "").strip()
        or str(getattr(bundle, "stock_name", "") or "").strip()
    )
    stock_code = str(getattr(bundle, "stock_code", "") or (parity or {}).get("stock_code") or "").strip()
    generated_at_kst = generated_at.strftime("%Y-%m-%d %H:%M:%S KST")

    sections = [
        _card("기본", _table([
            ("종목", f"{display_name} ({stock_code})"),
            ("현재가", _money(getattr(strategy, "current_price", None))),
            ("등락률", f"{_number(getattr(bundle, 'daily_change_pct', None))}%"),
            ("데이터 생성시각", generated_at_kst),
        ])),
    ]

    if parity:
        p_fin = parity.get("financials") or {}
        p_market = parity.get("market") or {}
        p_docs = parity.get("periodic_documents") or {}
        p_research = parity.get("research") or {}
        mismatch_fields = ", ".join(financial_sync.get("mismatch_fields") or []) or "NONE"
        financial_consistency = (
            f"{financial_sync.get('status', 'MISSING')} / {mismatch_fields}"
            if financial_sync
            else "MISSING"
        )
        sections.append(_card("오프라인 원자료 동등성", _table([
            ("상태", parity.get("status")),
            ("분석 일관성", consistency.get("status", "MISSING")),
            ("재무 일관성", financial_consistency),
            ("DART 공시", f"{(parity.get('disclosures') or {}).get('count', 0)}건"),
            ("재무", f"{p_fin.get('quarter_count', 0)}Q / {p_fin.get('canonical_scope', 'MISSING')}"),
            ("시장원자료", f"{p_market.get('count', 0)}D / {p_market.get('quality', 'MISSING')}"),
            ("DART 원문", f"{p_docs.get('downloaded_count', 0)}/{p_docs.get('document_count', 0)}"),
            ("증권사 리서치", f"{p_research.get('quality_status', 'NOT_AVAILABLE')} / {p_research.get('report_count', 0)}건"),
            ("통합첨부", "00~07 자료층 + V8/Strategy 부록 1개 MD"),
        ])))

    financial_profile = str(financial.get("financial_profile") or "GENERAL")
    if financial_profile == "BANK_HOLDING":
        financial_rows = [
            ("재무 원천", financial.get("financial_source", "DART_PARITY_BANK_HOLDING_8Q")),
            ("재무 프로파일", "BANK_HOLDING"),
            ("최근 영업이익", _eok(financial.get("operating_profit"))),
            ("최근 순이익", _eok(financial.get("net_income"))),
            ("총자산", f"{_number((financial.get('total_assets') or 0) / 1e12, 1)}조"),
            ("자기자본", f"{_number((financial.get('total_equity') or 0) / 1e12, 1)}조"),
            ("자본/자산", f"{_number(financial.get('equity_assets_ratio'), 2)}%"),
            ("재무 흐름", getattr(bundle, "prev_vs_current_summary", "")),
        ]
    else:
        financial_rows = [
            ("재무 원천", financial.get("financial_source", "V8_LEGACY")),
            ("최근 매출", _eok(financial.get("revenue"))),
            ("최근 영업이익", _eok(financial.get("operating_profit"))),
            ("OCF", _eok(getattr(bundle, "ocf_annual", None))),
            ("부채비율", f"{_number(getattr(bundle, 'debt_ratio', None))}%"),
            ("이전 부채비율", f"{_number(getattr(bundle, 'prev_debt_ratio', None))}%"),
            ("재무 흐름", getattr(bundle, "prev_vs_current_summary", "")),
        ]

    sections += [
        _card("재무", _table(financial_rows)),
        _card("최근 8분기", f"<div style='font-size:13px;line-height:1.65;overflow-x:auto'>{q8}</div>"),
        _card("산업 / DART", _table([
            ("산업 점수", getattr(bundle, "industry_score", None)),
            ("산업 Gate", getattr(bundle, "industry_gate", "")),
            ("산업 프로필", getattr(bundle, "industry_profile", "")),
            ("수주", getattr(bundle, "order_backlog_summary", "")),
            ("신규 수주", getattr(bundle, "new_orders_summary", "")),
            ("CAPA", getattr(bundle, "capa_summary", "")),
            ("DART 위험", getattr(bundle, "dart_risks", "")),
        ])),
        _card("기술 상태", _table([
            ("기술 점수", getattr(bundle, "t_score", None)),
            ("일봉 상태", getattr(bundle, "technical_action", "") or getattr(bundle, "technical_summary", "")),
            ("일봉 ADX/DI", getattr(bundle, "daily_adx_di", "")),
            ("45분봉 상태", getattr(bundle, "intraday_status", "")),
            ("45분봉 ADX/DI", getattr(bundle, "intraday_adx_di", "")),
            ("45분봉 OBV", getattr(bundle, "obv_45m_trend", "")),
            ("45분봉 기준시각", getattr(bundle, "intraday_last_timestamp", "")),
        ])),
        _card("가격 전략", _table([
            ("ATR14", _money(getattr(strategy, "atr14", None))),
            ("ATR 방식", (getattr(strategy, "calc_notes", {}) or {}).get("atr14_method", "MISSING")),
            ("ATR 비율", f"{_number(getattr(strategy, 'atr_pct', None))}%"),
            ("진입구간", f"{_money(getattr(strategy, 'entry_zone_low', None))} ~ {_money(getattr(strategy, 'entry_zone_high', None))}"),
            ("눌림목", _money(getattr(strategy, "pullback_zone", None))),
            ("돌파조건", _money(getattr(strategy, "breakout_trigger", None))),
            ("훼손가격", _money(getattr(strategy, "stop_price", None))),
            ("목표1", _money(getattr(strategy, "target_1", None))),
            ("목표2", _money(getattr(strategy, "target_2", None))),
            ("RR1", getattr(strategy, "rr_target1", None)),
            ("RR2", getattr(strategy, "rr_target2", None)),
            ("추격위험", getattr(strategy, "chase_risk", "")),
        ])),
        _card("Quality Warning", f"<div style='font-size:14px;line-height:1.65'>{warning_html}</div>"),
        _card("데이터 기준시각", _table([
            ("현재가", getattr(strategy, "current_price_asof", "")),
            ("일봉", getattr(strategy, "daily_ohlcv_asof", "")),
            ("45분봉", getattr(bundle, "intraday_last_timestamp", "")),
            ("메일 생성", generated_at.isoformat()),
        ])),
    ]

    title = f"V8 분석 — {html.escape(display_name)} ({html.escape(stock_code)})"
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title></head>"
        "<body style='margin:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif'>"
        "<div style='max-width:680px;margin:0 auto;padding:18px 12px 30px'>"
        f"<h1 style='font-size:22px;margin:4px 0 16px;color:#111827'>{title}</h1>"
        "<div style='font-size:13px;color:#6b7280;margin-bottom:16px'>V8 정량 결과입니다. AI 해석은 포함하지 않았습니다. 통합 첨부는 오프라인 원자료 동등성 검사를 통과한 경우에만 발송됩니다.</div>"
        + "".join(sections) +
        "</div></body></html>"
    )
