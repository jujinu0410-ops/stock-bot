# -*- coding: utf-8 -*-
"""Integrated read-only dry run for YouTube Candidate Watch V1.

No DB/Sheet/mail/order writes. Uses Gmail READ-ONLY + BODY.PEEK, SQLite mode=ro,
Kiwoom ka10099/ka10059 query APIs, and Naver daily OHLCV GET only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.youtube_candidate_dry_run import fetch_intelligence_mails
from src.analysis.technical_analysis import calculate_wilder_atr
from src.analysis.youtube_candidate_ingest import (
    CandidateParseError, ParseResult, aggregate_candidate_pool, parse_and_resolve_mail,
)
from src.analysis.youtube_candidate_signal import (
    FLOW_UNAVAILABLE, KiwoomInvestorFlowReader, TechnicalInput,
    combine_final_signal, evaluate_flow, evaluate_technical,
)

KST = ZoneInfo("Asia/Seoul")
DEFAULT_DB_PATH = BASE_DIR / "data" / "stock_system.db"
KIWOOM_BASE_URL = "https://api.kiwoom.com"


class DryRunError(RuntimeError):
    pass


def load_registry_and_holdings_ro(db_path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    path = db_path.resolve()
    if not path.exists():
        raise DryRunError(f"SQLite DB not found: {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        stocks = [dict(r) for r in conn.execute(
            "SELECT stock_code, stock_name, market_type FROM stock_info ORDER BY stock_code"
        ).fetchall()]
        held = [str(r["stock_code"]).zfill(6) for r in conn.execute(
            "SELECT stock_code FROM portfolio_positions WHERE quantity > 0 ORDER BY stock_code"
        ).fetchall()]
        return stocks, held
    finally:
        conn.close()


def get_kiwoom_token(session: requests.Session) -> str:
    app_key = os.getenv("KIWOOM_APP_KEY", "").strip()
    app_secret = os.getenv("KIWOOM_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        raise DryRunError("KIWOOM_APP_KEY/KIWOOM_APP_SECRET are not configured")
    res = session.post(
        f"{KIWOOM_BASE_URL}/oauth2/token",
        headers={"content-type": "application/json"},
        json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret},
        timeout=10,
    )
    if res.status_code != 200:
        raise DryRunError(f"Kiwoom token HTTP {res.status_code}")
    data = res.json()
    token = data.get("token") or data.get("access_token")
    if not token or str(data.get("return_code", "0")) not in ("0", "None"):
        raise DryRunError(f"Kiwoom token error: {data.get('return_code')} {data.get('return_msg')}")
    return str(token).strip()


def _kiwoom_list(session: requests.Session, token: str, api_id: str,
                 body: Dict[str, Any], list_key: str, max_pages: int = 10) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    next_key = ""
    seen = set()
    for page in range(max_pages):
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
        }
        if next_key:
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key
        res = session.post(f"{KIWOOM_BASE_URL}/api/dostk/stkinfo",
                           headers=headers, json=body, timeout=10)
        if res.status_code != 200:
            raise DryRunError(f"{api_id} HTTP {res.status_code}")
        data = res.json()
        if not isinstance(data, dict) or str(data.get("return_code", "0")) not in ("0", "None"):
            raise DryRunError(f"{api_id} error: {data.get('return_code')} {data.get('return_msg')}")
        items = data.get(list_key) or []
        if isinstance(items, list):
            rows.extend(x for x in items if isinstance(x, dict))
        cont = (res.headers or {}).get("cont-yn") or data.get("cont-yn")
        new_key = (res.headers or {}).get("next-key") or data.get("next-key") or ""
        if str(cont).upper() != "Y" or not new_key or new_key in seen:
            break
        seen.add(new_key)
        next_key = str(new_key)
        if page + 1 < max_pages:
            time.sleep(0.22)
    return rows


def fetch_live_master(session: requests.Session, token: str) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for market_code, market_name in [("0", "KOSPI"), ("10", "KOSDAQ"), ("8", "ETF")]:
        for item in _kiwoom_list(session, token, "ka10099", {"mrkt_tp": market_code}, "list"):
            raw_code = str(item.get("code") or "").replace("A", "").strip()
            name = str(item.get("name") or "").strip()
            code = raw_code[:6]
            if len(code) == 6 and code.isdigit() and name:
                out[code] = {
                    "stock_code": code,
                    "stock_name": name,
                    "market_type": str(item.get("marketName") or market_name),
                }
        time.sleep(0.25)
    return list(out.values())


def merge_registry(db_rows: List[Dict[str, Any]], live_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for row in db_rows + live_rows:
        code = str(row.get("stock_code") or "").replace("A", "").strip().zfill(6)
        name = str(row.get("stock_name") or "").strip()
        if len(code) == 6 and code.isdigit() and name:
            merged[code] = {"stock_code": code, "stock_name": name,
                            "market_type": row.get("market_type")}
    return list(merged.values())


def fetch_naver_daily(code: str, count: int = 100) -> pd.DataFrame:
    res = requests.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": code, "timeframe": "day", "count": str(count), "requestType": "0"},
        timeout=8,
    )
    if res.status_code != 200:
        raise DryRunError(f"Naver fchart HTTP {res.status_code}")
    root = ET.fromstring(res.text.strip())
    rows = []
    for item in root.findall(".//item"):
        p = (item.attrib.get("data") or "").split("|")
        if len(p) < 6:
            continue
        try:
            rows.append({"stk_date": p[0], "open_price": float(p[1]), "high_price": float(p[2]),
                         "low_price": float(p[3]), "close_price": float(p[4]), "volume": float(p[5])})
        except (TypeError, ValueError):
            pass
    if len(rows) < 65:
        raise DryRunError(f"Naver daily bars insufficient: {len(rows)}")
    df = pd.DataFrame(rows)
    df["stk_date"] = pd.to_datetime(df["stk_date"], format="%Y%m%d", errors="coerce")
    return df.dropna(subset=["stk_date"]).sort_values("stk_date").drop_duplicates("stk_date").reset_index(drop=True)


def rsi14(close: pd.Series) -> pd.Series:
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    ag = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    al = loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rs = ag / al.replace(0, float("nan"))
    out = 100 - 100 / (1 + rs)
    out = out.where(al != 0, 100.0)
    return out.where(~((ag == 0) & (al == 0)), 50.0)


def technical_input(df: pd.DataFrame, as_of: date) -> Tuple[TechnicalInput, Dict[str, Any]]:
    x = df.copy()
    x["ma20"] = x["close_price"].rolling(20).mean()
    x["ma60"] = x["close_price"].rolling(60).mean()
    x["atr14"] = calculate_wilder_atr(x, 14)
    x["rsi14"] = rsi14(x["close_price"])
    last = x.iloc[-1]
    vals = [last["ma20"], last["ma60"], last["atr14"], last["rsi14"]]
    if any(pd.isna(v) for v in vals):
        raise DryRunError("technical indicator contains NaN")
    latest = pd.Timestamp(last["stk_date"]).date()
    stale_days = (as_of - latest).days
    recent = x.tail(5).copy()
    recent["pullback_line"] = recent["ma20"] + 0.3 * recent["atr14"]
    mask = (recent["low_price"] <= recent["pullback_line"]) & (recent["close_price"] >= recent["ma60"])
    ready = bool(mask.any())
    if ready:
        first_idx = recent.index[mask][0]
        reference_low = float(x.loc[first_idx:, "low_price"].min())
        pullback_date = pd.Timestamp(x.loc[first_idx, "stk_date"]).date().isoformat()
    else:
        reference_low = float(recent["low_price"].min())
        pullback_date = None
    ti = TechnicalInput(
        close=float(last["close_price"]), current_price=float(last["close_price"]),
        reference_low=reference_low, atr14=float(last["atr14"]),
        ma20=float(last["ma20"]), ma60=float(last["ma60"]),
        ma20_slope_5d=float(last["ma20"] - x.iloc[-6]["ma20"]),
        rsi14=float(last["rsi14"]), pullback_ready=ready,
        data_valid=(0 <= stale_days <= 4), suspended=False,
    )
    return ti, {"price_mode": "DAILY_CLOSE_DRY_RUN", "price_as_of_date": latest.isoformat(),
                "stale_days": stale_days, "pullback_date": pullback_date}


def flow_dict(f: Any) -> Dict[str, Any]:
    return {"status": f.status, "foreign_5d": f.foreign_5d, "institution_5d": f.institution_5d,
            "combined_5d": f.combined_5d, "combined_20d": f.combined_20d,
            "avg_turnover_20d": f.avg_turnover_20d, "flow_strength_5d": f.flow_strength_5d,
            "direction_20d": f.direction_20d, "recent_buy_turn": f.recent_buy_turn,
            "reason": f.reason}


def run(days: int, as_of: date, db_path: Path) -> Dict[str, Any]:
    db_registry, held = load_registry_and_holdings_ro(db_path)
    session = requests.Session()
    token = get_kiwoom_token(session)
    live_registry = fetch_live_master(session, token)
    registry = merge_registry(db_registry, live_registry)
    mails = fetch_intelligence_mails(days, as_of)

    parsed: List[ParseResult] = []
    failures, unresolved = [], []
    for mail in mails:
        fallback = mail.get("received_date") or as_of
        try:
            pr = parse_and_resolve_mail(mail["subject"], mail["body"], registry, fallback)
            parsed.append(pr)
            unresolved.extend({"report_date": pr.report_date.isoformat(), "subject": pr.source_subject,
                               "raw_text": u.raw_text, "reason": u.reason} for u in pr.unresolved)
        except CandidateParseError as exc:
            failures.append({"subject": mail["subject"], "received_date": fallback.isoformat(), "reason": str(exc)})

    pool = aggregate_candidate_pool(parsed, as_of, held, ttl_days=30)
    flow_reader = KiwoomInvestorFlowReader(token, session=session)
    candidates, data_errors = [], []

    for c in pool:
        base = {"ticker": c.ticker, "name": c.name, "last_seen_date": c.last_seen_date.isoformat(),
                "mention_count_30d": c.mention_count_30d, "holding_status": c.holding_status,
                "eligible_new_buy": c.holding_status != "HELD"}
        try:
            ti, meta = technical_input(fetch_naver_daily(c.ticker), as_of)
            tech = evaluate_technical(ti)
        except Exception as exc:
            data_errors.append({"ticker": c.ticker, "stage": "TECHNICAL", "reason": str(exc)})
            candidates.append({**base, "technical": {"status": "DATA_HOLD", "reason": str(exc)},
                               "flow": {"status": FLOW_UNAVAILABLE}, "market_regime": "UNAVAILABLE",
                               "final_signal": "NO_ALERT"})
            continue
        try:
            frows = flow_reader.fetch_stock_flow(c.ticker, as_of, max_pages=5)
            flow = evaluate_flow(frows, source_verified=True)
            if flow.status == FLOW_UNAVAILABLE:
                data_errors.append({"ticker": c.ticker, "stage": "FLOW", "reason": flow.reason})
        except Exception as exc:
            data_errors.append({"ticker": c.ticker, "stage": "FLOW", "reason": str(exc)})
            flow = evaluate_flow([], source_verified=False)
        final = combine_final_signal(tech, flow)
        candidates.append({
            **base,
            "technical": {"status": tech.status, "close": ti.close, "reference_low": ti.reference_low,
                          "atr14": ti.atr14, "ma20": ti.ma20, "ma60": ti.ma60,
                          "ma20_slope_5d": ti.ma20_slope_5d, "rsi14": ti.rsi14,
                          "pullback_ready": ti.pullback_ready, "rebound_atr": tech.rebound_atr,
                          "early_trigger_04": tech.early_trigger_04, "buy_trigger_05": tech.buy_trigger_05,
                          "confirm_trigger_06": tech.confirm_trigger_06, "confirmed_06": tech.confirmed_06,
                          "overheat": tech.overheat, "reason": tech.reason, **meta},
            "flow": flow_dict(flow), "market_regime": "UNAVAILABLE",
            "final_signal": final.final_signal, "final_reason": final.reason,
        })
        time.sleep(0.28)

    tech_counts = Counter(x.get("technical", {}).get("status", "UNKNOWN") for x in candidates)
    flow_counts = Counter(x.get("flow", {}).get("status", "UNKNOWN") for x in candidates)
    final_counts = Counter(x.get("final_signal", "UNKNOWN") for x in candidates)
    quality = "VALID" if mails and parsed and not failures and not unresolved and not data_errors else "DATA_REVIEW"
    return {
        "run_mode": "INTEGRATED_DRY_RUN_READ_ONLY", "as_of_date": as_of.isoformat(),
        "price_mode": "DAILY_CLOSE_DRY_RUN", "data_quality": quality,
        "lookback_days_fetched": days, "mail_count": len(mails), "parsed_mail_count": len(parsed),
        "parse_failure_count": len(failures), "unresolved_count": len(unresolved),
        "candidate_count": len(pool), "held_candidate_count": sum(c.holding_status == "HELD" for c in pool),
        "registry_count_db": len(db_registry), "registry_count_kiwoom_live": len(live_registry),
        "registry_count_merged": len(registry), "technical_status_counts": dict(tech_counts),
        "flow_status_counts": dict(flow_counts), "final_signal_counts": dict(final_counts),
        "parse_failures": failures, "unresolved": unresolved, "data_errors": data_errors,
        "candidates": candidates,
        "safety": {"gmail_readonly": True, "gmail_body_peek": True, "sqlite_mode_ro": True,
                   "sqlite_query_only": True, "naver_get_only": True,
                   "kiwoom_query_only": ["oauth2/token", "ka10099", "ka10059"],
                   "sheet_write": False, "email_send": False, "portfolio_mutation": False,
                   "order_api": False, "market_regime_live_connected": False},
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=35)
    p.add_argument("--as-of", default=None)
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument("--json-out", type=Path, default=None)
    args = p.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(KST).date()
    result = run(args.days, as_of, args.db)
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
