# -*- coding: utf-8 -*-
"""Read-only Naver investor-flow fallback for validation only.

Production flow remains Kiwoom ka10059. GitHub-hosted Actions cannot call Kiwoom
when the runner IP is not registered, so validation uses Naver's public mobile JSON
investor trend instead of the PC HTML table that is often suppressed for cloud IPs.

Naver exposes foreign/institution *net volume* (shares), not exact executed net-buy
amount. Validation amounts therefore remain explicit estimates::

    estimated_net_amount = daily_net_volume * daily_close
    estimated_turnover = daily_volume * daily_close

Daily close/volume come from Naver fchart, which is also the price source used by the
integrated dry-run. These rows validate direction/classification plumbing only and
must never be presented as exact Kiwoom/KRX executed amounts.
"""
from __future__ import annotations

from datetime import date
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional
import xml.etree.ElementTree as ET

import requests


MOBILE_TREND_URL = "https://m.stock.naver.com/front-api/stock/domestic/trend"
MOBILE_INTEGRATION_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
LEGACY_TREND_URL = "https://m.stock.naver.com/api/item/getTrendList.nhn"
FCHART_URL = "https://fchart.stock.naver.com/sise.nhn"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


class NaverFlowError(RuntimeError):
    pass


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("+", "")
    if not s or s.lower() in {"nan", "none", "null", "-"}:
        return None
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s in {"-", ".", "-."}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _ticker(code: str) -> str:
    raw = str(code or "").strip().upper()
    if raw.startswith("A") and len(raw) == 7:
        raw = raw[1:]
    if not (len(raw) == 6 and raw.isdigit()):
        raise NaverFlowError(f"INVALID_TICKER: {code}")
    return raw


def _front_result(payload: Any) -> Any:
    """Unwrap Naver front-api's optional isSuccess/result envelope."""
    if isinstance(payload, dict) and "isSuccess" in payload:
        if payload.get("isSuccess") is True:
            return payload.get("result")
        detail = payload.get("detailCode") or payload.get("code") or "UNKNOWN"
        raise NaverFlowError(f"NAVER_FLOW_FRONT_ERROR_{detail}")
    return payload


def _deal_rows(payload: Any) -> List[Mapping[str, Any]]:
    result = _front_result(payload)
    if isinstance(result, dict):
        rows = result.get("dealTrendInfos")
    elif isinstance(result, list):
        rows = result
    else:
        rows = None
    if not isinstance(rows, list):
        keys = sorted(result.keys()) if isinstance(result, dict) else [type(result).__name__]
        raise NaverFlowError(f"NAVER_FLOW_JSON_SCHEMA: keys={keys[:20]}")
    return [r for r in rows if isinstance(r, Mapping)]


def _legacy_rows(payload: Any) -> List[Mapping[str, Any]]:
    if not isinstance(payload, dict):
        raise NaverFlowError("NAVER_FLOW_LEGACY_SCHEMA")
    result_code = str(payload.get("resultCode") or "success").lower()
    if result_code not in {"success", "0"}:
        raise NaverFlowError(f"NAVER_FLOW_LEGACY_ERROR_{result_code}")
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise NaverFlowError("NAVER_FLOW_LEGACY_RESULT_MISSING")
    return [r for r in rows if isinstance(r, Mapping)]


