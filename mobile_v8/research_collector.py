from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from mobile_v8.evidence_collector import _download_pdf_metadata, _new_session, _sha256_bytes

NAVER_STOCK = "https://stock.naver.com"
NAVER_FINANCE = "https://finance.naver.com"
_API_LIST = f"{NAVER_STOCK}/api/stockSecurity/researches/v2/company"
_API_DETAIL = f"{NAVER_STOCK}/api/stockSecurity/researches/v2/company"
_RESEARCH_BASE = f"{NAVER_FINANCE}/research/"
_LEGACY_LIST_ENDPOINTS = (
    f"{_RESEARCH_BASE}company_list.naver",
    f"{_RESEARCH_BASE}company_list.nhn",
)
_DETAIL_RE = re.compile(r"company_read\.(?:naver|nhn)", re.IGNORECASE)
_MAX_REPORTS = 8
_LOOKBACK_DAYS = 365


def _normalize_date(text: str) -> str:
    value = (text or "").strip()
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{2})", value)
    if match:
        yy, mm, dd = match.groups()
        return f"20{yy}-{mm}-{dd}"
    match = re.fullmatch(r"(\d{4})[.-](\d{2})[.-](\d{2})", value)
    if match:
        yyyy, mm, dd = match.groups()
        return f"{yyyy}-{mm}-{dd}"
    return value or "MISSING"


def _as_int_or_missing(value: Any) -> int | str:
    if value in (None, ""):
        return "MISSING"
    try:
        return int(str(value).replace(",", ""))
    except Exception:
        return str(value)


def _content_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "MISSING"
    return BeautifulSoup(value, "lxml").get_text(" ", strip=True) or "MISSING"


def _safe_api_get(session, url: str, *, params: dict[str, Any] | None = None):
    try:
        response = session.get(
            url,
            params=params,
            headers={"Accept": "application/json", "Referer": f"{NAVER_STOCK}/research/company"},
            timeout=20,
        )
    except Exception:
        return None
    return response


