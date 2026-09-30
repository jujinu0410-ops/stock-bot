# -*- coding: utf-8 -*-
"""Registered-IP read-only dry run for YouTube Candidate Watch V1.

This runner is the intended validation path for exact stock-level investor flow.
It deliberately performs work in this order:

1. read Economic Intelligence candidate mails (BODY.PEEK / Inbox read-only),
2. rebuild the rolling 30-day candidate pool,
3. calculate technical state for every candidate using completed Naver daily bars,
4. request Kiwoom credentials and ka10059 flow ONLY for non-held BUY_CANDIDATE rows,
5. combine technical + exact foreign/institution flow into the final review signal.

The lazy Kiwoom gate is important: WAIT / READY / EARLY_READY / DATA_HOLD candidates
never consume a flow API call.  This runner never writes DB/Sheet state, never sends
mail, and never places an order.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from email import policy
from email.parser import BytesParser
import imaplib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import scripts.youtube_candidate_dry_run as mailmod
from src.analysis.technical_analysis import calculate_wilder_atr
from src.analysis.youtube_candidate_ingest import (
    CandidateParseError,
    ParseResult,
    aggregate_candidate_pool,
)
from src.analysis.youtube_candidate_mail_adapter import parse_and_resolve_mail
from src.analysis.youtube_candidate_signal import (
    FINAL_NO_ALERT,
    FLOW_UNAVAILABLE,
    TECH_BUY_CANDIDATE,
    KiwoomInvestorFlowReader,
    TechnicalInput,
    combine_final_signal,
    evaluate_flow,
    evaluate_technical,
)

KST = ZoneInfo("Asia/Seoul")
DEFAULT_DB_PATH = BASE_DIR / "data" / "stock_system.db"
KIWOOM_BASE_URL = "https://api.kiwoom.com"


class RegisteredIPDryRunError(RuntimeError):
    pass


def should_query_exact_flow(tech_status: str, holding_status: str = "NOT_HELD") -> bool:
    """Exact flow is queried only at the operational 0.5 ATR buy gate."""
    return tech_status == TECH_BUY_CANDIDATE and holding_status != "HELD"


def _is_status_only_subject(subject: str) -> bool:
    upper = (subject or "").upper()
    return "FAILED" in upper or "DEGRADED" in upper or "파이프라인 실행 오류" in (subject or "")


def fetch_candidate_mails(days: int, as_of_date: date) -> List[Dict[str, Any]]:
    """Server-filter Economic Intelligence mail and fetch without changing read state."""
    if not mailmod.GMAIL_USER or not mailmod.GMAIL_APP_PASSWORD:
        raise RegisteredIPDryRunError("GMAIL_CREDENTIALS_MISSING")
    if days <= 0:
        raise ValueError("days must be positive")

    since_date = as_of_date - timedelta(days=days - 1)
    since_arg = since_date.strftime("%d-%b-%Y")
    rows: List[Dict[str, Any]] = []

    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
        imap.login(mailmod.GMAIL_USER, mailmod.GMAIL_APP_PASSWORD)
        typ, _ = imap.select("INBOX", readonly=True)
        if typ != "OK":
            raise RegisteredIPDryRunError("GMAIL_INBOX_READONLY_OPEN_FAILED")
        typ, data = imap.search(None, "SINCE", since_arg, "SUBJECT", '"Intelligence"')
        if typ != "OK":
            raise RegisteredIPDryRunError("GMAIL_FILTERED_SEARCH_FAILED")

        ids = data[0].split() if data and data[0] else []
        for msg_id in ids:
            typ, fetched = imap.fetch(msg_id, "(BODY.PEEK[])")
            if typ != "OK" or not fetched:
                continue
            raw_bytes = None
            for item in fetched:
                if isinstance(item, tuple) and isinstance(item[1], (bytes, bytearray)):
                    raw_bytes = bytes(item[1])
                    break
            if not raw_bytes:
                continue
            msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)
            subject = mailmod._decode_header_value(msg.get("Subject"))
            if not subject.startswith(mailmod.SUBJECT_PREFIX) or _is_status_only_subject(subject):
                continue
            rows.append({
                "imap_id": msg_id.decode("ascii", errors="ignore"),
                "message_id": str(msg.get("Message-ID") or ""),
                "subject": subject,
                "received_date": mailmod._parse_email_date(msg),
                "body": mailmod._extract_message_text(msg),
            })
    return rows


def load_registry_and_holdings_ro(db_path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    path = db_path.resolve()
    if not path.exists():
        raise RegisteredIPDryRunError(f"SQLITE_DB_NOT_FOUND: {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        registry = [dict(r) for r in conn.execute(
            "SELECT stock_code, stock_name, market_type FROM stock_info ORDER BY stock_code"
        ).fetchall()]
        held = [str(r["stock_code"]).zfill(6) for r in conn.execute(
            "SELECT stock_code FROM portfolio_positions WHERE quantity > 0 ORDER BY stock_code"
        ).fetchall()]
        return registry, held
    finally:
        conn.close()


def get_kiwoom_token(session: requests.Session) -> str:
    """Read-only OAuth token request; must run from a Kiwoom-registered IP."""
    app_key = str(os.getenv("KIWOOM_APP_KEY") or "").strip()
    app_secret = str(os.getenv("KIWOOM_APP_SECRET") or "").strip()
    if not app_key or not app_secret:
        raise RegisteredIPDryRunError("KIWOOM_CREDENTIALS_MISSING")
    res = session.post(
        f"{KIWOOM_BASE_URL}/oauth2/token",
        headers={"content-type": "application/json"},
        json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret},
        timeout=10,
    )
    if res.status_code != 200:
        raise RegisteredIPDryRunError(f"KIWOOM_TOKEN_HTTP_{res.status_code}")
    data = res.json()
    token = data.get("token") or data.get("access_token")
    code = str(data.get("return_code", "0"))
    if not token or code not in ("0", "None"):
        msg = str(data.get("return_msg") or "")
        if "8050" in msg or "IP" in msg.upper():
            raise RegisteredIPDryRunError(f"KIWOOM_IP_BLOCKED: {msg}")
        raise RegisteredIPDryRunError(f"KIWOOM_TOKEN_ERROR: {code} {msg}")
    return str(token).strip()


def fetch_naver_daily(code: str, count: int = 100) -> pd.DataFrame:
    """Completed daily price bars for the technical gate only."""
    res = requests.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": code, "timeframe": "day", "count": str(count), "requestType": "0"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=8,
    )
    if res.status_code != 200:
        raise RegisteredIPDryRunError(f"NAVER_FCHART_HTTP_{res.status_code}")
    try:
        root = ET.fromstring(res.text.strip())
    except Exception as exc:
        raise RegisteredIPDryRunError("NAVER_FCHART_XML_PARSE") from exc

    rows: List[Dict[str, Any]] = []
    for item in root.findall(".//item"):
        p = str(item.attrib.get("data") or "").split("|")
        if len(p) < 6:
            continue
        try:
            rows.append({
                "stk_date": p[0],
                "open_price": float(p[1]),
                "high_price": float(p[2]),
                "low_price": float(p[3]),
                "close_price": float(p[4]),
                "volume": float(p[5]),
            })
        except (TypeError, ValueError):
            continue
    if len(rows) < 65:
        raise RegisteredIPDryRunError(f"NAVER_BARS_INSUFFICIENT_{len(rows)}")
    df = pd.DataFrame(rows)
    df["stk_date"] = pd.to_datetime(df["stk_date"], format="%Y%m%d", errors="coerce")
    return (
        df.dropna(subset=["stk_date"])
        .sort_values("stk_date")
        .drop_duplicates("stk_date")
        .reset_index(drop=True)
    )


def _rsi14(close: pd.Series) -> pd.Series:
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out = 100 - 100 / (1 + avg_gain / avg_loss.replace(0, float("nan")))
    out = out.where(avg_loss != 0, 100.0)
    return out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)


def technical_input(df: pd.DataFrame, as_of: date) -> Tuple[TechnicalInput, Dict[str, Any]]:
    x = df.copy()
    x["ma20"] = x["close_price"].rolling(20).mean()
    x["ma60"] = x["close_price"].rolling(60).mean()
    x["atr14"] = calculate_wilder_atr(x, 14)
    x["rsi14"] = _rsi14(x["close_price"])
    last = x.iloc[-1]
    needed = [last["ma20"], last["ma60"], last["atr14"], last["rsi14"]]
    if any(pd.isna(v) for v in needed):
        raise RegisteredIPDryRunError("TECHNICAL_NAN")

    latest = pd.Timestamp(last["stk_date"]).date()
    stale_days = (as_of - latest).days
    recent = x.tail(5).copy()
    recent["pullback_line"] = recent["ma20"] + 0.3 * recent["atr14"]
    mask = (recent["low_price"] <= recent["pullback_line"]) & (recent["close_price"] >= recent["ma60"])
    pullback_ready = bool(mask.any())
    if pullback_ready:
        first_idx = recent.index[mask][0]
        reference_low = float(x.loc[first_idx:, "low_price"].min())
        pullback_date = pd.Timestamp(x.loc[first_idx, "stk_date"]).date().isoformat()
    else:
        reference_low = float(recent["low_price"].min())
        pullback_date = None

    ti = TechnicalInput(
        close=float(last["close_price"]),
        current_price=float(last["close_price"]),
        reference_low=reference_low,
        atr14=float(last["atr14"]),
        ma20=float(last["ma20"]),
        ma60=float(last["ma60"]),
        ma20_slope_5d=float(last["ma20"] - x.iloc[-6]["ma20"]),
        rsi14=float(last["rsi14"]),
        pullback_ready=pullback_ready,
        data_valid=(0 <= stale_days <= 4),
        suspended=False,
    )
    meta = {
        "price_mode": "DAILY_CLOSE_DRY_RUN",
        "price_as_of_date": latest.isoformat(),
        "stale_days": stale_days,
        "pullback_date": pullback_date,
    }
    return ti, meta


def _tech_dict(tech: Any, ti: TechnicalInput, meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **meta,
        "status": tech.status,
        "close": ti.close,
        "current_price": ti.current_price,
        "reference_low": ti.reference_low,
        "atr14": ti.atr14,
        "ma20": ti.ma20,
        "ma60": ti.ma60,
        "ma20_slope_5d": ti.ma20_slope_5d,
        "rsi14": ti.rsi14,
        "pullback_ready": ti.pullback_ready,
        "rebound_atr": tech.rebound_atr,
        "early_trigger_04": tech.early_trigger_04,
        "buy_trigger_05": tech.buy_trigger_05,
        "confirm_trigger_06": tech.confirm_trigger_06,
        "confirmed_06": tech.confirmed_06,
        "overheat": tech.overheat,
        "reason": tech.reason,
    }


def _flow_dict(flow: Any, query_status: str, error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "source": "KIWOOM_KA10059_EXACT" if query_status == "QUERIED" else None,
        "query_status": query_status,
        "status": flow.status if flow is not None else None,
        "foreign_5d": flow.foreign_5d if flow is not None else None,
        "institution_5d": flow.institution_5d if flow is not None else None,
        "combined_5d": flow.combined_5d if flow is not None else None,
        "combined_20d": flow.combined_20d if flow is not None else None,
        "avg_turnover_20d": flow.avg_turnover_20d if flow is not None else None,
        "flow_strength_5d": flow.flow_strength_5d if flow is not None else None,
        "direction_20d": flow.direction_20d if flow is not None else None,
        "recent_buy_turn": flow.recent_buy_turn if flow is not None else None,
        "reason": flow.reason if flow is not None else None,
        "error": error,
    }


def _parse_pool(days: int, as_of: date, registry: List[Dict[str, Any]], held: List[str]):
    mails = fetch_candidate_mails(days, as_of)
    parsed: List[ParseResult] = []
    failures: List[Dict[str, str]] = []
    unresolved: List[Dict[str, str]] = []
    for mail in mails:
        fallback = mail.get("received_date") or as_of
        try:
            result = parse_and_resolve_mail(mail["subject"], mail["body"], registry, fallback)
            parsed.append(result)
            unresolved.extend({
                "report_date": result.report_date.isoformat(),
                "subject": result.source_subject,
                "raw_text": item.raw_text,
                "reason": item.reason,
            } for item in result.unresolved)
        except CandidateParseError as exc:
            failures.append({
                "subject": mail["subject"],
                "received_date": fallback.isoformat(),
                "reason": str(exc),
            })
    pool = aggregate_candidate_pool(parsed, as_of, held_tickers=held, ttl_days=30)
    return mails, parsed, failures, unresolved, pool


def run(days: int, as_of: date, db_path: Path) -> Dict[str, Any]:
    registry, held = load_registry_and_holdings_ro(db_path)
    mails, parsed, failures, unresolved, pool = _parse_pool(days, as_of, registry, held)

    candidate_rows: List[Dict[str, Any]] = []
    tech_counts: Counter = Counter()
    final_counts: Counter = Counter()
    technical_errors: List[Dict[str, str]] = []
    exact_targets: List[Tuple[int, Any, Any]] = []

    for candidate in pool:
        row: Dict[str, Any] = {
            "ticker": candidate.ticker,
            "name": candidate.name,
            "first_seen_date": candidate.first_seen_date.isoformat(),
            "last_seen_date": candidate.last_seen_date.isoformat(),
            "mention_count_30d": candidate.mention_count_30d,
            "expires_at": candidate.expires_at.isoformat(),
            "holding_status": candidate.holding_status,
            "candidate_status": candidate.candidate_status,
            "source_subject_latest": candidate.source_subject_latest,
        }
        try:
            daily = fetch_naver_daily(candidate.ticker)
            ti, meta = technical_input(daily, as_of)
            tech = evaluate_technical(ti)
            row["technical"] = _tech_dict(tech, ti, meta)
            tech_counts[tech.status] += 1
            if should_query_exact_flow(tech.status, candidate.holding_status):
                row["flow"] = _flow_dict(None, "PENDING_EXACT_FLOW")
                row["final"] = None
                exact_targets.append((len(candidate_rows), candidate, tech))
            else:
                row["flow"] = _flow_dict(None, "NOT_QUERIED_TECH_GATE")
                row["final"] = {
                    "signal": FINAL_NO_ALERT,
                    "tech_status": tech.status,
                    "flow_status": None,
                    "reason": (
                        "held candidate; new-buy flow query suppressed"
                        if candidate.holding_status == "HELD" and tech.status == TECH_BUY_CANDIDATE
                        else f"technical state is {tech.status}; exact flow not required"
                    ),
                }
                final_counts[FINAL_NO_ALERT] += 1
        except Exception as exc:
            reason = str(exc)
            row["technical"] = {"status": "DATA_HOLD", "reason": reason}
            row["flow"] = _flow_dict(None, "NOT_QUERIED_TECH_DATA_HOLD")
            row["final"] = {
                "signal": FINAL_NO_ALERT,
                "tech_status": "DATA_HOLD",
                "flow_status": None,
                "reason": "technical/source data unavailable; candidate isolated",
            }
            tech_counts["DATA_HOLD"] += 1
            final_counts[FINAL_NO_ALERT] += 1
            technical_errors.append({"ticker": candidate.ticker, "name": candidate.name, "reason": reason})
        candidate_rows.append(row)

    session = requests.Session()
    token: Optional[str] = None
    token_status = "NOT_REQUESTED_NO_BUY_CANDIDATE"
    token_error: Optional[str] = None
    exact_query_count = 0
    exact_success_count = 0

    if exact_targets:
        try:
            token = get_kiwoom_token(session)
            token_status = "AVAILABLE_REGISTERED_IP"
        except Exception as exc:
            token_error = str(exc)
            token_status = "IP_BLOCKED" if "KIWOOM_IP_BLOCKED" in token_error else "UNAVAILABLE"

    reader = KiwoomInvestorFlowReader(token, session=session) if token else None
    for row_index, candidate, tech in exact_targets:
        row = candidate_rows[row_index]
        if reader is None:
            flow = evaluate_flow([], source_verified=False)
            row["flow"] = _flow_dict(flow, "EXACT_FLOW_UNAVAILABLE", token_error)
        else:
            exact_query_count += 1
            try:
                flow_days = reader.fetch_stock_flow(candidate.ticker, as_of, max_pages=10)
                flow = evaluate_flow(flow_days, source_verified=True, short_days=5, long_days=20)
                row["flow"] = _flow_dict(flow, "QUERIED")
                if flow.status != FLOW_UNAVAILABLE:
                    exact_success_count += 1
            except Exception as exc:
                flow = evaluate_flow([], source_verified=False)
                row["flow"] = _flow_dict(flow, "EXACT_FLOW_ERROR", str(exc))
        final = combine_final_signal(tech, flow)
        row["final"] = {
            "signal": final.final_signal,
            "tech_status": final.tech_status,
            "flow_status": final.flow_status,
            "market_regime": final.market_regime,
            "reason": final.reason,
        }
        final_counts[final.final_signal] += 1

    exact_required = len(exact_targets)
    exact_gap = exact_success_count < exact_required
    if failures or unresolved:
        data_quality = "DATA_REVIEW_PARSE"
    elif exact_required and token_status == "IP_BLOCKED":
        data_quality = "DATA_REVIEW_KIWOOM_IP_BLOCKED"
    elif exact_required and token_status == "UNAVAILABLE":
        data_quality = "DATA_REVIEW_KIWOOM_UNAVAILABLE"
    elif exact_gap:
        data_quality = "DATA_REVIEW_EXACT_FLOW_GAP"
    elif technical_errors:
        data_quality = "PARTIAL_REVIEW_ISOLATED_CANDIDATE_DATA"
    else:
        data_quality = "VALID"

    alert_readiness = "READY" if not exact_gap and not failures and not unresolved else "BLOCKED"
    return {
        "run_mode": "REGISTERED_IP_EXACT_FLOW_DRY_RUN_READ_ONLY",
        "as_of_date": as_of.isoformat(),
        "lookback_days_fetched": days,
        "candidate_ttl_days": 30,
        "data_quality": data_quality,
        "alert_readiness": alert_readiness,
        "mail_count": len(mails),
        "parsed_mail_count": len(parsed),
        "parse_failure_count": len(failures),
        "unresolved_count": len(unresolved),
        "candidate_count": len(pool),
        "held_candidate_count": sum(1 for x in pool if x.holding_status == "HELD"),
        "technical_counts": dict(tech_counts),
        "final_counts": dict(final_counts),
        "technical_error_count": len(technical_errors),
        "technical_errors": technical_errors,
        "exact_flow_required_count": exact_required,
        "exact_flow_query_count": exact_query_count,
        "exact_flow_success_count": exact_success_count,
        "kiwoom_token_status": token_status,
        "kiwoom_token_error": token_error,
        "parse_failures": failures,
        "unresolved": unresolved,
        "candidates": candidate_rows,
        "safety": {
            "gmail_readonly_body_peek": True,
            "sqlite_mode_ro_query_only": True,
            "price_source_get_only": True,
            "kiwoom_exact_flow_read_only": True,
            "sheet_write": False,
            "email_send": False,
            "portfolio_mutation": False,
            "order_api": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube Candidate Watch registered-IP exact-flow dry run")
    parser.add_argument("--days", type=int, default=35)
    parser.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD; default today KST")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(KST).date()
    result = run(args.days, as_of, args.db)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")

    return 0 if result["alert_readiness"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