def _normalize_deal_rows(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize both current camelCase and older snake_case mobile trend rows."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        dt = str(r.get("bizdate") or r.get("localDate") or r.get("date") or "").replace("-", "").replace(".", "").strip()
        if not (len(dt) == 8 and dt.isdigit()):
            continue
        foreign_qty = _num(
            r.get("foreignerPureBuyQuant")
            if r.get("foreignerPureBuyQuant") is not None
            else r.get("frgn_pure_buy_quant")
        )
        institution_qty = _num(
            r.get("organPureBuyQuant")
            if r.get("organPureBuyQuant") is not None
            else r.get("organ_pure_buy_quant")
        )
        close = _num(
            r.get("closePrice")
            if r.get("closePrice") is not None
            else r.get("close_val")
        )
        if foreign_qty is None or institution_qty is None:
            continue
        out[dt] = {
            "dt": dt,
            "foreign_qty": foreign_qty,
            "institution_qty": institution_qty,
            "trend_close": close,
        }
    return [out[k] for k in sorted(out)]


def _fetch_json(session: Any, url: str, *, params: Optional[Dict[str, str]] = None, referer: str) -> Any:
    headers = dict(DEFAULT_HEADERS)
    headers["Referer"] = referer
    res = session.get(url, params=params, headers=headers, timeout=10)
    if getattr(res, "status_code", None) != 200:
        raise NaverFlowError(f"NAVER_FLOW_JSON_HTTP_{getattr(res, 'status_code', 'UNKNOWN')}")
    try:
        return res.json()
    except Exception as exc:
        preview = str(getattr(res, "text", ""))[:160].replace("\n", " ")
        raise NaverFlowError(f"NAVER_FLOW_JSON_DECODE: {preview}") from exc


def _fetch_mobile_trend(session: Any, ticker: str, required_days: int = 20) -> List[Dict[str, Any]]:
    """Fetch enough investor history for the 20-day direction filter.

    The current front/integration endpoints expose only about five recent sessions.
    The trend-list endpoint accepts an explicit size and is therefore attempted first
    for validation. Current endpoints remain as conservative fallbacks/augmenters.
    """
    referer = f"https://m.stock.naver.com/domestic/stock/{ticker}/total"
    collected: List[Mapping[str, Any]] = []
    errors: List[str] = []

    try:
        payload = _fetch_json(
            session,
            LEGACY_TREND_URL,
            params={"code": ticker, "size": str(max(required_days + 5, 30))},
            referer=referer,
        )
        collected.extend(_legacy_rows(payload))
        normalized = _normalize_deal_rows(collected)
        if len(normalized) >= required_days:
            return normalized
    except Exception as exc:
        errors.append(f"trend_list={exc}")

    try:
        payload = _fetch_json(
            session,
            MOBILE_TREND_URL,
            params={"code": ticker},
            referer=referer,
        )
        collected.extend(_deal_rows(payload))
        normalized = _normalize_deal_rows(collected)
        if len(normalized) >= required_days:
            return normalized
    except Exception as exc:
        errors.append(f"front={exc}")

    try:
        payload = _fetch_json(
            session,
            MOBILE_INTEGRATION_URL.format(code=ticker),
            params=None,
            referer=referer,
        )
        collected.extend(_deal_rows(payload))
    except Exception as exc:
        errors.append(f"integration={exc}")

    rows = _normalize_deal_rows(collected)
    if not rows:
        raise NaverFlowError("NAVER_FLOW_JSON_EMPTY: " + "; ".join(errors))
    return rows


def _fetch_fchart(session: Any, ticker: str, count: int = 90) -> Dict[str, Dict[str, float]]:
    res = session.get(
        FCHART_URL,
        params={"symbol": ticker, "timeframe": "day", "count": str(count), "requestType": "0"},
        headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
        timeout=10,
    )
    if getattr(res, "status_code", None) != 200:
        raise NaverFlowError(f"NAVER_FCHART_HTTP_{getattr(res, 'status_code', 'UNKNOWN')}")
    try:
        root = ET.fromstring(str(getattr(res, "text", "")).strip())
    except Exception as exc:
        raise NaverFlowError("NAVER_FCHART_XML_PARSE") from exc

    out: Dict[str, Dict[str, float]] = {}
    for item in root.findall(".//item"):
        parts = str(item.attrib.get("data") or "").split("|")
        if len(parts) < 6:
            continue
        dt = parts[0].strip()
        close = _num(parts[4])
        volume = _num(parts[5])
        if len(dt) == 8 and dt.isdigit() and close is not None and volume is not None:
            out[dt] = {"close": close, "volume": max(0.0, volume)}
    if not out:
        raise NaverFlowError("NAVER_FCHART_EMPTY")
    return out


def fetch_naver_flow(
    code: str,
    as_of: date,
    *,
    pages: int = 2,
    session: Optional[Any] = None,
    exclude_as_of_date: bool = True,
) -> List[Dict[str, Any]]:
    """Return estimated-amount flow rows suitable for evaluate_flow()."""
    del pages
    ticker = _ticker(code)
    sess = session or requests.Session()

    trend = _fetch_mobile_trend(sess, ticker, required_days=20)
    prices = _fetch_fchart(sess, ticker)
    cutoff = as_of.strftime("%Y%m%d")

    rows: List[Dict[str, Any]] = []
    for r in trend:
        dt = r["dt"]
        if (exclude_as_of_date and dt >= cutoff) or (not exclude_as_of_date and dt > cutoff):
            continue
        px = prices.get(dt)
        if not px:
            continue
        close = float(px["close"])
        volume = float(px["volume"])
        rows.append({
            "dt": dt,
            "foreign_amount": float(r["foreign_qty"]) * close,
            "institution_amount": float(r["institution_qty"]) * close,
            "turnover_amount": max(0.0, volume * close),
            "foreign_qty": float(r["foreign_qty"]),
            "institution_qty": float(r["institution_qty"]),
            "close": close,
            "amount_basis": "NAVER_MOBILE_NET_VOLUME_X_FCHART_CLOSE_ESTIMATE",
        })

    rows.sort(key=lambda x: x["dt"])
    if not rows:
        raise NaverFlowError("NAVER_FLOW_NO_JOINED_DAYS")
    return rows