def _collect_current_api(session, stock_code: str) -> dict[str, Any] | None:
    response = _safe_api_get(
        session,
        _API_LIST,
        params={"itemCodes": stock_code, "index": 0, "size": 30},
    )
    if response is None or response.status_code != 200:
        return None
    try:
        payload = response.json()
    except Exception:
        return None
    items = payload.get("items") or []
    if not isinstance(items, list):
        return None

    cutoff = date.today() - timedelta(days=_LOOKBACK_DAYS)
    selected: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        write_date = _normalize_date(str(item.get("writeDate") or ""))
        try:
            if write_date != "MISSING" and date.fromisoformat(write_date) < cutoff:
                continue
        except ValueError:
            pass
        selected.append(item)
        if len(selected) >= _MAX_REPORTS:
            break

    reports: list[dict[str, Any]] = []
    for item in selected:
        nid = str(item.get("nid") or "").strip()
        detail_url = f"{_API_DETAIL}/{nid}" if nid else "MISSING"
        detail_status = "MISSING"
        detail: dict[str, Any] = dict(item)
        detail_manifest = "MISSING"

        if nid:
            detail_response = _safe_api_get(session, detail_url)
            if detail_response is not None:
                detail_manifest = _sha256_bytes(detail_response.content)
                if detail_response.status_code == 200:
                    try:
                        detail_payload = detail_response.json()
                    except Exception:
                        detail_status = "INVALID_JSON"
                    else:
                        if isinstance(detail_payload, dict):
                            detail = detail_payload
                            detail_status = "API_OK"
                        else:
                            detail_status = "INVALID_PAYLOAD"
                else:
                    detail_status = f"HTTP_{detail_response.status_code}"

        pdf_url = str(detail.get("attachUrl") or "MISSING")
        if pdf_url != "MISSING":
            pdf_status, pdf_hash, pdf_bytes = _download_pdf_metadata(session, pdf_url)
        else:
            pdf_status, pdf_hash, pdf_bytes = "NO_PDF_LINK", "MISSING", "MISSING"

        content_text = _content_text(detail.get("content"))
        reports.append(
            {
                "date": _normalize_date(str(detail.get("writeDate") or item.get("writeDate") or "")),
                "broker": str(detail.get("brokerName") or item.get("brokerName") or "MISSING"),
                "broker_code": str(detail.get("brokerCode") or item.get("brokerCode") or "MISSING"),
                "analyst": "MISSING",
                "title": str(detail.get("title") or item.get("title") or "MISSING"),
                "report_id": nid or "MISSING",
                "detail_url": detail_url,
                "detail_status": detail_status,
                "target_price": _as_int_or_missing(detail.get("goalPrice", item.get("goalPrice"))),
                "previous_target_price": _as_int_or_missing(detail.get("prevGoalPrice")),
                "opinion": str(detail.get("opinionText") or item.get("opinionText") or "MISSING"),
                "opinion_type": str(detail.get("opinionType") or item.get("opinionType") or "MISSING"),
                "price_at_write_date": _as_int_or_missing(detail.get("priceAtWriteDate")),
                "read_count": _as_int_or_missing(detail.get("readCount", item.get("readCount"))),
                "content_text": content_text,
                "content_hash": _sha256_bytes(content_text.encode("utf-8")) if content_text != "MISSING" else "MISSING",
                "attach_name": str(detail.get("attachName") or "MISSING"),
                "pdf_url": pdf_url,
                "pdf_status": pdf_status,
                "pdf_hash": pdf_hash,
                "pdf_bytes": pdf_bytes,
                "source_manifest": detail_manifest,
            }
        )

    failed = sum(1 for report in reports if report["pdf_status"] != "DOWNLOADED")
    dates = [report["date"] for report in reports if report["date"] != "MISSING"]
    brokers = {report["broker"] for report in reports if report["broker"] != "MISSING"}
    if reports:
        return {
            "quality_status": "AVAILABLE" if failed == 0 else "PARTIAL",
            "reason": "OK" if failed == 0 else "PDF_DOWNLOAD_PARTIAL",
            "report_count": len(reports),
            "failed_report_count": failed,
            "latest_report_date": max(dates) if dates else "MISSING",
            "broker_count": len(brokers),
            "reports": reports,
            "source_manifest": _sha256_bytes(response.content),
            "source_endpoint": _API_LIST,
            "source_total_count": payload.get("totalCount", "MISSING"),
            "lookback_days": _LOOKBACK_DAYS,
            "max_reports": _MAX_REPORTS,
            "diagnostics": [f"NAVER_STOCK_API:HTTP_200:{len(reports)}"],
        }

    return {
        "quality_status": "NOT_AVAILABLE",
        "reason": "NO_REPORTS_IN_LOOKBACK",
        "report_count": 0,
        "failed_report_count": 0,
        "latest_report_date": "MISSING",
        "broker_count": 0,
        "reports": [],
        "source_manifest": _sha256_bytes(response.content),
        "source_endpoint": _API_LIST,
        "source_total_count": payload.get("totalCount", "MISSING"),
        "lookback_days": _LOOKBACK_DAYS,
        "max_reports": _MAX_REPORTS,
        "diagnostics": ["NAVER_STOCK_API:HTTP_200:0"],
    }


def _report_id(detail_url: str) -> str:
    try:
        return (parse_qs(urlparse(detail_url).query).get("nid") or ["MISSING"])[0]
    except Exception:
        return "MISSING"


def _find_pdf_in_detail(session, detail_url: str) -> tuple[str, str]:
    try:
        res = session.get(detail_url, timeout=20)
    except Exception:
        return "DETAIL_FAILED", "MISSING"
    if res.status_code != 200:
        return f"HTTP_{res.status_code}", "MISSING"
    soup = BeautifulSoup(res.content, "lxml")
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if ".pdf" in href.lower():
            return "LEGACY_OK", urljoin(_RESEARCH_BASE, href)
    return "LEGACY_OK", "MISSING"


