from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Any


_SEVERITY_ORDER = {"HIGH": 0, "REVIEW": 1, "MEDIUM": 2, "INFO": 3}
_FINANCIAL_EVIDENCE_KEYWORDS = (
    "매출",
    "영업이익",
    "영업손실",
    "ocf",
    "현금흐름",
    "부채",
    "revenue",
    "operating profit",
    "operating income",
    "cash flow",
    "debt",
)


def _clean_report_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\[[^\]]+\]\s*", "", text)
    return re.sub(r"\s+", " ", text)


def _parse_yyyymmdd(value: Any):
    try:
        return datetime.strptime(str(value or ""), "%Y%m%d").date()
    except ValueError:
        return None


def _extract_cb_series(report_name: str) -> str | None:
    """Return a CB series number only when the filing title states it explicitly."""
    match = re.search(r"제\s*(\d+)\s*회차\s*CB", _clean_report_name(report_name), re.IGNORECASE)
    return match.group(1) if match else None


def _count_note(category: str, events: list[dict[str, Any]]) -> str:
    """Distinguish raw filing count from economically distinct events."""
    filing_count = len(events)
    if category == "CB_FINANCING":
        series = sorted(
            {
                value
                for value in (_extract_cb_series(str(event.get("report_name") or "")) for event in events)
                if value
            },
            key=int,
        )
        if series:
            return f" {len(series)}개 회차 / 관련 공시 {filing_count}건(원공시·정정·발행결과 포함)"
    if filing_count > 1:
        return f" 관련 공시 {filing_count}건(원공시·정정 포함)"
    return ""


def _classify_disclosure(report_name: str) -> tuple[str, str, str] | None:
    """Classify only exact/strong DART title patterns."""
    name = _clean_report_name(report_name)

    if "단일판매" in name and "공급계약" in name and ("해지" in name or "취소" in name or "해제" in name):
        return (
            "CONTRACT_CANCELLATION",
            "HIGH",
            "단일판매·공급계약 해지/취소. 취소금액·매출비중·귀책사유를 원문에서 확인",
        )

    if "최대주주변경을수반하는주식담보제공계약" in name:
        if "해제" in name or "취소" in name:
            return (
                "OWNER_PLEDGE_RELEASE",
                "REVIEW",
                "최대주주 주식담보 계약 해제/취소 관련. 판매·수주계약 해지로 해석 금지",
            )
        if "체결" in name:
            return (
                "OWNER_PLEDGE",
                "HIGH",
                "최대주주 주식담보 제공. 담보권 실행 시 지배구조 변동 가능성 확인",
            )

    if "전환사채권발행결정" in name or ("증권발행결과" in name and "CB" in name.upper()):
        return (
            "CB_FINANCING",
            "HIGH",
            "전환사채 자금조달. 전환가·리픽싱·만기·풋옵션·잠재 희석을 원문에서 확인",
        )

    if "자본으로인정되는채무증권발행결정" in name:
        return (
            "HYBRID_FINANCING",
            "HIGH",
            "자본인정 채무증권 발행. 회계상 자본 여부와 별개로 이자·상환·콜 조건 확인",
        )

    if "유상증자결정" in name or ("증권발행결과" in name and "유상증자" in name):
        return (
            "EQUITY_FINANCING",
            "HIGH",
            "유상증자 관련 공시. 배정방식·발행규모·보호예수·희석률은 원문 확인 전 추정 금지",
        )

    if "전환청구권행사" in name or "신주인수권행사" in name:
        return (
            "DILUTION_EXERCISE",
            "MEDIUM",
            "전환/신주인수권 행사. 실제 신주 증가와 잔여 잠재주식 물량 확인",
        )

    if "타인에대한채무보증결정" in name:
        return (
            "DEBT_GUARANTEE",
            "HIGH",
            "타인 채무보증. 보증금액·자기자본 대비 비율·피보증회사 재무상태 확인",
        )

    return None


