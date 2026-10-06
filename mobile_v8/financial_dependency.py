from __future__ import annotations

import os
from typing import Any

from mobile_v8.evidence_collector import (
    _fetch_financial_report,
    _financial_summary,
    _new_session,
    _standalone_rows,
)

_PREVIOUS_QUARTER = {
    "Q2": ("Q1", "11013", "FLOW_Q2_REQUIRES_Q1"),
    "Q3": ("Q2", "11012", "FLOW_Q3_REQUIRES_H1"),
    "Q4": ("Q3", "11014", "FLOW_Q4_REQUIRES_9M"),
}


def _rebuild_warnings(reports: list[dict[str, Any]], summaries: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for report, summary in zip(reports, summaries):
        unresolved = sum(
            1
            for row in report.get("rows") or []
            if row.get("computation_status") not in {"VALID", "NOT_COMPUTED"}
        )
        if unresolved or summary.get("quality") != "FULL_VALID":
            warnings.append(
                f"{report['year']}-{report['quarter']}: quality={summary.get('quality')}, "
                f"unresolved={unresolved}, core_missing={','.join(summary.get('core_missing') or []) or 'NONE'}"
            )
    return warnings


def enrich_financial_dependency(
    evidence: dict[str, Any],
    *,
    dart_api_key: str | None = None,
) -> dict[str, Any]:
    """Restore the lookback dependency used by the old offline 8Q package.

    If the oldest quarter in the final 8Q is Q2/Q3/Q4, its standalone flow values may
    require Q1/H1/9M data that sits outside the final eight quarters. Fetch only that
    one dependency, recompute the eight selected quarters with the dependency present,
    and record the dependency lineage explicitly.
    """
    financials = evidence.get("financials") or {}
    reports = list(financials.get("reports") or [])
    if not reports:
        return evidence

    first = reports[0]
    quarter = str(first.get("quarter") or "")
    dependency_spec = _PREVIOUS_QUARTER.get(quarter)
    financials["selection_reason"] = "LAST_8_COMPLETED_WITH_DEPENDENCY_LOOKBACK"
    financials["available_report_count"] = financials.get("available_report_count", "MISSING")
    financials["selected_manifests"] = [
        str(report.get("source_manifest") or "MISSING") for report in reports
    ]

    if dependency_spec is None:
        financials["dependencies"] = []
        evidence["financials"] = financials
        return evidence

    previous_quarter, report_code, reason = dependency_spec
    api_key = dart_api_key or os.environ.get("DART_API_KEY") or ""
    if not api_key:
        dependency = {
            "scope": financials.get("canonical_scope", "MISSING"),
            "fiscal_year": first.get("year", "MISSING"),
            "report_code": report_code,
            "label": f"{first.get('year')}-{previous_quarter}",
            "dependency_only": True,
            "required_by": f"{first.get('year')}-{quarter}",
            "reason": reason,
            "status": "MISSING",
            "receipt": "MISSING",
            "source_manifest": "MISSING",
        }
        financials["dependencies"] = [dependency]
        evidence["financials"] = financials
        issues = list(evidence.get("issues") or [])
        issues.append("FINANCIAL_DEPENDENCY_MISSING:DART_API_KEY")
        evidence["issues"] = issues
        evidence["status"] = "PARITY_INCOMPLETE"
        return evidence

    session = _new_session()
    dependency_report = _fetch_financial_report(
        session,
        api_key,
        str(evidence.get("corp_code") or ""),
        int(first["year"]),
        report_code,
        str(financials.get("canonical_scope") or first.get("scope") or "OFS"),
    )

    if dependency_report is None:
        dependency = {
            "scope": financials.get("canonical_scope", "MISSING"),
            "fiscal_year": first.get("year", "MISSING"),
            "report_code": report_code,
            "label": f"{first.get('year')}-{previous_quarter}",
            "dependency_only": True,
            "required_by": f"{first.get('year')}-{quarter}",
            "reason": reason,
            "status": "MISSING",
            "receipt": "MISSING",
            "source_manifest": "MISSING",
        }
        financials["dependencies"] = [dependency]
        evidence["financials"] = financials
        issues = list(evidence.get("issues") or [])
        issues.append(f"FINANCIAL_DEPENDENCY_MISSING:{first.get('year')}-{previous_quarter}")
        evidence["issues"] = issues
        evidence["status"] = "PARITY_INCOMPLETE"
        return evidence

    dependency_report["quarter"] = previous_quarter
    selected_raw = [
        {
            "year": report["year"],
            "report_code": report["report_code"],
            "scope": report["scope"],
            "receipt": report["receipt"],
            "raw_items": report["raw_items"],
            "source_manifest": report["source_manifest"],
            "quarter": report["quarter"],
        }
        for report in reports
    ]
    recomputed = _standalone_rows([dependency_report, *selected_raw])[-8:]
    summaries = [_financial_summary(report) for report in recomputed]

    dependency = {
        "scope": dependency_report["scope"],
        "fiscal_year": dependency_report["year"],
        "report_code": dependency_report["report_code"],
        "label": f"{dependency_report['year']}-{dependency_report['quarter']}",
        "dependency_only": True,
        "required_by": f"{first['year']}-{quarter}",
        "reason": reason,
        "status": "AVAILABLE",
        "receipt": dependency_report["receipt"],
        "source_manifest": dependency_report["source_manifest"],
    }

    financials["reports"] = recomputed
    financials["quarters"] = summaries
    financials["dependencies"] = [dependency]
    financials["warnings"] = _rebuild_warnings(recomputed, summaries)
    financials["selected_manifests"] = [
        str(report.get("source_manifest") or "MISSING") for report in recomputed
    ]
    evidence["financials"] = financials
    return evidence