def _parse_legacy_page(session, endpoint: str, stock_code: str) -> tuple[list[dict[str, Any]], str, str]:
    try:
        res = session.get(
            endpoint,
            params={"searchType": "itemCode", "itemCode": stock_code, "page": 1},
            timeout=20,
            allow_redirects=False,
        )
    except Exception:
        return [], "REQUEST_FAILED", "MISSING"
    if res.status_code != 200:
        return [], f"HTTP_{res.status_code}", _sha256_bytes(res.content)

    soup = BeautifulSoup(res.content, "lxml")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tr in soup.find_all("tr"):
        anchor = tr.find("a", href=_DETAIL_RE)
        if anchor is None:
            continue
        href = str(anchor.get("href") or "")
        detail_url = urljoin(_RESEARCH_BASE, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)

        tds = tr.find_all("td")
        title = anchor.get_text(" ", strip=True) or "MISSING"
        broker = "MISSING"
        report_date = "MISSING"
        pdf_url = "MISSING"
        if len(tds) >= 5:
            broker = tds[2].get_text(" ", strip=True) or "MISSING"
            report_date = _normalize_date(tds[4].get_text(" ", strip=True))
            pdf_anchor = tds[3].find("a", href=True)
            if pdf_anchor is not None:
                pdf_href = str(pdf_anchor.get("href") or "")
                if ".pdf" in pdf_href.lower():
                    pdf_url = urljoin(_RESEARCH_BASE, pdf_href)

        detail_status = "NOT_FETCHED"
        if pdf_url == "MISSING":
            detail_status, detail_pdf = _find_pdf_in_detail(session, detail_url)
            if detail_pdf != "MISSING":
                pdf_url = detail_pdf
        if pdf_url != "MISSING":
            pdf_status, pdf_hash, pdf_bytes = _download_pdf_metadata(session, pdf_url)
        else:
            pdf_status, pdf_hash, pdf_bytes = "NO_PDF_LINK", "MISSING", "MISSING"

        rows.append(
            {
                "date": report_date,
                "broker": broker,
                "broker_code": "MISSING",
                "analyst": "MISSING",
                "title": title,
                "report_id": _report_id(detail_url),
                "detail_url": detail_url,
                "detail_status": detail_status,
                "target_price": "MISSING",
                "previous_target_price": "MISSING",
                "opinion": "MISSING",
                "opinion_type": "MISSING",
                "price_at_write_date": "MISSING",
                "read_count": "MISSING",
                "content_text": "MISSING",
                "content_hash": "MISSING",
                "attach_name": "MISSING",
                "pdf_url": pdf_url,
                "pdf_status": pdf_status,
                "pdf_hash": pdf_hash,
                "pdf_bytes": pdf_bytes,
                "source_manifest": "MISSING",
            }
        )
        if len(rows) >= _MAX_REPORTS:
            break

    return rows, "OK", _sha256_bytes(res.content)


def _collect_legacy(session, stock_code: str) -> dict[str, Any]:
    diagnostics: list[str] = []
    for endpoint in _LEGACY_LIST_ENDPOINTS:
        reports, status, manifest = _parse_legacy_page(session, endpoint, stock_code)
        diagnostics.append(f"{endpoint}:{status}:{len(reports)}")
        if not reports:
            continue
        failed = sum(1 for report in reports if report["pdf_status"] != "DOWNLOADED")
        dates = [report["date"] for report in reports if report["date"] != "MISSING"]
        brokers = {report["broker"] for report in reports if report["broker"] != "MISSING"}
        return {
            "quality_status": "AVAILABLE" if failed == 0 else "PARTIAL",
            "reason": "LEGACY_OK" if failed == 0 else "PDF_DOWNLOAD_PARTIAL",
            "report_count": len(reports),
            "failed_report_count": failed,
            "latest_report_date": max(dates) if dates else "MISSING",
            "broker_count": len(brokers),
            "reports": reports,
            "source_manifest": manifest,
            "source_endpoint": endpoint,
            "source_total_count": "MISSING",
            "lookback_days": "MISSING",
            "max_reports": _MAX_REPORTS,
            "diagnostics": diagnostics,
        }

    return {
        "quality_status": "NOT_AVAILABLE",
        "reason": "NO_REPORTS_FOUND",
        "report_count": 0,
        "failed_report_count": 0,
        "latest_report_date": "MISSING",
        "broker_count": 0,
        "reports": [],
        "source_manifest": "MISSING",
        "source_endpoint": "MISSING",
        "source_total_count": "MISSING",
        "lookback_days": "MISSING",
        "max_reports": _MAX_REPORTS,
        "diagnostics": diagnostics,
    }


def collect_naver_research(stock_code: str) -> dict[str, Any]:
    """Collect research evidence at or above the old offline-package metadata level.

    Current Naver Stock JSON API is primary. It exposes list metadata, detail content,
    target prices/opinions and direct PDF URLs. We verify PDF availability and retain
    hashes/byte counts without attaching the PDFs themselves. Legacy finance.naver HTML
    parsing remains only as a fallback for future API outages.
    """
    session = _new_session()
    current = _collect_current_api(session, stock_code)
    if current is not None:
        return current
    return _collect_legacy(session, stock_code)