def _summarize_recent_disclosure_risks(
    evidence: dict[str, Any],
    *,
    asof: datetime,
    lookback_days: int = 120,
) -> tuple[str, list[dict[str, Any]]]:
    rows = ((evidence.get("disclosures") or {}).get("rows") or [])
    cutoff = asof.date() - timedelta(days=lookback_days)
    classified: list[dict[str, Any]] = []

    for row in rows:
        filing_date = _parse_yyyymmdd(row.get("rcept_dt"))
        if filing_date is None or filing_date < cutoff:
            continue
        classified_event = _classify_disclosure(str(row.get("report_nm") or ""))
        if classified_event is None:
            continue
        category, severity, note = classified_event
        classified.append(
            {
                "date": filing_date.isoformat(),
                "date_compact": filing_date.strftime("%Y%m%d"),
                "category": category,
                "severity": severity,
                "report_name": _clean_report_name(row.get("report_nm")),
                "receipt": row.get("rcept_no") or "MISSING",
                "amendment_state": row.get("amendment_state") or "MISSING",
                "note": note,
            }
        )

    if not classified:
        return "최근 120일 규칙기반 중요 DART 위험 공시 없음", []

    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in classified:
        grouped.setdefault(event["category"], []).append(event)

    summaries: list[tuple[int, str, str]] = []
    for category, events in grouped.items():
        events.sort(key=lambda x: x["date_compact"], reverse=True)
        latest = events[0]
        latest_names = []
        for event in events:
            name = event["report_name"]
            if name not in latest_names:
                latest_names.append(name)
            if len(latest_names) >= 2:
                break
        title = " / ".join(latest_names)
        count_note = _count_note(category, events)
        text = (
            f"[{latest['date']}] {category}{count_note} "
            f"(Hazard: {latest['severity']} | {latest['note']} | 최근: {title})"
        )
        summaries.append((_SEVERITY_ORDER.get(latest["severity"], 9), latest["date_compact"], text))

    summaries.sort(key=lambda item: (item[0], -int(item[1])))
    return "; ".join(item[2] for item in summaries[:5]), classified


def _as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _is_materially_different(left: Any, right: Any) -> bool:
    a = _as_float(left)
    b = _as_float(right)
    if a is None or b is None:
        return a != b
    tolerance = max(1.0, abs(b) * 0.005)
    return abs(a - b) > tolerance


def _debt_ratio_from_quarter(quarter: dict[str, Any]) -> float | None:
    liabilities = _as_float(quarter.get("liabilities"))
    equity = _as_float(quarter.get("equity"))
    if liabilities is None or equity is None or equity == 0:
        return None
    return liabilities / equity * 100.0


def _equity_asset_ratio_from_quarter(quarter: dict[str, Any]) -> float | None:
    assets = _as_float(quarter.get("assets"))
    equity = _as_float(quarter.get("equity"))
    if assets is None or equity is None or assets == 0:
        return None
    return equity / assets * 100.0


def _financial_profile(evidence: dict[str, Any]) -> str:
    company = evidence.get("company") or {}
    induty_code = str(company.get("induty_code") or "").strip()
    # 64992 = 금융지주회사. Keep the first rollout intentionally narrow:
    # banks/insurance/securities are not inferred into this profile.
    if induty_code == "64992":
        return "BANK_HOLDING"
    return "GENERAL"


