from __future__ import annotations

import hashlib
import io
import os
import re
import zipfile
from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DART_BASE = "https://opendart.fss.or.kr/api"
NAVER_CHART = "https://fchart.stock.naver.com/sise.nhn"
NAVER_FINANCE = "https://finance.naver.com"
USER_AGENT = "Mozilla/5.0 (compatible; MobileV8Parity/1.0; +https://finance.naver.com)"

_REPORT_CODES = (
    ("11013", "Q1"),
    ("11012", "Q2"),
    ("11014", "Q3"),
    ("11011", "Q4"),
)
_REGULAR_KINDS = ("사업보고서", "반기보고서", "분기보고서")


class ParityCollectionError(RuntimeError):
    pass


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _missing(value: Any) -> Any:
    if value is None or value == "":
        return "MISSING"
    return value


def _num(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in {"", "-", "None", "null", "MISSING"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _request_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any],
    timeout: int = 20,
) -> tuple[dict[str, Any], str]:
    res = session.get(url, params=params, timeout=timeout)
    res.raise_for_status()
    raw = res.content
    return res.json(), _sha256_bytes(raw)


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6"})
    return session


_CORP_MAP_CACHE: dict[str, str] | None = None


def _load_corp_map(session: requests.Session, api_key: str) -> tuple[dict[str, str], str]:
    global _CORP_MAP_CACHE
    res = session.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": api_key}, timeout=30)
    res.raise_for_status()
    raw = res.content
    if _CORP_MAP_CACHE is None:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            xml_bytes = zf.read("CORPCODE.xml")
        soup = BeautifulSoup(xml_bytes, "xml")
        mapping: dict[str, str] = {}
        for row in soup.find_all("list"):
            stock = (row.stock_code.text if row.stock_code else "").strip()
            corp = (row.corp_code.text if row.corp_code else "").strip()
            if stock and corp:
                mapping[stock] = corp
        _CORP_MAP_CACHE = mapping
    return _CORP_MAP_CACHE, _sha256_bytes(raw)


def _get_company(
    session: requests.Session,
    api_key: str,
    corp_code: str,
    expected_stock_code: str,
) -> dict[str, Any]:
    data, manifest = _request_json(
        session,
        f"{DART_BASE}/company.json",
        params={"crtfc_key": api_key, "corp_code": corp_code},
    )
    if data.get("status") != "000":
        raise ParityCollectionError(f"DART_COMPANY_{data.get('status')}_{data.get('message')}")
    actual = str(data.get("stock_code") or "").strip()
    if actual != expected_stock_code:
        raise ParityCollectionError(f"DART_STOCK_CODE_MISMATCH:{expected_stock_code}!={actual}")
    fields = (
        "corp_name", "corp_name_eng", "stock_name", "stock_code", "ceo_nm", "corp_cls",
        "jurir_no", "bizr_no", "adres", "hm_url", "ir_url", "phn_no", "fax_no", "induty_code",
        "est_dt", "acc_mt",
    )
    return {
        "corp_code": corp_code,
        "source_manifest": manifest,
        **{field: _missing(data.get(field)) for field in fields},
    }


def _collect_disclosures(
    session: requests.Session,
    api_key: str,
    corp_code: str,
    asof: date,
) -> dict[str, Any]:
    bgn_de = f"{asof.year - 3}0101"
    end_de = asof.strftime("%Y%m%d")
    rows: list[dict[str, Any]] = []
    manifests: list[str] = []
    page_no = 1
    total_pages = 1
    while page_no <= total_pages:
        data, manifest = _request_json(
            session,
            f"{DART_BASE}/list.json",
            params={
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": page_no,
                "page_count": 100,
                "sort": "date",
                "sort_mth": "desc",
            },
        )
        if data.get("status") not in {"000", "013"}:
            raise ParityCollectionError(f"DART_LIST_{data.get('status')}_{data.get('message')}")
        manifests.append(manifest)
        if data.get("status") == "013":
            break
        total_pages = int(data.get("total_page") or 1)
        for item in data.get("list") or []:
            report_nm = str(item.get("report_nm") or "").strip()
            regular = any(kind in report_nm for kind in _REGULAR_KINDS)
            amendment = bool(re.search(r"\[(?:기재|첨부|정정|철회).*?\]", report_nm)) or "정정" in report_nm
            rows.append({
                "rcept_dt": _missing(item.get("rcept_dt")),
                "report_nm": _missing(report_nm),
                "rcept_no": _missing(item.get("rcept_no")),
                "flr_nm": _missing(item.get("flr_nm")),
                "rm": _missing(item.get("rm")),
                "report_type": "REGULAR_REPORT" if regular else "UNCLASSIFIED",
                "amendment_state": "REVIEW_REQUIRED" if amendment else "UNLINKED",
                "source_manifest": manifest,
            })
        page_no += 1
    return {
        "begin_date": bgn_de,
        "end_date": end_de,
        "count": len(rows),
        "rows": rows,
        "source_manifests": manifests,
    }


