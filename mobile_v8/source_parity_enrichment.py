from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

import requests

from mobile_v8.evidence_collector import _sha256_bytes

NAVER_BASIC = "https://m.stock.naver.com/api/stock/{code}/basic"
NAVER_POLLING = "https://polling.finance.naver.com/api/realtime"
USER_AGENT = "Mozilla/5.0 (compatible; MobileV8Parity/1.0)"


def _missing(value: Any) -> Any:
    return "MISSING" if value in (None, "") else value


def _num(value: Any) -> int | float | str:
    if value in (None, ""):
        return "MISSING"
    text = str(value).replace(",", "").strip()
    if not text:
        return "MISSING"
    try:
        number = float(text)
    except ValueError:
        return str(value)
    return int(number) if number.is_integer() else number


def _chain_id(receipt: Any, report_name: Any) -> str:
    raw = f"{receipt}|{report_name}".encode("utf-8")
    return "chain_" + hashlib.sha256(raw).hexdigest()[:16]


def enrich_disclosure_chains(evidence: dict[str, Any]) -> dict[str, Any]:
    """Restore the old package's chain/effective-filing audit layer conservatively.

    The current DART list API does not expose a reliable machine-readable parent link
    for arbitrary amendment filings. Therefore no cross-filing relationship is
    invented: each filing gets an auditable chain record, amendment-looking filings
    remain REVIEW_REQUIRED/LOW confidence, and latest_effective stays MISSING unless a
    future authoritative linker is added. This preserves the old package's explicit
    uncertainty instead of guessing.
    """
    disclosures = evidence.get("disclosures") or {}
    rows = disclosures.get("rows") or []
    chains: list[dict[str, Any]] = []
    effective: list[dict[str, Any]] = []
    for row in rows:
        receipt = row.get("rcept_no", "MISSING")
        report_name = row.get("report_nm", "MISSING")
        state = row.get("amendment_state", "UNLINKED")
        manifest = row.get("source_manifest", "MISSING")
        chain = _chain_id(receipt, report_name)
        confidence = "LOW" if state == "REVIEW_REQUIRED" else "NONE"
        chains.append({
            "chain": chain,
            "event_type": row.get("report_type", "UNCLASSIFIED"),
            "status": state,
            "confidence": confidence,
            "original_receipt": receipt,
            "latest_effective": "MISSING",
            "versions": f"{receipt}:{state}",
            "source_manifests": manifest,
        })
        effective.append({
            "chain": chain,
            "effective_receipt": "MISSING",
            "state": state,
            "source_manifest": "MISSING",
        })
    disclosures["chains"] = chains
    disclosures["latest_effective_filings"] = effective
    disclosures["chain_method"] = "CONSERVATIVE_UNLINKED_AUDIT"
    disclosures["chain_note"] = (
        "DART list evidence does not prove amendment parentage; no cross-filing link is inferred."
    )
    evidence["disclosures"] = disclosures
    return evidence


def _fetch_json(session: requests.Session, url: str, **kwargs: Any) -> tuple[dict[str, Any] | None, str, str]:
    try:
        response = session.get(url, timeout=20, **kwargs)
    except Exception as exc:
        return None, "MISSING", f"REQUEST_FAILED:{type(exc).__name__}"
    manifest = _sha256_bytes(response.content)
    if response.status_code != 200:
        return None, manifest, f"HTTP_{response.status_code}"
    try:
        data = response.json()
    except Exception:
        return None, manifest, "INVALID_JSON"
    if not isinstance(data, dict):
        return None, manifest, "INVALID_PAYLOAD"
    return data, manifest, "OK"


def enrich_market_quote(
    evidence: dict[str, Any],
    *,
    stock_code: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    """Add current Naver quote/source lineage beside the 250D evidence."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"})

    basic, basic_manifest, basic_status = _fetch_json(
        session,
        NAVER_BASIC.format(code=stock_code),
        headers={"Accept": "application/json"},
    )
    polling, polling_manifest, polling_status = _fetch_json(
        session,
        NAVER_POLLING,
        params={"query": f"SERVICE_ITEM:{stock_code}"},
        headers={"Accept": "application/json"},
    )

    pdata: dict[str, Any] = {}
    if polling:
        try:
            pdata = ((polling.get("result") or {}).get("areas") or [])[0].get("datas", [])[0]
        except (IndexError, AttributeError, TypeError):
            pdata = {}

    basic = basic or {}
    price = pdata.get("nv") if pdata.get("nv") is not None else basic.get("closePrice")
    market_status = pdata.get("ms") or basic.get("marketStatus") or "MISSING"
    source_timestamp = basic.get("localTradedAt") or "MISSING"
    missing_fields: list[str] = []
    field_map = {
        "price": price,
        "previous_close": pdata.get("sv"),
        "change": pdata.get("cv") if pdata.get("cv") is not None else basic.get("compareToPreviousClosePrice"),
        "change_rate": pdata.get("cr") if pdata.get("cr") is not None else basic.get("fluctuationsRatio"),
        "volume": pdata.get("aq"),
        "trading_value": pdata.get("aa"),
    }
    for key, value in field_map.items():
        if value in (None, ""):
            missing_fields.append(key)

    quote = {
        "stock_code": stock_code,
        "stock_name": _missing(basic.get("stockName")),
        "source_timestamp": _missing(source_timestamp),
        "fetched_at": fetched_at.isoformat(),
        "price": _num(price),
        "previous_close": _num(pdata.get("sv")),
        "change": _num(pdata.get("cv") if pdata.get("cv") is not None else basic.get("compareToPreviousClosePrice")),
        "change_rate": _num(pdata.get("cr") if pdata.get("cr") is not None else basic.get("fluctuationsRatio")),
        "open": _num(pdata.get("ov")),
        "high": _num(pdata.get("hv")),
        "low": _num(pdata.get("lv")),
        "volume": _num(pdata.get("aq")),
        "trading_value": _num(pdata.get("aa")),
        "quote_status": "INTRADAY" if str(market_status).upper() == "OPEN" else "SNAPSHOT",
        "market_status": market_status,
        "parse_status": "FULL" if not missing_fields else "PARTIAL",
        "parse_issues": missing_fields,
        "basic_status": basic_status,
        "polling_status": polling_status,
        "basic_source_manifest": basic_manifest,
        "polling_source_manifest": polling_manifest,
        "sources": [NAVER_BASIC.format(code=stock_code), f"{NAVER_POLLING}?query=SERVICE_ITEM:{stock_code}"],
        "lineage": "NAVER_PUBLIC_API -> MOBILE_V8_PARITY_NORMALIZE",
    }

    market = evidence.get("market") or {}
    market["quote"] = quote
    market["lineage"] = quote["lineage"]
    market["quote_source_manifests"] = [basic_manifest, polling_manifest]
    evidence["market"] = market

    if quote["price"] == "MISSING":
        issues = list(evidence.get("issues") or [])
        if "MARKET_QUOTE_MISSING" not in issues:
            issues.append("MARKET_QUOTE_MISSING")
        evidence["issues"] = issues
        evidence["status"] = "PARITY_INCOMPLETE"
    return evidence


def enrich_source_parity(
    evidence: dict[str, Any],
    *,
    stock_code: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    evidence = enrich_disclosure_chains(evidence)
    evidence = enrich_market_quote(evidence, stock_code=stock_code, fetched_at=fetched_at)
    return evidence
