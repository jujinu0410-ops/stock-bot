# -*- coding: utf-8 -*-
"""Read-only Naver investor-flow fallback for validation only.

Naver Finance exposes daily institution/foreign *net volume*, not exact executed
net-buy amount.  For the GitHub dry-run (where Kiwoom REST is blocked by fixed-IP
policy), this adapter estimates daily amounts as::

    estimated_net_amount = daily_net_volume * close_price

and estimated turnover as::

    estimated_turnover = daily_volume * close_price

Those estimates are sufficient to validate direction/classification plumbing but
must never be presented as exact Kiwoom/official KRX executed amounts.
"""
from __future__ import annotations

from datetime import date
from io import StringIO
import re
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests


BASE_URL = "https://finance.naver.com/item/frgn.naver"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


class NaverFlowError(RuntimeError):
    pass


def _num(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().replace(",", "").replace("+", "")
    if not s or s.lower() == "nan":
        return None
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s in {"-", ".", "-."}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _flatten_col(col: Any) -> str:
    if isinstance(col, tuple):
        parts = [str(x).strip() for x in col if str(x).strip() and "Unnamed" not in str(x)]
        return " ".join(parts)
    return str(col).strip()


def _find_col(columns: List[str], *needles: str) -> Optional[str]:
    for c in columns:
        compact = re.sub(r"\s+", "", c)
        if all(re.sub(r"\s+", "", n) in compact for n in needles):
            return c
    return None


def _extract_table(html: str) -> pd.DataFrame:
    try:
        # pandas may otherwise pick the html5lib parser, which is optional and was
        # absent in the real GitHub Actions validation environment.  lxml is an
        # explicit project dependency, so pin the parser for deterministic dry-runs.
        tables = pd.read_html(StringIO(html), flavor="lxml")
    except Exception as exc:
        raise NaverFlowError(f"NAVER_FLOW_HTML_PARSE: {exc}") from exc

    for table in tables:
        table = table.copy()
        table.columns = [_flatten_col(c) for c in table.columns]
        cols = list(table.columns)
        date_col = _find_col(cols, "날짜")
        close_col = _find_col(cols, "종가")
        volume_col = _find_col(cols, "거래량")
        inst_col = _find_col(cols, "기관", "순매매") or _find_col(cols, "기관")
        foreign_col = _find_col(cols, "외국인", "순매매") or _find_col(cols, "외국인")
        if all([date_col, close_col, volume_col, inst_col, foreign_col]):
            out = pd.DataFrame({
                "date": pd.to_datetime(table[date_col], errors="coerce"),
                "close": table[close_col].map(_num),
                "volume": table[volume_col].map(_num),
                "institution_qty": table[inst_col].map(_num),
                "foreign_qty": table[foreign_col].map(_num),
            })
            out = out.dropna(subset=["date", "close", "volume", "institution_qty", "foreign_qty"])
            if not out.empty:
                return out
    raise NaverFlowError("NAVER_FLOW_TABLE_NOT_FOUND")


def fetch_naver_flow(
    code: str,
    as_of: date,
    *,
    pages: int = 2,
    session: Optional[requests.Session] = None,
    exclude_as_of_date: bool = True,
) -> List[Dict[str, Any]]:
    """Return normalized estimated-amount rows for evaluate_flow().

    The current as-of date is excluded by default because investor totals can be
    provisional shortly after the close.  With two pages Naver normally provides
    more than the required 20 completed trading days.
    """
    ticker = re.sub(r"\D", "", str(code or "")).zfill(6)
    if len(ticker) != 6:
        raise NaverFlowError(f"INVALID_TICKER: {code}")
    if pages < 1:
        raise ValueError("pages must be >= 1")

    sess = session or requests.Session()
    sess.headers.update(DEFAULT_HEADERS)
    frames: List[pd.DataFrame] = []
    for page in range(1, pages + 1):
        res = sess.get(BASE_URL, params={"code": ticker, "page": page}, timeout=10)
        if res.status_code != 200:
            raise NaverFlowError(f"NAVER_FLOW_HTTP_{res.status_code}")
        res.encoding = "euc-kr"
        frames.append(_extract_table(res.text))
        if page < pages:
            time.sleep(0.25)

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["date"]).sort_values("date")
    if exclude_as_of_date:
        df = df[df["date"].dt.date < as_of]
    else:
        df = df[df["date"].dt.date <= as_of]

    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        close = float(r["close"])
        volume = float(r["volume"])
        rows.append({
            "dt": pd.Timestamp(r["date"]).strftime("%Y%m%d"),
            "foreign_amount": float(r["foreign_qty"]) * close,
            "institution_amount": float(r["institution_qty"]) * close,
            "turnover_amount": max(0.0, volume * close),
            "foreign_qty": float(r["foreign_qty"]),
            "institution_qty": float(r["institution_qty"]),
            "close": close,
            "amount_basis": "NET_VOLUME_X_CLOSE_ESTIMATE",
        })
    return rows
