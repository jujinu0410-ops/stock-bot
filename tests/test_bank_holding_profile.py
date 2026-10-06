from datetime import datetime
from types import SimpleNamespace

from mobile_v8.consistency_guard import apply_mobile_consistency_guard
from mobile_v8.evidence_collector import _apply_company_financial_profile


def _quarters():
    rows = []
    for i in range(8):
        rows.append(
            {
                "quarter": f"202{4 + (i // 4)}-Q{(i % 4) + 1}",
                "revenue": "MISSING",
                "operating_income": 1_000_000_000_000 + i * 10_000_000_000,
                "profit_loss": 700_000_000_000 + i * 10_000_000_000,
                "operating_cash_flow": "MISSING" if i == 0 else 100_000_000_000,
                "assets": 500_000_000_000_000 + i * 1_000_000_000_000,
                "liabilities": 465_000_000_000_000 + i * 900_000_000_000,
                "equity": 35_000_000_000_000 + i * 100_000_000_000,
                "scope": "CFS",
                "receipt": f"R{i}",
                "source_manifest": f"M{i}",
                "core_missing": ["revenue"],
                "quality": "INCOMPLETE",
            }
        )
    return rows


def _bundle():
    return SimpleNamespace(
        financial_summary={
            "revenue": 123,
            "operating_profit": 456,
            "ocf": 789,
            "f_score": 4,
            "cat_pts": 1,
            "growth_pts": 1,
            "cf_pts": 1,
            "debt_pts": 1,
        },
        quarterly_8q_table="legacy table",
        prev_vs_current_summary="legacy summary",
        debt_ratio=999.0,
        prev_debt_ratio=888.0,
        ocf_annual=777.0,
        dart_risks="",
        research_summary="",
        bull_evidence=["매출 증가", "기술 추세 양호"],
        bear_evidence=["부채 부담", "가격 부담"],
    )


def test_bank_holding_profile_reclassifies_revenue_missing():
    financials = {
        "financial_profile": "GENERAL",
        "quarters": _quarters(),
        "warnings": ["revenue missing"],
    }
    result = _apply_company_financial_profile(financials, {"induty_code": "64992"})

    assert result["financial_profile"] == "BANK_HOLDING"
    assert result["quarters"][-1]["core_missing"] == []
    assert result["quarters"][-1]["quality"] == "BANK_CORE_VALID"
    assert all("revenue" not in warning.lower() for warning in result["warnings"])


def test_bank_holding_consistency_sync_uses_bank_core_without_revenue():
    bundle = _bundle()
    evidence = {
        "company": {"induty_code": "64992"},
        "financials": {
            "financial_profile": "BANK_HOLDING",
            "quarters": _quarters(),
        },
        "disclosures": {"rows": []},
        "research": {"report_count": 0},
    }

    result = apply_mobile_consistency_guard(
        bundle=bundle,
        evidence=evidence,
        generated_at=datetime(2026, 10, 6, 23, 0, 0),
    )
    sync = result["financial_sync"]

    assert sync["status"] == "BANK_SYNCED"
    assert sync["financial_profile"] == "BANK_HOLDING"
    assert bundle.financial_summary["financial_profile"] == "BANK_HOLDING"
    assert bundle.financial_summary["revenue"] is None
    assert bundle.financial_summary["f_score"] is None
    assert bundle.debt_ratio is None
    assert bundle.ocf_annual is None
    assert "자본/자산" in bundle.quarterly_8q_table
    assert "금융지주 프로파일" in bundle.prev_vs_current_summary


def test_general_profile_still_rejects_missing_revenue():
    bundle = _bundle()
    evidence = {
        "company": {"induty_code": "26110"},
        "financials": {"quarters": _quarters()},
        "disclosures": {"rows": []},
        "research": {"report_count": 0},
    }

    result = apply_mobile_consistency_guard(
        bundle=bundle,
        evidence=evidence,
        generated_at=datetime(2026, 10, 6, 23, 0, 0),
    )
    sync = result["financial_sync"]

    assert sync["status"] == "SKIPPED"
    assert sync["reason"] == "latest_core_missing"
