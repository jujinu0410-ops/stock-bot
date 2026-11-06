# -*- coding: utf-8 -*-
"""Validation-only Naver PC investor-flow fallback.

Production flow remains Kiwoom ka10059.  This module exists only so a GitHub-hosted
read-only dry run can validate 5d/20d flow classification when Kiwoom rejects the
runner's dynamic IP.

Naver PC exposes daily foreign/institution *net share volume*, not exact executed
net-buy amount.  Therefore monetary values below are estimates:

    estimated_net_amount = net_share_volume * close
    estimated_turnover   = volume * close

Do not present these values as exact Kiwoom/KRX executed amounts and do not use this
module as the production flow source.
"""
from __future__ import annotations

from datetime import date
import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
import requests


PC_FLOW_URL = "https://finance.naver.com/item/frgn.naver"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


class NaverPCFlowError(RuntimeError):
    pass


def _ticker(code: str) -> str:
    raw = str(code or "").strip().upper()
    if raw.startswith("A") and len(raw) == 7:
        raw = raw[1:]
    if not (len(raw) == 6 and raw.isdigit()):
        raise NaverPCFlowError(f"INVALID_TICKER: {code}")
    return raw


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("+", "")
    if not text or text in {"-", ".", "-."}:
        return None
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_naver_pc_flow_html(html: str) -> List[Dict[str, Any]]:
    """Parse Naver's '외국인ㆍ기관 순매매 거래량' table.

    Expected data columns are date, close, change, change-rate, volume,
    institution net volume, foreign net volume, followed by foreign holdings fields.
    Only rows with a valid YYYY.MM.DD date and the required numeric fields are kept.
    """
    soup = BeautifulSoup(str(html or ""), "lxml")
    out: Dict[str, Dict[str, Any]] = {}

    for tr in soup.select("table.type2 tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 7:
            continue
        raw_date = cells[0].strip()
        if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", raw_date):
            continue
        dt = raw_date.replace(".", "")
        close = _num(cells[1])
        volume = _num(cells[4])
        institution_qty = _num(cells[5])
        foreign_qty = _num(cells[6])
        if None in (close, volume, institution_qty, foreign_qty):
            continue
        out[dt] = {
            "dt": dt,
            "close": float(close),
            "volume": max(0.0, float(volume)),
            "institution_qty": float(institution_qty),
            "foreign_qty": float(foreign_qty),
        }

    return [out[k] for k in sorted(out)]


def fetch_naver_pc_flow(
    code: str,
    as_of: date,
    *,
    pages: int = 2,
    session: Optional[Any] = None,
    exclude_as_of_date: bool = True,
) -> List[Dict[str, Any]]:
    """Fetch enough page-based Naver PC rows for a 20-trading-day validation window."""
    ticker = _ticker(code)
    sess = session or requests.Session()
    cutoff = as_of.strftime("%Y%m%d")
    collected: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []

    for page in range(1, max(1, int(pages)) + 1):
        try:
            headers = dict(DEFAULT_HEADERS)
            headers["Referer"] = f"https://finance.naver.com/item/main.naver?code={ticker}"
            response = sess.get(
                PC_FLOW_URL,
                params={"code": ticker, "page": str(page)},
                headers=headers,
                timeout=10,
            )
            if getattr(response, "status_code", None) != 200:
                raise NaverPCFlowError(
                    f"NAVER_PC_FLOW_HTTP_{getattr(response, 'status_code', 'UNKNOWN')}"
                )
            rows = parse_naver_pc_flow_html(str(getattr(response, "text", "")))
            if not rows:
                raise NaverPCFlowError(f"NAVER_PC_FLOW_EMPTY_PAGE_{page}")
            for row in rows:
                collected[row["dt"]] = row
        except Exception as exc:
            errors.append(f"page{page}={exc}")
            continue

    result: List[Dict[str, Any]] = []
    for dt in sorted(collected):
        if (exclude_as_of_date and dt >= cutoff) or (not exclude_as_of_date and dt > cutoff):
            continue
        row = collected[dt]
        close = float(row["close"])
        volume = float(row["volume"])
        foreign_qty = float(row["foreign_qty"])
        institution_qty = float(row["institution_qty"])
        result.append({
            "dt": dt,
            "foreign_amount": foreign_qty * close,
            "institution_amount": institution_qty * close,
            "turnover_amount": max(0.0, volume * close),
            "foreign_qty": foreign_qty,
            "institution_qty": institution_qty,
            "close": close,
            "amount_basis": "NAVER_PC_NET_VOLUME_X_CLOSE_ESTIMATE",
        })

    if not result:
        raise NaverPCFlowError("NAVER_PC_FLOW_NO_ROWS: " + "; ".join(errors))
    return result
