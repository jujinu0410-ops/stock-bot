# -*- coding: utf-8 -*-
"""Read-only post-processing preview for YouTube Candidate Watch dry-run JSON.

It can optionally add market regime from Kiwoom ka10066, then renders plain-text
mail previews and evaluates the V1 mail-worthiness rule.  It never sends email,
never writes Sheets/portfolio state, and never places orders.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, Optional

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import KIWOOM_APP_KEY, KIWOOM_APP_SECRET
from scripts.youtube_candidate_registered_ip_validated_dry_run import (
    _Retry429Session,
    _rate_limit_events,
)
from src.analysis.youtube_candidate_review import (
    KiwoomAfterCloseMarketRegimeReader,
    MarketRegimeSnapshot,
    decide_notification,
    market_code_from_market_type,
    render_candidate_preview,
)

KIWOOM_BASE_URL = "https://api.kiwoom.com"
DEFAULT_SOURCE_DB = Path(r"C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system\data\stock_system.db")


class PreviewError(RuntimeError):
    pass


def get_token(session: requests.Session) -> str:
    key = str(os.getenv("KIWOOM_APP_KEY") or KIWOOM_APP_KEY or "").strip()
    secret = str(os.getenv("KIWOOM_APP_SECRET") or KIWOOM_APP_SECRET or "").strip()
    if not key or key == "YOUR_KIWOOM_APP_KEY_HERE" or not secret or secret == "YOUR_KIWOOM_APP_SECRET_HERE":
        raise PreviewError("KIWOOM_CREDENTIALS_MISSING")
    response = session.post(
        f"{KIWOOM_BASE_URL}/oauth2/token",
        headers={"content-type": "application/json"},
        json={"grant_type": "client_credentials", "appkey": key, "secretkey": secret},
        timeout=10,
    )
    if response.status_code != 200:
        raise PreviewError(f"KIWOOM_TOKEN_HTTP_{response.status_code}")
    payload = response.json()
    token = payload.get("token") or payload.get("access_token")
    if not token or str(payload.get("return_code", "0")) not in ("0", "None"):
        raise PreviewError(f"KIWOOM_TOKEN_ERROR: {payload.get('return_code')} {payload.get('return_msg')}")
    return str(token).strip()


def latest_json(log_dir: Path) -> Path:
    rows = sorted(log_dir.glob("youtube_candidate_registered_ip_dry_run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not rows:
        raise PreviewError(f"NO_DRY_RUN_JSON_IN_{log_dir}")
    return rows[0]


def market_types_ro(db_path: Path, tickers: list[str]) -> Dict[str, Optional[str]]:
    if not db_path.exists():
        return {t: None for t in tickers}
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        out: Dict[str, Optional[str]] = {t: None for t in tickers}
        for ticker in tickers:
            row = conn.execute("SELECT market_type FROM stock_info WHERE stock_code=?", (ticker,)).fetchone()
            if row is not None:
                out[ticker] = row["market_type"]
        return out
    finally:
        conn.close()


def snapshot_dict(x: MarketRegimeSnapshot) -> Dict[str, Any]:
    return {
        "market": x.market,
        "market_code": x.market_code,
        "status": x.status,
        "foreign_amount": x.foreign_amount,
        "institution_amount": x.institution_amount,
        "program_amount": x.program_amount,
        "row_count": x.row_count,
        "complete": x.complete,
        "source": x.source,
        "reason": x.reason,
    }


def run(input_json: Path, db_path: Path, with_market: bool) -> Dict[str, Any]:
    data = json.loads(input_json.read_text(encoding="utf-8"))
    candidates = data.get("candidates") or []
    interesting = [c for c in candidates if (c.get("final") or {}).get("signal") not in (None, "NO_ALERT")]
    types = market_types_ro(db_path, [str(c.get("ticker")) for c in interesting])

    market_cache: Dict[str, MarketRegimeSnapshot] = {}
    market_errors: Dict[str, str] = {}
    token_status = "NOT_REQUESTED"
    _rate_limit_events.clear()

    if with_market and interesting:
        session = _Retry429Session(requests.Session())
        try:
            token = get_token(session)
            token_status = "AVAILABLE_REGISTERED_IP"
            reader = KiwoomAfterCloseMarketRegimeReader(token, session)
            for c in interesting:
                ticker = str(c.get("ticker"))
                mapped = market_code_from_market_type(types.get(ticker))
                if mapped is None:
                    market_errors[ticker] = f"UNSUPPORTED_MARKET_TYPE:{types.get(ticker)}"
                    continue
                market, code = mapped
                if market not in market_cache:
                    try:
                        market_cache[market] = reader.fetch(market, code)
                    except Exception as exc:
                        market_errors[market] = str(exc)
        except Exception as exc:
            token_status = f"UNAVAILABLE:{exc}"

    reviews = []
    for c in interesting:
        ticker = str(c.get("ticker"))
        mapped = market_code_from_market_type(types.get(ticker))
        market = market_cache.get(mapped[0]) if mapped else None
        signal = str((c.get("final") or {}).get("signal") or "NO_ALERT")
        notify = decide_notification(signal)
        reviews.append({
            "ticker": ticker,
            "name": c.get("name"),
            "signal": signal,
            "flow_status": (c.get("flow") or {}).get("status"),
            "market_type": types.get(ticker),
            "market_regime": snapshot_dict(market) if market else None,
            "market_error": market_errors.get((mapped or (ticker,))[0]) or market_errors.get(ticker),
            "would_send_by_signal_policy": notify.would_send,
            "notification_reason": notify.reason,
            "actual_send_action": False,
            "preview": render_candidate_preview(c, market),
        })

    return {
        "run_mode": "YOUTUBE_CANDIDATE_REVIEW_PREVIEW_READ_ONLY",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_json": str(input_json),
        "with_market_regime": with_market,
        "kiwoom_token_status": token_status,
        "kiwoom_http_429_retry_count": len(_rate_limit_events),
        "kiwoom_http_429_retry_wait_seconds": sum(
            float(x.get("delay_seconds") or 0) for x in _rate_limit_events
        ),
        "review_count": len(reviews),
        "reviews": reviews,
        "safety": {
            "sheet_write": False,
            "email_send": False,
            "portfolio_mutation": False,
            "order_api": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json", type=Path, default=None)
    parser.add_argument("--log-dir", type=Path, default=BASE_DIR / "logs")
    parser.add_argument("--db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--with-market", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--text-out", type=Path, default=None)
    args = parser.parse_args()

    input_json = args.input_json or latest_json(args.log_dir)
    result = run(input_json, args.db, args.with_market)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    if args.text_out:
        args.text_out.parent.mkdir(parents=True, exist_ok=True)
        text = "\n\n".join(r["preview"] for r in result["reviews"]) or "NO REVIEW SIGNALS"
        args.text_out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