def _fetch_financial_report(
    session: requests.Session,
    api_key: str,
    corp_code: str,
    year: int,
    report_code: str,
    scope: str,
) -> dict[str, Any] | None:
    data, manifest = _request_json(
        session,
        f"{DART_BASE}/fnlttSinglAcntAll.json",
        params={
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code,
            "fs_div": scope,
        },
    )
    if data.get("status") != "000" or not data.get("list"):
        return None
    items = data["list"]
    return {
        "year": year,
        "report_code": report_code,
        "scope": scope,
        "receipt": _missing(items[0].get("rcept_no")),
        "raw_items": items,
        "source_manifest": manifest,
    }


def _candidate_financial_reports(
    session: requests.Session,
    api_key: str,
    corp_code: str,
    asof: date,
    scope: str,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for year in range(asof.year - 3, asof.year + 1):
        for report_code, quarter in _REPORT_CODES:
            report = _fetch_financial_report(session, api_key, corp_code, year, report_code, scope)
            if report:
                report["quarter"] = quarter
                reports.append(report)
    reports.sort(key=lambda x: (x["year"], int(x["quarter"][1:])))
    return reports


def _row_key(item: dict[str, Any]) -> str:
    return "|".join([
        str(item.get("sj_div") or "").strip().upper(),
        str(item.get("account_id") or "").strip(),
        str(item.get("account_nm") or "").strip(),
    ])


def _row_key_loose(item: dict[str, Any]) -> str:
    return "|".join([
        str(item.get("sj_div") or "").strip().upper(),
        str(item.get("account_id") or "").strip(),
    ])


def _cumulative_value(item: dict[str, Any] | None) -> float | None:
    if not item:
        return None
    add = _num(item.get("thstrm_add_amount"))
    cur = _num(item.get("thstrm_amount"))
    return add if add is not None else cur


def _standalone_rows(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    report_maps: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    report_loose_maps: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    for report in reports:
        period = (report["year"], report["quarter"])
        report_maps[period] = {_row_key(item): item for item in report["raw_items"]}
        report_loose_maps[period] = {_row_key_loose(item): item for item in report["raw_items"]}

    output: list[dict[str, Any]] = []
    for report in reports:
        year = report["year"]
        q = report["quarter"]
        prev_q = {"Q2": "Q1", "Q3": "Q2", "Q4": "Q3"}.get(q)
        prev_period = (year, prev_q) if prev_q else None
        prev_map = report_maps.get(prev_period, {}) if prev_period else {}
        prev_loose_map = report_loose_maps.get(prev_period, {}) if prev_period else {}
        rows: list[dict[str, Any]] = []
        for item in report["raw_items"]:
            statement = str(item.get("sj_div") or "").strip().upper()
            key = _row_key(item)
            loose_key = _row_key_loose(item)
            current = _num(item.get("thstrm_amount"))
            current_add = _num(item.get("thstrm_add_amount"))
            value: float | None = None
            formula = "NOT_COMPUTED"
            status = "VALID"

            if statement == "BS":
                value = current
                formula = "STOCK_PERIOD_END_DIRECT"
            elif statement in {"IS", "CIS"}:
                if q == "Q1":
                    value = current_add if current_add is not None else current
                    formula = "FLOW_Q1_DIRECT"
                elif q in {"Q2", "Q3"}:
                    if current is not None and current_add is not None:
                        value = current
                        formula = f"FLOW_{q}_3M_DIRECT"
                    elif current is not None:
                        value = current
                        formula = f"FLOW_{q}_DIRECT_FALLBACK"
                        status = "VALID_WITH_WARNING"
                    else:
                        status = "ACCOUNT_MAPPING_UNRESOLVED"
                elif q == "Q4":
                    cum = _cumulative_value(item)
                    prev_item = prev_map.get(key) or prev_loose_map.get(loose_key)
                    prev_cum = _cumulative_value(prev_item)
                    if cum is not None and prev_cum is not None:
                        value = cum - prev_cum
                        formula = "FLOW_Q4_FY_MINUS_9M"
                    else:
                        status = "ACCOUNT_MAPPING_UNRESOLVED"
                else:
                    status = "ACCOUNT_MAPPING_UNRESOLVED"
            elif statement == "CF":
                cum = _cumulative_value(item)
                if q == "Q1":
                    value = cum
                    formula = "FLOW_CF_Q1_DIRECT"
                else:
                    prev_item = prev_map.get(key) or prev_loose_map.get(loose_key)
                    prev_cum = _cumulative_value(prev_item)
                    if cum is not None and prev_cum is not None:
                        value = cum - prev_cum
                        formula = f"FLOW_CF_{q}_CUM_MINUS_PREV"
                    else:
                        status = "ACCOUNT_MAPPING_UNRESOLVED"
            else:
                status = "NOT_COMPUTED"

            rows.append({
                "statement": statement or "MISSING",
                "account_id": _missing(item.get("account_id")),
                "account_name": _missing(item.get("account_nm")),
                "account_detail": _missing(item.get("account_detail")),
                "raw_current": _missing(item.get("thstrm_amount")),
                "raw_cumulative": _missing(item.get("thstrm_add_amount")),
                "standalone_value": value if value is not None else "MISSING",
                "computation_status": status,
                "formula": formula,
                "receipt": report["receipt"],
                "scope": report["scope"],
                "currency": _missing(item.get("currency")),
                "source_manifest": report["source_manifest"],
            })
        output.append({**report, "rows": rows})
    return output


def _find_metric(
    rows: Iterable[dict[str, Any]],
    *,
    ids: tuple[str, ...],
    names: tuple[str, ...],
) -> Any:
    normalized_names = {n.replace(" ", "") for n in names}
    for row in rows:
        aid = str(row.get("account_id") or "").strip()
        aname = str(row.get("account_name") or "").replace(" ", "")
        if aid in ids or aname in normalized_names:
            return row.get("standalone_value", "MISSING")
    return "MISSING"


def _financial_summary(report: dict[str, Any]) -> dict[str, Any]:
    rows = report["rows"]
    metrics = {
        "revenue": _find_metric(
            rows,
            ids=("ifrs-full_Revenue", "ifrs-full_SalesRevenue"),
            names=("매출액", "수익(매출액)", "매출"),
        ),
        "operating_income": _find_metric(
            rows,
            ids=("dart_OperatingIncomeLoss", "ifrs-full_OperatingIncomeLoss"),
            names=("영업이익", "영업이익(손실)"),
        ),
        "profit_loss": _find_metric(
            rows,
            ids=("ifrs-full_ProfitLoss",),
            names=("당기순이익", "당기순이익(손실)", "연결당기순이익"),
        ),
        "operating_cash_flow": _find_metric(
            rows,
            ids=("ifrs-full_CashFlowsFromUsedInOperatingActivities",),
            names=("영업활동현금흐름", "영업활동으로인한현금흐름"),
        ),
        "assets": _find_metric(rows, ids=("ifrs-full_Assets",), names=("자산총계",)),
        "liabilities": _find_metric(rows, ids=("ifrs-full_Liabilities",), names=("부채총계",)),
        "equity": _find_metric(
            rows,
            ids=("ifrs-full_Equity", "ifrs-full_EquityAttributableToOwnersOfParent"),
            names=("자본총계",),
        ),
    }
    core_keys = ("revenue", "operating_income", "assets", "liabilities", "equity")
    core_missing = [key for key in core_keys if metrics[key] == "MISSING"]
    warning_rows = [row for row in rows if row["computation_status"] not in {"VALID", "NOT_COMPUTED"}]
    if core_missing:
        quality = "INCOMPLETE"
    elif warning_rows or metrics["profit_loss"] == "MISSING" or metrics["operating_cash_flow"] == "MISSING":
        quality = "CORE_VALID_WITH_WARNINGS"
    else:
        quality = "FULL_VALID"
    return {
        "quarter": f"{report['year']}-{report['quarter']}",
        **metrics,
        "core_missing": core_missing,
        "warning_count": len(warning_rows),
        "quality": quality,
        "scope": report["scope"],
        "receipt": report["receipt"],
        "source_manifest": report["source_manifest"],
    }


def _collect_financials(
    session: requests.Session,
    api_key: str,
    corp_code: str,
    asof: date,
) -> dict[str, Any]:
    by_scope = {
        scope: _candidate_financial_reports(session, api_key, corp_code, asof, scope)
        for scope in ("CFS", "OFS")
    }
    scope = "CFS" if len(by_scope["CFS"]) >= 8 else "OFS"
    available = by_scope[scope]
    if len(available) < 8:
        scope = "OFS" if scope == "CFS" else "CFS"
        available = by_scope[scope]
    selected = available[-8:]
    standalone = _standalone_rows(selected)
    summaries = [_financial_summary(report) for report in standalone]
    warnings: list[str] = []
    for report, summary in zip(standalone, summaries):
        unresolved = sum(
            1 for row in report["rows"]
            if row["computation_status"] not in {"VALID", "NOT_COMPUTED"}
        )
        if unresolved or summary["quality"] != "FULL_VALID":
            warnings.append(
                f"{report['year']}-{report['quarter']}: quality={summary['quality']}, "
                f"unresolved={unresolved}, core_missing={','.join(summary['core_missing']) or 'NONE'}"
            )
    return {
        "canonical_scope": scope,
        "quarter_count": len(selected),
        "status": "VALID" if len(selected) == 8 else "INCOMPLETE",
        "financial_profile": "GENERAL",
        "quarters": summaries,
        "reports": standalone,
        "warnings": warnings,
    }


def _apply_company_financial_profile(
    financials: dict[str, Any],
    company: dict[str, Any],
) -> dict[str, Any]:
    """Apply a narrow industry-aware quality profile without inventing values.

    64992 is the Korean industry code for financial holding companies.  Their
    consolidated statements do not expose one manufacturing-style Revenue row;
    interest, fee, insurance and other operating income are split across rows.
    For this profile, revenue and OCF are not core completeness requirements.
    """
    induty_code = str(company.get("induty_code") or "").strip()
    if induty_code != "64992":
        financials["financial_profile"] = "GENERAL"
        return financials

    financials["financial_profile"] = "BANK_HOLDING"
    warnings: list[str] = []
    for quarter in financials.get("quarters") or []:
        required = ("operating_income", "profit_loss", "assets", "equity")
        missing = [key for key in required if quarter.get(key) == "MISSING"]
        quarter["financial_profile"] = "BANK_HOLDING"
        quarter["core_missing"] = missing
        if missing:
            quarter["quality"] = "BANK_CORE_INCOMPLETE"
        elif quarter.get("operating_cash_flow") == "MISSING":
            quarter["quality"] = "BANK_CORE_VALID_WITH_WARNINGS"
        else:
            quarter["quality"] = "BANK_CORE_VALID"

        if missing:
            warnings.append(
                f"{quarter.get('quarter')}: quality={quarter['quality']}, "
                f"core_missing={','.join(missing)}"
            )
        elif quarter.get("operating_cash_flow") == "MISSING":
            warnings.append(
                f"{quarter.get('quarter')}: quality={quarter['quality']}, "
                "optional_missing=operating_cash_flow"
            )

    financials["warnings"] = warnings
    return financials


def _collect_market(session: requests.Session, stock_code: str) -> dict[str, Any]:
    res = session.get(
        NAVER_CHART,
        params={"symbol": stock_code, "timeframe": "day", "count": 250, "requestType": 0},
        timeout=25,
    )
    res.raise_for_status()
    raw = res.content
    soup = BeautifulSoup(raw, "xml")
    candles: list[dict[str, Any]] = []
    for item in soup.find_all("item"):
        data = str(item.get("data") or "")
        parts = data.split("|")
        if len(parts) < 6:
            continue
        d, o, h, low, c, v = parts[:6]
        candles.append({
            "date": d,
            "open": _num(o),
            "high": _num(h),
            "low": _num(low),
            "close": _num(c),
            "volume": _num(v),
        })
    candles = candles[-250:]
    duplicate_count = len(candles) - len({c["date"] for c in candles})
    ohlc_warnings = 0
    for candle in candles:
        values = (candle["open"], candle["high"], candle["low"], candle["close"])
        if any(value is None for value in values):
            ohlc_warnings += 1
            continue
        if (
            candle["high"] < max(candle["open"], candle["close"], candle["low"])
            or candle["low"] > min(candle["open"], candle["close"], candle["high"])
        ):
            ohlc_warnings += 1
    return {
        "source": "naver",
        "source_manifest": _sha256_bytes(raw),
        "count": len(candles),
        "earliest": candles[0]["date"] if candles else "MISSING",
        "latest": candles[-1]["date"] if candles else "MISSING",
        "duplicate_count": duplicate_count,
        "ohlc_warning_count": ohlc_warnings,
        "quality": (
            "VALID"
            if len(candles) == 250 and duplicate_count == 0 and ohlc_warnings == 0
            else "INVALID"
        ),
        "candles": candles,
    }


def _report_kind(name: str) -> str | None:
    for kind in _REGULAR_KINDS:
        if kind in name:
            return kind
    return None


def _collect_periodic_documents(
    session: requests.Session,
    api_key: str,
    disclosures: dict[str, Any],
) -> dict[str, Any]:
    selected: dict[str, dict[str, Any]] = {}
    for row in disclosures["rows"]:
        name = str(row["report_nm"])
        kind = _report_kind(name)
        if kind and kind not in selected:
            selected[kind] = row
        if len(selected) == len(_REGULAR_KINDS):
            break

    docs: list[dict[str, Any]] = []
    for kind in _REGULAR_KINDS:
        row = selected.get(kind)
        if not row:
            docs.append({
                "kind": kind,
                "report_nm": "MISSING",
                "receipt": "MISSING",
                "filing_date": "MISSING",
                "download_status": "MISSING",
                "zip_hash": "MISSING",
                "zip_bytes": "MISSING",
                "files": [],
            })
            continue
        res = session.get(
            f"{DART_BASE}/document.xml",
            params={"crtfc_key": api_key, "rcept_no": row["rcept_no"]},
            timeout=40,
        )
        if res.status_code != 200:
            docs.append({
                "kind": kind,
                "report_nm": row["report_nm"],
                "receipt": row["rcept_no"],
                "filing_date": row["rcept_dt"],
                "download_status": f"HTTP_{res.status_code}",
                "zip_hash": "MISSING",
                "zip_bytes": len(res.content),
                "files": [],
            })
            continue
        raw = res.content
        filenames: list[str] = []
        status = "DOWNLOADED"
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                filenames = zf.namelist()
        except zipfile.BadZipFile:
            status = "INVALID_ZIP"
        docs.append({
            "kind": kind,
            "report_nm": row["report_nm"],
            "receipt": row["rcept_no"],
            "filing_date": row["rcept_dt"],
            "download_status": status,
            "zip_hash": _sha256_bytes(raw),
            "zip_bytes": len(raw),
            "files": filenames,
        })
    ok = sum(1 for doc in docs if doc["download_status"] == "DOWNLOADED")
    return {"document_count": len(docs), "downloaded_count": ok, "documents": docs}


def _download_pdf_metadata(
    session: requests.Session,
    url: str,
) -> tuple[str, str, Any]:
    try:
        with session.get(url, timeout=30, stream=True) as res:
            if res.status_code != 200:
                return f"HTTP_{res.status_code}", "MISSING", "MISSING"
            content_length = int(res.headers.get("Content-Length") or 0)
            if content_length > 30 * 1024 * 1024:
                return "TOO_LARGE", "MISSING", content_length
            digest = hashlib.sha256()
            total = 0
            for chunk in res.iter_content(1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 30 * 1024 * 1024:
                    return "TOO_LARGE", "MISSING", total
                digest.update(chunk)
            return "DOWNLOADED", digest.hexdigest(), total
    except requests.RequestException:
        return "DOWNLOAD_FAILED", "MISSING", "MISSING"


def _collect_research(session: requests.Session, stock_code: str) -> dict[str, Any]:
    url = f"{NAVER_FINANCE}/research/company_list.naver"
    res = session.get(url, params={"searchType": "itemCode", "itemCode": stock_code}, timeout=20)
    if res.status_code != 200:
        return {"quality_status": "NOT_AVAILABLE", "reason": f"HTTP_{res.status_code}", "reports": []}
    soup = BeautifulSoup(res.content, "lxml")
    reports: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="company_read.naver"]'):
        href = str(anchor.get("href") or "")
        detail_url = urljoin(NAVER_FINANCE, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)
        title = anchor.get_text(" ", strip=True)
        tr = anchor.find_parent("tr")
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")] if tr else []
        broker = cells[1] if len(cells) > 1 else "MISSING"
        report_date = cells[-1] if cells else "MISSING"
        detail_status = "DOWNLOADED"
        pdf_url = "MISSING"
        pdf_status = "MISSING"
        pdf_hash = "MISSING"
        pdf_bytes: Any = "MISSING"
        try:
            detail_res = session.get(detail_url, timeout=20)
            if detail_res.status_code == 200:
                detail_soup = BeautifulSoup(detail_res.content, "lxml")
                pdf_link = None
                for link in detail_soup.find_all("a", href=True):
                    href2 = str(link.get("href") or "")
                    if ".pdf" in href2.lower():
                        pdf_link = urljoin(NAVER_FINANCE, href2)
                        break
                if pdf_link:
                    pdf_url = pdf_link
                    pdf_status, pdf_hash, pdf_bytes = _download_pdf_metadata(session, pdf_link)
                else:
                    pdf_status = "NO_PDF_LINK"
            else:
                detail_status = f"HTTP_{detail_res.status_code}"
        except requests.RequestException:
            detail_status = "DETAIL_FAILED"
        reports.append({
            "date": report_date,
            "broker": broker,
            "title": title or "MISSING",
            "detail_url": detail_url,
            "detail_status": detail_status,
            "pdf_url": pdf_url,
            "pdf_status": pdf_status,
            "pdf_hash": pdf_hash,
            "pdf_bytes": pdf_bytes,
        })
        if len(reports) >= 20:
            break

    if not reports:
        return {"quality_status": "NOT_AVAILABLE", "reason": "NO_REPORTS_FOUND", "reports": []}
    failed = sum(1 for report in reports if report["pdf_status"] != "DOWNLOADED")
    return {
        "quality_status": "AVAILABLE" if failed == 0 else "PARTIAL",
        "reason": "OK" if failed == 0 else "PDF_DOWNLOAD_PARTIAL",
        "report_count": len(reports),
        "failed_report_count": failed,
        "reports": reports,
        "source_manifest": _sha256_bytes(res.content),
    }


def _parity_status(
    company: dict[str, Any],
    disclosures: dict[str, Any],
    financials: dict[str, Any],
    market: dict[str, Any],
    periodic: dict[str, Any],
) -> tuple[str, list[str]]:
    issues: list[str] = []
    if not company or company.get("stock_code") in {"MISSING", None}:
        issues.append("COMPANY_MISSING")
    if disclosures.get("count", 0) <= 0:
        issues.append("DISCLOSURES_MISSING")
    if financials.get("quarter_count") != 8:
        issues.append(f"FINANCIAL_8Q_COUNT_{financials.get('quarter_count')}")
    if market.get("count") != 250:
        issues.append(f"MARKET_250D_COUNT_{market.get('count')}")
    if market.get("quality") != "VALID":
        issues.append("MARKET_250D_QUALITY_INVALID")
    docs = periodic.get("documents") or []
    missing_kinds = [
        doc.get("kind") for doc in docs
        if doc.get("download_status") != "DOWNLOADED"
    ]
    if missing_kinds:
        issues.append("DART_PERIODIC_DOCS_INCOMPLETE:" + ",".join(str(x) for x in missing_kinds))
    return ("PARITY_READY" if not issues else "PARITY_INCOMPLETE", issues)


def collect_offline_parity(
    *,
    stock_code: str,
    stock_name: str,
    generated_at: datetime,
    dart_api_key: str | None = None,
) -> dict[str, Any]:
    api_key = dart_api_key or os.environ.get("DART_API_KEY") or ""
    if not api_key:
        raise ParityCollectionError("DART_API_KEY_MISSING")
    if not re.fullmatch(r"\d{6}", stock_code):
        raise ParityCollectionError(f"INVALID_STOCK_CODE:{stock_code}")

    session = _new_session()
    asof = generated_at.date()
    corp_map, corp_map_manifest = _load_corp_map(session, api_key)
    corp_code = corp_map.get(stock_code)
    if not corp_code:
        raise ParityCollectionError(f"DART_CORP_CODE_NOT_FOUND:{stock_code}")

    company = _get_company(session, api_key, corp_code, stock_code)
    disclosures = _collect_disclosures(session, api_key, corp_code, asof)
    financials = _collect_financials(session, api_key, corp_code, asof)
    financials = _apply_company_financial_profile(financials, company)
    market = _collect_market(session, stock_code)
    periodic = _collect_periodic_documents(session, api_key, disclosures)
    research = _collect_research(session, stock_code)
    status, issues = _parity_status(company, disclosures, financials, market, periodic)

    return {
        "status": status,
        "issues": issues,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "corp_code": corp_code,
        "generated_at": generated_at.isoformat(),
        "corp_map_manifest": corp_map_manifest,
        "company": company,
        "disclosures": disclosures,
        "financials": financials,
        "market": market,
        "periodic_documents": periodic,
        "research": research,
    }