def _eok(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "MISSING"
    return f"{number / 1e8:,.1f}"


def _jo(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "MISSING"
    return f"{number / 1e12:,.1f}"


def _pct_change(current: Any, previous: Any) -> str:
    cur = _as_float(current)
    prev = _as_float(previous)
    if cur is None or prev is None or prev == 0:
        return "-"
    return f"{(cur / prev - 1.0) * 100.0:+.1f}%"


def _quarter_label(raw: Any) -> str:
    return str(raw or "MISSING").replace("-", " ")


def _same_quarter_prior_year(quarters: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    label = str(quarters[index].get("quarter") or "")
    match = re.fullmatch(r"(\d{4})-Q([1-4])", label)
    if not match:
        return None
    wanted = f"{int(match.group(1)) - 1}-Q{match.group(2)}"
    for quarter in quarters:
        if str(quarter.get("quarter") or "") == wanted:
            return quarter
    return None


def _render_parity_8q_table(quarters: list[dict[str, Any]]) -> str:
    lines = [
        "| 분기 | 매출액(억) | YoY(%) | 영업이익(억) | YoY(%) | OPM(%) | OCF(억) | 부채비율(%) |",
        "| :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for idx, quarter in enumerate(quarters):
        prior = _same_quarter_prior_year(quarters, idx)
        revenue = _as_float(quarter.get("revenue"))
        operating = _as_float(quarter.get("operating_income"))
        opm = (operating / revenue * 100.0) if revenue not in (None, 0.0) and operating is not None else None
        debt_ratio = _debt_ratio_from_quarter(quarter)
        lines.append(
            "| "
            + " | ".join(
                [
                    _quarter_label(quarter.get("quarter")),
                    _eok(revenue),
                    _pct_change(revenue, prior.get("revenue") if prior else None),
                    _eok(operating),
                    _pct_change(operating, prior.get("operating_income") if prior else None),
                    f"{opm:.1f}%" if opm is not None else "MISSING",
                    _eok(quarter.get("operating_cash_flow")),
                    f"{debt_ratio:.1f}%" if debt_ratio is not None else "MISSING",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _render_bank_holding_8q_table(quarters: list[dict[str, Any]]) -> str:
    lines = [
        "| 분기 | 영업이익(억) | YoY(%) | 순이익(억) | YoY(%) | 총자산(조) | 자기자본(조) | 자본/자산(%) |",
        "| :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for idx, quarter in enumerate(quarters):
        prior = _same_quarter_prior_year(quarters, idx)
        operating = _as_float(quarter.get("operating_income"))
        net_income = _as_float(quarter.get("profit_loss"))
        equity_assets = _equity_asset_ratio_from_quarter(quarter)
        lines.append(
            "| "
            + " | ".join(
                [
                    _quarter_label(quarter.get("quarter")),
                    _eok(operating),
                    _pct_change(operating, prior.get("operating_income") if prior else None),
                    _eok(net_income),
                    _pct_change(net_income, prior.get("profit_loss") if prior else None),
                    _jo(quarter.get("assets")),
                    _jo(quarter.get("equity")),
                    f"{equity_assets:.2f}%" if equity_assets is not None else "MISSING",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _filter_legacy_financial_evidence(values: Any) -> tuple[list[str], int]:
    if not isinstance(values, list):
        return [], 0
    kept: list[str] = []
    removed = 0
    for value in values:
        text = str(value or "")
        lowered = text.lower()
        if any(keyword in lowered for keyword in _FINANCIAL_EVIDENCE_KEYWORDS):
            removed += 1
            continue
        kept.append(text)
    return kept, removed


def _sync_bank_holding_financials_from_parity(
    bundle: object,
    evidence: dict[str, Any],
    quarters: list[dict[str, Any]],
    *,
    warnings: list[str],
    original: dict[str, Any],
) -> dict[str, Any]:
    latest = quarters[-1]
    latest_core = {
        "operating_profit": _as_float(latest.get("operating_income")),
        "net_income": _as_float(latest.get("profit_loss")),
        "assets": _as_float(latest.get("assets")),
        "equity": _as_float(latest.get("equity")),
        "equity_assets_ratio": _equity_asset_ratio_from_quarter(latest),
    }
    missing = [key for key, value in latest_core.items() if value is None]
    if missing:
        return {
            "status": "SKIPPED",
            "reason": "bank_latest_core_missing:" + ",".join(missing),
            "financial_profile": "BANK_HOLDING",
        }

    previous = quarters[-2] if len(quarters) >= 2 else {}
    previous_equity_assets = _equity_asset_ratio_from_quarter(previous)
    financial_summary = dict(getattr(bundle, "financial_summary", {}) or {})

    original["financial_summary"] = dict(financial_summary)
    original["quarterly_8q_table"] = str(getattr(bundle, "quarterly_8q_table", "") or "")
    original["prev_vs_current_summary"] = str(getattr(bundle, "prev_vs_current_summary", "") or "")
    original["debt_ratio"] = getattr(bundle, "debt_ratio", None)
    original["prev_debt_ratio"] = getattr(bundle, "prev_debt_ratio", None)
    original["ocf_annual"] = getattr(bundle, "ocf_annual", None)

    # General-company ratios are not economically comparable for a financial
    # holding company. Preserve raw evidence, but invalidate general scores.
    for score_field in ("f_score", "cat_pts", "growth_pts", "cf_pts", "debt_pts"):
        if score_field in financial_summary:
            financial_summary[score_field] = None

    financial_summary.update(
        {
            "revenue": None,
            "ocf": None,
            "operating_profit": latest_core["operating_profit"],
            "net_income": latest_core["net_income"],
            "total_assets": latest_core["assets"],
            "total_equity": latest_core["equity"],
            "equity_assets_ratio": latest_core["equity_assets_ratio"],
            "financial_profile": "BANK_HOLDING",
            "financial_source": "DART_PARITY_BANK_HOLDING_8Q",
            "fundamental_state": "BANK_PROFILE_PARITY_ONLY",
            "sanity_flag": "BANK_PROFILE_GENERAL_SCORE_INVALIDATED",
        }
    )

    setattr(bundle, "financial_summary", financial_summary)
    setattr(bundle, "quarterly_8q_table", _render_bank_holding_8q_table(quarters))
    setattr(bundle, "ocf_annual", None)
    setattr(bundle, "debt_ratio", None)
    setattr(bundle, "prev_debt_ratio", None)

    yoy_quarter = _same_quarter_prior_year(quarters, len(quarters) - 1)
    flow_summary = (
        f"금융지주 프로파일 | "
        f"영업이익 {_eok(latest_core['operating_profit'])}억 "
        f"(YoY {_pct_change(latest_core['operating_profit'], yoy_quarter.get('operating_income') if yoy_quarter else None)}) | "
        f"순이익 {_eok(latest_core['net_income'])}억 "
        f"(YoY {_pct_change(latest_core['net_income'], yoy_quarter.get('profit_loss') if yoy_quarter else None)}) | "
        f"총자산 {_jo(latest_core['assets'])}조 | "
        f"자기자본 {_jo(latest_core['equity'])}조 | "
        f"자본/자산 {latest_core['equity_assets_ratio']:.2f}%"
    )
    setattr(bundle, "prev_vs_current_summary", flow_summary)

    canonical_evidence = [
        f"PARITY_BANK_HOLDING: latest={latest.get('quarter')} scope={latest.get('scope')} receipt={latest.get('receipt')}",
        (
            "PARITY_BANK_HOLDING: "
            f"영업이익 {_eok(latest_core['operating_profit'])}억, "
            f"순이익 {_eok(latest_core['net_income'])}억, "
            f"총자산 {_jo(latest_core['assets'])}조, "
            f"자기자본 {_jo(latest_core['equity'])}조, "
            f"자본/자산 {latest_core['equity_assets_ratio']:.2f}%"
        ),
        "PARITY_BANK_HOLDING: manufacturing-style revenue/OCF/debt score is not used for financial holding companies",
    ]
    setattr(bundle, "fundamental_evidence", canonical_evidence)
    for attr in ("bull_evidence", "bear_evidence"):
        kept, removed = _filter_legacy_financial_evidence(getattr(bundle, attr, []))
        setattr(bundle, attr, kept)
        if removed:
            warnings.append(f"LEGACY_{attr.upper()}_FINANCIAL_ITEMS_FILTERED:{removed}")

    warnings.append("FINANCIAL_PROFILE_BANK_HOLDING_APPLIED")
    warnings.append("LEGACY_GENERAL_FUNDAMENTAL_SCORE_INVALIDATED_FOR_BANK_HOLDING")

    return {
        "status": "BANK_SYNCED",
        "source": "DART_PARITY_BANK_HOLDING_8Q",
        "financial_profile": "BANK_HOLDING",
        "latest_quarter": latest.get("quarter"),
        "scope": latest.get("scope"),
        "receipt": latest.get("receipt"),
        "mismatch_fields": ["GENERAL_PROFILE_NOT_APPLICABLE"],
        "latest": latest_core,
        "previous_equity_assets_ratio": previous_equity_assets,
    }


def _sync_financials_from_parity(
    bundle: object,
    evidence: dict[str, Any],
    *,
    warnings: list[str],
    original: dict[str, Any],
) -> dict[str, Any]:
    financials = evidence.get("financials") or {}
    quarters = list(financials.get("quarters") or [])
    if len(quarters) != 8:
        return {"status": "SKIPPED", "reason": f"quarter_count={len(quarters)}"}

    if _financial_profile(evidence) == "BANK_HOLDING":
        return _sync_bank_holding_financials_from_parity(
            bundle,
            evidence,
            quarters,
            warnings=warnings,
            original=original,
        )

    latest = quarters[-1]
    latest_core = {
        "revenue": _as_float(latest.get("revenue")),
        "operating_profit": _as_float(latest.get("operating_income")),
        "ocf": _as_float(latest.get("operating_cash_flow")),
        "debt_ratio": _debt_ratio_from_quarter(latest),
    }
    if latest_core["revenue"] is None or latest_core["operating_profit"] is None or latest_core["debt_ratio"] is None:
        return {"status": "SKIPPED", "reason": "latest_core_missing"}

    previous = quarters[-2] if len(quarters) >= 2 else None
    previous_debt_ratio = _debt_ratio_from_quarter(previous or {})
    financial_summary = dict(getattr(bundle, "financial_summary", {}) or {})
    mismatch_fields: list[str] = []
    checks = {
        "revenue": (financial_summary.get("revenue"), latest_core["revenue"]),
        "operating_profit": (financial_summary.get("operating_profit"), latest_core["operating_profit"]),
        "ocf": (financial_summary.get("ocf"), latest_core["ocf"]),
        "debt_ratio": (getattr(bundle, "debt_ratio", None), latest_core["debt_ratio"]),
    }
    for field, (legacy_value, parity_value) in checks.items():
        if _is_materially_different(legacy_value, parity_value):
            mismatch_fields.append(field)

    original["financial_summary"] = dict(financial_summary)
    original["quarterly_8q_table"] = str(getattr(bundle, "quarterly_8q_table", "") or "")
    original["prev_vs_current_summary"] = str(getattr(bundle, "prev_vs_current_summary", "") or "")
    original["debt_ratio"] = getattr(bundle, "debt_ratio", None)
    original["prev_debt_ratio"] = getattr(bundle, "prev_debt_ratio", None)
    original["ocf_annual"] = getattr(bundle, "ocf_annual", None)

    financial_summary["revenue"] = latest_core["revenue"]
    financial_summary["operating_profit"] = latest_core["operating_profit"]
    financial_summary["ocf"] = latest_core["ocf"]
    financial_summary["financial_source"] = "DART_PARITY_CANONICAL_8Q"

    if mismatch_fields:
        for score_field in ("f_score", "cat_pts", "growth_pts", "cf_pts", "debt_pts"):
            if score_field in financial_summary:
                financial_summary[score_field] = None
        financial_summary["fundamental_state"] = "REVIEW_REQUIRED_PARITY_FINANCIAL_OVERRIDE"
        financial_summary["sanity_flag"] = "PARITY_CANONICAL_OVERRIDE"

    setattr(bundle, "financial_summary", financial_summary)
    setattr(bundle, "quarterly_8q_table", _render_parity_8q_table(quarters))
    setattr(bundle, "ocf_annual", latest_core["ocf"])
    setattr(bundle, "debt_ratio", latest_core["debt_ratio"])
    setattr(bundle, "prev_debt_ratio", previous_debt_ratio)

    yoy_quarter = _same_quarter_prior_year(quarters, len(quarters) - 1)
    flow_summary = (
        f"매출 {_eok(latest_core['revenue'])}억 (YoY {_pct_change(latest_core['revenue'], yoy_quarter.get('revenue') if yoy_quarter else None)}) | "
        f"영업이익 {_eok(latest_core['operating_profit'])}억 (YoY {_pct_change(latest_core['operating_profit'], yoy_quarter.get('operating_income') if yoy_quarter else None)}) | "
        f"OCF {_eok(latest_core['ocf'])}억 | "
        f"부채비율 {latest_core['debt_ratio']:.1f}%"
    )
    setattr(bundle, "prev_vs_current_summary", flow_summary)

    if mismatch_fields:
        canonical_evidence = [
            f"PARITY_CANONICAL: latest={latest.get('quarter')} scope={latest.get('scope')} receipt={latest.get('receipt')}",
            f"PARITY_CANONICAL: 매출 {_eok(latest_core['revenue'])}억, 영업이익 {_eok(latest_core['operating_profit'])}억, OCF {_eok(latest_core['ocf'])}억, 부채비율 {latest_core['debt_ratio']:.1f}%",
            "PARITY_CANONICAL: legacy V8 fundamental score is invalidated because core financial inputs disagreed with DART parity evidence",
        ]
        setattr(bundle, "fundamental_evidence", canonical_evidence)
        for attr in ("bull_evidence", "bear_evidence"):
            kept, removed = _filter_legacy_financial_evidence(getattr(bundle, attr, []))
            setattr(bundle, attr, kept)
            if removed:
                warnings.append(f"LEGACY_{attr.upper()}_FINANCIAL_ITEMS_FILTERED:{removed}")
        warnings.append("FINANCIAL_CORE_SYNCED_WITH_DART_PARITY:" + ",".join(mismatch_fields))
        warnings.append("LEGACY_FUNDAMENTAL_SCORE_INVALIDATED_DUE_TO_FINANCIAL_MISMATCH")
    else:
        warnings.append("FINANCIAL_8Q_RENDERED_FROM_DART_PARITY")

    return {
        "status": "OVERRIDDEN" if mismatch_fields else "SYNCED",
        "source": "DART_PARITY_CANONICAL_8Q",
        "latest_quarter": latest.get("quarter"),
        "scope": latest.get("scope"),
        "receipt": latest.get("receipt"),
        "mismatch_fields": mismatch_fields,
        "latest": latest_core,
        "previous_debt_ratio": previous_debt_ratio,
    }


def apply_mobile_consistency_guard(
    *,
    bundle: object,
    evidence: dict[str, Any],
    generated_at: datetime,
) -> dict[str, Any]:
    """Apply active V8-path corrections without changing shared Daily V8 strategy code."""
    warnings: list[str] = []
    original: dict[str, Any] = {}

    original_dart_risks = str(getattr(bundle, "dart_risks", "") or "")
    corrected_dart_risks, classified_events = _summarize_recent_disclosure_risks(
        evidence,
        asof=generated_at,
    )
    original["dart_risks"] = original_dart_risks
    setattr(bundle, "dart_risks", corrected_dart_risks)

    if "주식담보" in original_dart_risks and "수주계약 해지" in original_dart_risks:
        warnings.append("DART_CLASSIFIER_CORRECTED: shareholder pledge release was mislabeled as sales-contract cancellation")

    financial_sync = _sync_financials_from_parity(
        bundle,
        evidence,
        warnings=warnings,
        original=original,
    )

    buy = getattr(bundle, "candidate_buy_price", None)
    stop = getattr(bundle, "candidate_stop_price", None)
    try:
        buy_f = float(buy) if buy not in (None, "") else None
        stop_f = float(stop) if stop not in (None, "") else None
    except (TypeError, ValueError):
        buy_f, stop_f = None, None
    if buy_f is not None and stop_f is not None and stop_f >= buy_f:
        original["candidate_stop_price"] = stop
        setattr(bundle, "candidate_stop_price", None)
        warnings.append(
            f"CANDIDATE_STOP_SANITIZED: stop({stop_f:g}) must be below buy({buy_f:g}); mobile Strategy fallback used"
        )

    research = evidence.get("research") or {}
    report_count = int(research.get("report_count") or 0)
    if report_count > 0:
        previous_research = str(getattr(bundle, "research_summary", "") or "")
        if "INSUFFICIENT" in previous_research.upper() or not previous_research:
            original["research_summary"] = previous_research
            setattr(bundle, "research_summary", f"AVAILABLE (mobile parity research: {report_count} reports)")
            warnings.append("RESEARCH_SUMMARY_SYNCED_WITH_PARITY")

    if hasattr(bundle, "generated_at"):
        previous_generated_at = getattr(bundle, "generated_at", None)
        if str(previous_generated_at or "") != generated_at.isoformat():
            original["generated_at"] = str(previous_generated_at or "")
            setattr(bundle, "generated_at", generated_at)
            warnings.append("BUNDLE_GENERATED_AT_NORMALIZED_TO_JOB_KST")

    result = {
        "status": "PASS_WITH_CORRECTIONS" if warnings else "PASS",
        "warnings": warnings,
        "original_values": original,
        "dart_risk_summary": corrected_dart_risks,
        "classified_recent_events": classified_events,
        "financial_sync": financial_sync,
        "rules": {
            "lookback_days": 120,
            "generic_cancel_word_matching": "FORBIDDEN",
            "capital_raising_priority": "HIGH",
            "filing_count_semantics": "RAW_FILINGS_NOT_EVENT_COUNT",
            "cb_series_dedup": "EXPLICIT_SERIES_NUMBER_ONLY",
            "financial_display_source": "DART_PARITY_CANONICAL_8Q",
            "legacy_fundamental_score_on_mismatch": "INVALIDATE",
            "missing_detail_policy": "DO_NOT_INFER",
        },
    }
    evidence["analysis_consistency"] = result
    return result


def append_consistency_appendix(markdown: str, evidence: dict[str, Any]) -> str:
    marker = "# Appendix B. Mobile Analysis Consistency Guard"
    if marker in markdown:
        return markdown
    guard = evidence.get("analysis_consistency") or {}
    financial_sync = guard.get("financial_sync") or {}
    lines = [
        "",
        marker,
        "",
        f"- status: {guard.get('status', 'MISSING')}",
        f"- warnings: {'; '.join(guard.get('warnings') or []) or 'NONE'}",
        f"- corrected DART risk summary: {guard.get('dart_risk_summary', 'MISSING')}",
        f"- financial source: {financial_sync.get('source', 'MISSING')}",
        f"- financial sync status: {financial_sync.get('status', 'MISSING')}",
        f"- financial mismatch fields: {', '.join(financial_sync.get('mismatch_fields') or []) or 'NONE'}",
        "- policy: generic 해제/취소 keywords alone must never be interpreted as sales-contract cancellation",
        "- policy: capital-raising filings are prioritized; allocation method/amount/dilution are not inferred from list titles",
        "- policy: raw DART filing counts include originals/amendments/results and must not be read as distinct-event counts",
        "- policy: CB series are de-duplicated only when an explicit 제N회차 CB title is available",
        "- policy: active-path 8Q financial display uses canonical DART parity rows; conflicting legacy V8 fundamental scores are invalidated",
        "",
        "## Recent classified disclosure events",
        "",
        "| date | category | severity | report | receipt | note |",
        "|---|---|---|---|---|---|",
    ]
    for event in guard.get("classified_recent_events") or []:
        cells = [
            event.get("date"), event.get("category"), event.get("severity"),
            event.get("report_name"), event.get("receipt"), event.get("note"),
        ]
        lines.append("| " + " | ".join(str(v or "MISSING").replace("|", r"\|") for v in cells) + " |")
    return markdown.rstrip() + "\n" + "\n".join(lines) + "\n"
