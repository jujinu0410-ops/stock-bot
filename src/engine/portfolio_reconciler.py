# -*- coding: utf-8 -*-
"""
portfolio_reconciler.py

Reconciles real-time account holdings (LIVE HOLDINGS) with strategy configuration
(STRATEGY CONFIG) and synchronizes to Google Sheet MONITOR_CONFIG and local/GCS snapshots.

Separation of Concerns:
- LIVE HOLDINGS: Actual brokerage balance (ticker, qty, avg_price, current_price, status)
- STRATEGY CONFIG: Strategy targets (stop_price, target1, target2, target_source, target_base_date)

Post-Reconciliation Strategy Validation:
- Detects position changes (qty_changed, avg_price_changed, new_position, reopened_position)
- Validates stop_price and targets against current position and market price
- Produces strategy_status (NORMAL, REVIEW_REQUIRED, STOP_BREACHED, TARGET_REACHED, UNCONFIGURED, SUSPENDED)
- CRITICAL: NEVER automatically alters or recalculates stop_price or targets (Validation Only).
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.utils.logger import logger

KST = timezone(timedelta(hours=9))

DEFAULT_SPREADSHEET_ID = "15WgSe4yOSBqSt6YTRQEETD_Hs6J1WL2Hop9RlzFCGkw"
SHEET_CONFIG_TAB = "MONITOR_CONFIG"

# Known KOSDAQ tickers
KOSDAQ_CODES = {
    "013030", "047770", "086450", "086520", "108490", "140670", "196170",
    "206650", "214450", "219550", "234920", "241520", "348340", "028300", "277810"
}


def get_current_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def get_current_date_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def parse_price(val: Any) -> Optional[float]:
    """Safely extracts numeric price from string or number."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if val > 0 else None
    if isinstance(val, str):
        v = val.strip().replace(",", "").replace("원", "")
        if not v or any(k in v.upper() for k in ["HOLD", "N/A", "NONE", "거래정지", "-"]):
            return None
        try:
            num = float(v)
            return num if num > 0 else None
        except ValueError:
            return None
    return None


def validate_position_strategy(
    strat: Dict[str, Any],
    live: Dict[str, Any],
    prev_qty: int,
    prev_avg: float,
) -> Dict[str, Any]:
    """
    Validates existing STRATEGY CONFIG against current LIVE position.
    
    CRITICAL: NEVER modifies stop_price or targets. Validation only!
    
    Returns validation bundle:
    - strategy_revalidation_required: bool
    - revalidation_reasons: List[str]
    - stop_status: str (ACTIVE, BREACHED_OR_STALE, PROFIT_PROTECTION_OK, MISSING, SUSPENDED_HOLD)
    - target1_status: str (ACTIVE, REACHED_OR_STALE, INVALID_OR_REVIEW, NONE)
    - target2_status: str (ACTIVE, REACHED_OR_STALE, INVALID_OR_REVIEW, NONE)
    - strategy_status: str (NORMAL, REVIEW_REQUIRED, STOP_BREACHED, TARGET_REACHED, UNCONFIGURED, SUSPENDED)
    - strategy_warning: str
    """
    code = str(live.get("stock_code") or strat.get("ticker") or "").strip().zfill(6)
    new_qty = int(live.get("quantity") or 0)
    new_avg = float(live.get("avg_buy_price") or 0.0)
    cur_p = float(live.get("current_price") or live.get("raw_balance_price") or 0.0)
    if cur_p <= 0:
        cur_p = parse_price(strat.get("ref_close")) or new_avg

    # 1. Trigger detection
    revalidation_reasons = []
    is_new = strat.get("target_source") == "UNCONFIGURED" or not strat.get("ticker")
    is_reopened = (prev_qty == 0 and new_qty > 0)
    qty_changed = (prev_qty != new_qty)
    avg_price_changed = (abs(prev_avg - new_avg) > 1e-4)

    if is_new:
        revalidation_reasons.append("NEW_POSITION")
    elif is_reopened:
        revalidation_reasons.append("REOPENED_POSITION")
    else:
        if qty_changed:
            revalidation_reasons.append("QTY_CHANGED")
        if avg_price_changed:
            revalidation_reasons.append("AVG_PRICE_CHANGED")

    revalidation_required = len(revalidation_reasons) > 0

    # 2. Stop price evaluation
    stop_raw = strat.get("stop_price")
    status_raw = str(strat.get("status") or "").upper()
    is_suspended = "SUSPENDED" in status_raw or code == "234920"

    stop_num = parse_price(stop_raw)

    if is_suspended:
        stop_status = "SUSPENDED_HOLD"
    elif stop_num is None or stop_num <= 0:
        stop_status = "MISSING"
    elif stop_num >= cur_p:
        stop_status = "BREACHED_OR_STALE"
    elif stop_num > new_avg and cur_p > stop_num:
        stop_status = "PROFIT_PROTECTION_OK"
    elif stop_num < cur_p:
        stop_status = "ACTIVE"
    else:
        stop_status = "ACTIVE"

    # 3. Target 1 evaluation
    t1_num = parse_price(strat.get("target1"))
    if t1_num is None or t1_num <= 0:
        t1_status = "NONE"
    elif t1_num <= new_avg:
        t1_status = "INVALID_OR_REVIEW"
    elif t1_num <= cur_p:
        t1_status = "REACHED_OR_STALE"
    else:
        t1_status = "ACTIVE"

    # 4. Target 2 evaluation
    t2_num = parse_price(strat.get("target2"))
    if t2_num is None or t2_num <= 0:
        t2_status = "NONE"
    elif t2_num <= new_avg:
        t2_status = "INVALID_OR_REVIEW"
    elif t2_num <= cur_p:
        t2_status = "REACHED_OR_STALE"
    else:
        t2_status = "ACTIVE"

    # 5. Overall strategy_status and warning message
    strategy_warning = ""
    if is_suspended:
        strategy_status = "SUSPENDED"
        strategy_warning = "거래정지 보류"
    elif strat.get("target_source") == "UNCONFIGURED" or stop_status == "MISSING":
        strategy_status = "UNCONFIGURED"
        strategy_warning = "전략 미설정 (목표/손절 미지정)"
    elif stop_status == "BREACHED_OR_STALE":
        if revalidation_required or (stop_num is not None and stop_num > new_avg):
            strategy_status = "REVIEW_REQUIRED"
            if avg_price_changed:
                strategy_warning = "추가매수로 평단 변경 + 현재가가 기존 손절선 하회"
            else:
                strategy_warning = "현재가가 기존 손절선 아래 (전략 재검토 필요)"
        else:
            strategy_status = "STOP_BREACHED"
            strategy_warning = "현재가가 손절선 아래"
    elif t1_status == "INVALID_OR_REVIEW" or t2_status == "INVALID_OR_REVIEW":
        strategy_status = "REVIEW_REQUIRED"
        strategy_warning = "목표가가 평단 이하 (전략 재설정 필요)"
    elif t1_status == "REACHED_OR_STALE" or t2_status == "REACHED_OR_STALE":
        strategy_status = "TARGET_REACHED"
        strategy_warning = "목표가 도달"
    else:
        strategy_status = "NORMAL"
        strategy_warning = ""

    return {
        "strategy_revalidation_required": revalidation_required,
        "revalidation_reasons": revalidation_reasons,
        "stop_status": stop_status,
        "target1_status": t1_status,
        "target2_status": t2_status,
        "strategy_status": strategy_status,
        "strategy_warning": strategy_warning,
    }


def compute_atr_metrics(
    atr_val: Optional[Any],
    base_date: Optional[str] = None,
    is_suspended: bool = False,
    is_etf: bool = False,
) -> Dict[str, Any]:
    """
    Computes ATR14 and trailing reference distances (+0.6 ATR buy tracking, -0.4 ATR high trailing).
    
    Rules:
    - Never invent new ATR calculation logic (reuses Wilder ATR14 from stockbot).
    - If atr_val is missing, stale, or <= 0: atr_text = 'ATR -' or 'ATR 자료없음'
    - If is_suspended:
      - If last valid ATR and base_date exist: 'ATR14 {atr_round:,}원 · 기준일 {mm_dd}'
      - Otherwise: 'ATR - (거래정지)'
    - Trailing distances:
      - buy_trailing_dist = int(round(0.6 * atr_round))
      - sell_trailing_dist = int(round(0.4 * atr_round))
      - atr_detail_text = '0.6ATR {buy:,}원 · 0.4ATR {sell:,}원' (or '-' if suspended/missing)
    """
    atr_num = None
    if atr_val is not None:
        try:
            val_f = float(str(atr_val).replace(",", "").replace("원", "").strip())
            if val_f > 0:
                atr_num = val_f
        except Exception:
            atr_num = None

    if is_suspended:
        if atr_num is not None and atr_num > 0:
            atr_round = int(round(atr_num))
            date_part = ""
            if base_date:
                clean_d = str(base_date).replace("-", "").strip()
                if len(clean_d) >= 8:
                    date_part = f" · 기준일 {clean_d[4:6]}/{clean_d[6:8]}"
                else:
                    date_part = f" · 기준일 {base_date}"
            atr_text = f"ATR14 {atr_round:,}원{date_part}"
        else:
            atr_round = None
            atr_text = "ATR - (거래정지)"
        return {
            "atr_14": atr_num,
            "atr_round": atr_round,
            "atr_text": atr_text,
            "buy_trailing_dist": None,
            "sell_trailing_dist": None,
            "atr_detail_text": "-",
            "atr_base_date": base_date or "",
        }

    if atr_num is None or atr_num <= 0:
        return {
            "atr_14": None,
            "atr_round": None,
            "atr_text": "ATR -",
            "buy_trailing_dist": None,
            "sell_trailing_dist": None,
            "atr_detail_text": "-",
            "atr_base_date": base_date or "",
        }

    atr_round = int(round(atr_num))
    buy_dist = int(round(atr_num * 0.6))
    sell_dist = int(round(atr_num * 0.4))
    atr_text = f"ATR14 {atr_round:,}원"
    atr_detail_text = f"0.6ATR {buy_dist:,}원 · 0.4ATR {sell_dist:,}원"

    return {
        "atr_14": atr_num,
        "atr_round": atr_round,
        "atr_text": atr_text,
        "buy_trailing_dist": buy_dist,
        "sell_trailing_dist": sell_dist,
        "atr_detail_text": atr_detail_text,
        "atr_base_date": base_date or "",
    }



def get_google_access_token() -> Optional[str]:
    """Acquires Google OAuth access token for Sheets/Drive API."""
    # 1. Try google.auth default credentials (Cloud Run SA / ADC)
    try:
        import google.auth
        from google.auth.transport.requests import Request
        creds, _ = google.auth.default(scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ])
        creds.refresh(Request())
        if creds.token:
            return creds.token
    except Exception as exc:
        logger.debug(f"[PortfolioReconciler] google.auth token acquisition skipped: {exc}")

    # 2. Try gcloud CLI fallback (local dev environment)
    for cmd in ["gcloud.cmd", "gcloud"]:
        try:
            token = subprocess.check_output(f"{cmd} auth print-access-token", shell=True, text=True, stderr=subprocess.DEVNULL).strip()
            if token:
                return token
        except Exception:
            continue

    return None


class PortfolioReconciler:
    def __init__(
        self,
        spreadsheet_id: Optional[str] = None,
        state_dir: Optional[Path] = None,
        token: Optional[str] = None,
    ):
        self.spreadsheet_id = spreadsheet_id or os.getenv("MONITOR_SPREADSHEET_ID") or DEFAULT_SPREADSHEET_ID
        self.state_dir = Path(state_dir or os.getenv("STOCKBOT_STATE_DIR") or ".").resolve()
        self.token = token

    def _get_token(self) -> str:
        if self.token:
            return self.token
        tok = get_google_access_token()
        if not tok:
            raise RuntimeError("Cannot acquire Google access token for Sheets synchronization")
        return tok

    def validate_live_positions(self, live_positions: Optional[List[Dict[str, Any]]]) -> Tuple[bool, str]:
        """Safety Gate: Verifies that live positions is a non-empty, valid list."""
        if live_positions is None:
            return False, "LIVE_POSITIONS_IS_NONE"
        if not isinstance(live_positions, list):
            return False, f"INVALID_TYPE_{type(live_positions).__name__}"
        if len(live_positions) == 0:
            return False, "EMPTY_LIVE_POSITIONS_REJECTED"

        # Validate entries
        valid_count = 0
        for p in live_positions:
            if not isinstance(p, dict):
                continue
            code = str(p.get("stock_code") or "").strip()
            qty = int(p.get("quantity") or 0)
            avg_p = float(p.get("avg_buy_price") or 0.0)
            if code and qty > 0 and avg_p > 0:
                valid_count += 1

        if valid_count == 0:
            return False, "ZERO_VALID_POSITIONS_FOUND"

        return True, "VALID"

    def read_remote_config(self) -> List[List[Any]]:
        """Reads current MONITOR_CONFIG from Google Sheet."""
        token = self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{SHEET_CONFIG_TAB}!A1:T100"
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code != 200:
            raise RuntimeError(f"Google Sheets read failed ({res.status_code}): {res.text}")
        data = res.json()
        return data.get("values", [])

    def write_remote_config(self, rows: List[List[Any]]) -> int:
        """Writes updated MONITOR_CONFIG rows to Google Sheet."""
        token = self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{SHEET_CONFIG_TAB}!A2:T{len(rows)+1}?valueInputOption=USER_ENTERED"
        body = {"values": rows}
        res = requests.put(url, headers=headers, json=body, timeout=15)
        if res.status_code != 200:
            raise RuntimeError(f"Google Sheets write failed ({res.status_code}): {res.text}")
        updated_cells = res.json().get("updatedCells", 0)
        return updated_cells

    def update_header_if_needed(self) -> None:
        """Ensures headers A1:T1 contain STRATEGY_STATUS, STRATEGY_WARNING, and ATR trailing columns."""
        try:
            token = self._get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{SHEET_CONFIG_TAB}!A1:T1?valueInputOption=USER_ENTERED"
            header_row = [
                "TICKER", "EXCHANGE", "NAME", "QTY", "AVG_PRICE", "REF_CLOSE",
                "STOP_PRICE", "STATUS", "BASE_DATE", "ENABLED", "TARGET1", "TARGET2",
                "TARGET_SOURCE", "TARGET_BASE_DATE", "STRATEGY_STATUS", "STRATEGY_WARNING",
                "ATR14", "BUY_TRAILING_DIST", "SELL_TRAILING_DIST", "ATR_BASE_DATE"
            ]
            requests.put(url, headers=headers, json={"values": [header_row]}, timeout=10)
        except Exception as exc:
            logger.debug(f"[PortfolioReconciler] Header update skipped: {exc}")

    def _resolve_atr_for_position(
        self,
        code: str,
        live: Dict[str, Any],
        strat: Dict[str, Any],
        atr_map: Optional[Dict[str, Any]],
        today_date: str,
        is_suspended: bool,
    ) -> Dict[str, Any]:
        """Resolves ATR14 and computes trailing reference metrics with fallback cascade."""
        atr_candidate = None
        base_date = today_date

        # 1. Check explicit atr_map passed by caller
        if atr_map and code in atr_map:
            m = atr_map[code]
            if isinstance(m, dict):
                atr_candidate = m.get("atr_14") or m.get("current_completed_atr") or m.get("atr")
                base_date = m.get("base_date") or m.get("atr_base_date") or today_date
            elif isinstance(m, (int, float)):
                atr_candidate = m

        # 2. Check live position dict
        if atr_candidate is None:
            atr_candidate = live.get("atr_14") or live.get("current_completed_atr") or live.get("atr")
            if live.get("atr_base_date"):
                base_date = live.get("atr_base_date")

        # 3. Check existing strategy config from Google Sheet
        if atr_candidate is None and strat.get("atr14"):
            atr_candidate = parse_price(strat.get("atr14"))
            if strat.get("atr_base_date"):
                base_date = str(strat.get("atr_base_date")).strip()

        # 4. Check SQLite DB portfolio_positions if available
        if atr_candidate is None:
            try:
                db_path = self.state_dir / "data" / "stock_system.db"
                if not db_path.exists():
                    db_path = Path("data/stock_system.db")
                if db_path.exists():
                    import sqlite3
                    conn = sqlite3.connect(str(db_path))
                    try:
                        c = conn.cursor()
                        c.execute("SELECT current_completed_atr FROM portfolio_positions WHERE stock_code=?", (code,))
                        row_p = c.fetchone()
                        if row_p and row_p[0] and float(row_p[0]) > 0:
                            atr_candidate = float(row_p[0])
                    finally:
                        conn.close()
            except Exception as e_db:
                logger.debug(f"[PortfolioReconciler] DB ATR lookup skipped: {e_db}")

        # 5. For suspended stocks without specific base date, check strat base date
        if is_suspended and (not base_date or base_date == today_date):
            if strat.get("atr_base_date"):
                base_date = str(strat.get("atr_base_date")).strip()
            elif strat.get("base_date"):
                base_date = str(strat.get("base_date")).strip()

        is_etf = code in {"490590", "161510", "371460", "484730", "088500"}
        return compute_atr_metrics(atr_candidate, base_date=base_date, is_suspended=is_suspended, is_etf=is_etf)

    def reconcile(
        self,
        live_positions: List[Dict[str, Any]],
        *,
        atr_map: Optional[Dict[str, Any]] = None,
        asof_kst: Optional[str] = None,
        sync_to_sheet: bool = True,
    ) -> Dict[str, Any]:
        """
        Executes complete reconciliation between live holdings and strategy config,
        followed by post-reconciliation strategy validation and ATR metrics enrichment.
        
        Returns reconciliation result dictionary.
        """
        now_str = asof_kst or get_current_kst_str()
        today_date = now_str.split(" ")[0]

        # 1. Safety validation
        is_valid, reason = self.validate_live_positions(live_positions)
        if not is_valid:
            logger.critical(f"[PortfolioReconciler] 🛑 Safety guard triggered: {reason}. Preserving existing snapshot.")
            failed_res = {
                "synced_at_kst": now_str,
                "source": "KIWOOM_API_KT00018",
                "sync_status": "FAILED",
                "fail_reason": reason,
                "holdings_count": 0,
                "positions": [],
                "reconciliation_log": [f"RECONCILIATION_ABORTED: {reason}"]
            }
            self._save_snapshots(failed_res)
            return failed_res

        # 2. Build live positions lookup
        live_map: Dict[str, Dict[str, Any]] = {}
        for p in live_positions:
            code = str(p.get("stock_code") or "").strip().zfill(6)
            if code and int(p.get("quantity") or 0) > 0:
                live_map[code] = p

        # 3. Read existing sheet rows (Strategy Config)
        existing_rows: List[List[Any]] = []
        if sync_to_sheet:
            try:
                raw_sheet = self.read_remote_config()
                if len(raw_sheet) > 1:
                    existing_rows = raw_sheet[1:]  # skip header
            except Exception as exc:
                logger.warning(f"[PortfolioReconciler] Could not read remote sheet ({exc}). Continuing with local reconciliation.")

        existing_by_code: Dict[str, Dict[str, Any]] = {}
        row_order: List[str] = []

        for row in existing_rows:
            if not row or not str(row[0]).strip():
                continue
            code = str(row[0]).replace("'", "").replace("A", "").strip().zfill(6)
            row_order.append(code)
            existing_by_code[code] = {
                "ticker": code,
                "exchange": row[1] if len(row) > 1 else ("KOSDAQ" if code in KOSDAQ_CODES else "KRX"),
                "name": row[2] if len(row) > 2 else "",
                "qty": int(row[3]) if len(row) > 3 and str(row[3]).isdigit() else 0,
                "avg_price": float(str(row[4]).replace(",", "")) if len(row) > 4 and str(row[4]).strip() else 0.0,
                "ref_close": row[5] if len(row) > 5 else "",
                "stop_price": row[6] if len(row) > 6 else "",
                "status": row[7] if len(row) > 7 else "ACTIVE",
                "base_date": row[8] if len(row) > 8 else today_date,
                "enabled": row[9] if len(row) > 9 else "TRUE",
                "target1": row[10] if len(row) > 10 else "",
                "target2": row[11] if len(row) > 11 else "",
                "target_source": row[12] if len(row) > 12 else "",
                "target_base_date": row[13] if len(row) > 13 else "",
                "strategy_status": row[14] if len(row) > 14 else "NORMAL",
                "strategy_warning": row[15] if len(row) > 15 else "",
                "atr14": row[16] if len(row) > 16 else "",
                "buy_trailing_dist": row[17] if len(row) > 17 else "",
                "sell_trailing_dist": row[18] if len(row) > 18 else "",
                "atr_base_date": row[19] if len(row) > 19 else "",
            }

        reconciliation_log: List[str] = []
        reconciled_rows: List[List[Any]] = []
        final_live_positions: List[Dict[str, Any]] = []

        # 4. Reconcile existing strategy configs against live positions
        seen_codes = set()

        for code in row_order:
            seen_codes.add(code)
            strat = existing_by_code[code]
            name = strat["name"]

            if code not in live_map:
                # CLOSED: Stock in strategy config is not in brokerage account
                prev_qty = strat["qty"]
                strat["qty"] = 0
                strat["status"] = "CLOSED"
                strat["enabled"] = "FALSE"
                strat["strategy_status"] = "CLOSED"
                strat["strategy_warning"] = "매도 완료"
                reconciliation_log.append(f"CLOSED: {code} {name} {prev_qty} -> 0")
                logger.info(f"[Reconciler] CLOSED: {name}({code}) {prev_qty}주 -> 0주 (매도 완료, 감시대시보드 제외)")

                reconciled_rows.append([
                    f"'{strat['ticker']}",
                    strat["exchange"],
                    strat["name"],
                    strat["qty"],
                    int(round(strat["avg_price"])) if isinstance(strat["avg_price"], (int, float)) else strat["avg_price"],
                    strat["ref_close"],
                    strat["stop_price"],
                    strat["status"],
                    strat["base_date"],
                    strat["enabled"],
                    strat["target1"],
                    strat["target2"],
                    strat["target_source"],
                    strat["target_base_date"],
                    strat.get("strategy_status", "NORMAL"),
                    strat.get("strategy_warning", ""),
                    "",  # ATR14 empty for CLOSED
                    "",  # BUY_TRAILING_DIST empty
                    "",  # SELL_TRAILING_DIST empty
                    "",  # ATR_BASE_DATE empty
                ])
            else:
                live = live_map[code]
                prev_qty = strat["qty"]
                prev_avg = strat["avg_price"]
                new_qty = int(live.get("quantity") or 0)
                new_avg = float(live.get("avg_buy_price") or 0.0)
                cur_price = int(live.get("current_price") or live.get("raw_balance_price") or 0)
                live_name = live.get("stock_name") or name

                # Validate strategy without modifying strategy config
                val = validate_position_strategy(strat, live, prev_qty, prev_avg)

                if prev_qty != new_qty or abs(prev_avg - new_avg) > 1e-4:
                    reconciliation_log.append(
                        f"POSITION_CHANGED: {code} {live_name} | qty: {prev_qty} -> {new_qty}, avg: {prev_avg:.0f} -> {new_avg:.0f}, stop: {strat['stop_price']}, cur: {cur_price} | strategy_status: {val['strategy_status']} ({val['strategy_warning']})"
                    )
                    logger.info(
                        f"[Reconciler] POSITION_CHANGED: {live_name}({code}) 수량 {prev_qty}->{new_qty}주, 평단 {prev_avg:.0f}->{new_avg:.0f}원 | 전략: {val['strategy_status']} ({val['strategy_warning']})"
                    )
                    strat["base_date"] = today_date
                else:
                    reconciliation_log.append(
                        f"UNCHANGED: {code} {live_name} qty={new_qty}, avg={new_avg:.0f} | strategy_status: {val['strategy_status']}"
                    )

                is_susp = "SUSPENDED" in str(strat["status"]).upper() or code == "234920"
                strat["qty"] = new_qty
                strat["avg_price"] = new_avg
                strat["name"] = live_name
                strat["status"] = "SUSPENDED_HOLD" if is_susp else "ACTIVE"
                strat["enabled"] = "TRUE"
                strat["strategy_status"] = val["strategy_status"]
                strat["strategy_warning"] = val["strategy_warning"]

                atr_info = self._resolve_atr_for_position(
                    code, live, strat, atr_map, today_date, is_suspended=is_susp
                )

                final_live_positions.append({
                    "ticker": code,
                    "name": live_name,
                    "qty": new_qty,
                    "avg_price": new_avg,
                    "current_price": cur_price,
                    "position_status": "OPEN",
                    "strategy_revalidation_required": val["strategy_revalidation_required"],
                    "revalidation_reasons": val["revalidation_reasons"],
                    "stop_status": val["stop_status"],
                    "target1_status": val["target1_status"],
                    "target2_status": val["target2_status"],
                    "strategy_status": val["strategy_status"],
                    "strategy_warning": val["strategy_warning"],
                    "stop_price": strat["stop_price"],
                    "target1": strat["target1"],
                    "target2": strat["target2"],
                    "atr_14": atr_info["atr_14"],
                    "atr_round": atr_info["atr_round"],
                    "atr_text": atr_info["atr_text"],
                    "buy_trailing_dist": atr_info["buy_trailing_dist"],
                    "sell_trailing_dist": atr_info["sell_trailing_dist"],
                    "atr_detail_text": atr_info["atr_detail_text"],
                    "atr_base_date": atr_info["atr_base_date"],
                    "synced_at_kst": now_str,
                })

                # Format row for MONITOR_CONFIG (prefixed with ' to preserve leading zeros in Sheets)
                reconciled_rows.append([
                    f"'{strat['ticker']}",
                    strat["exchange"],
                    strat["name"],
                    strat["qty"],
                    int(round(strat["avg_price"])) if isinstance(strat["avg_price"], (int, float)) else strat["avg_price"],
                    strat["ref_close"],
                    strat["stop_price"],
                    strat["status"],
                    strat["base_date"],
                    strat["enabled"],
                    strat["target1"],
                    strat["target2"],
                    strat["target_source"],
                    strat["target_base_date"],
                    strat.get("strategy_status", "NORMAL"),
                    strat.get("strategy_warning", ""),
                    atr_info["atr_round"] if atr_info["atr_round"] is not None else "",
                    atr_info["buy_trailing_dist"] if atr_info["buy_trailing_dist"] is not None else "",
                    atr_info["sell_trailing_dist"] if atr_info["sell_trailing_dist"] is not None else "",
                    atr_info["atr_base_date"] if atr_info["atr_round"] is not None else "",
                ])

        # 5. Handle NEW holdings (in account but not in strategy config)
        for code, live in live_map.items():
            if code in seen_codes:
                continue
            name = live.get("stock_name") or code
            qty = int(live.get("quantity") or 0)
            avg_p = float(live.get("avg_buy_price") or 0.0)
            cur_price = int(live.get("current_price") or live.get("raw_balance_price") or 0)
            exchange = "KOSDAQ" if code in KOSDAQ_CODES else "KRX"

            new_strat = {
                "ticker": code,
                "target_source": "UNCONFIGURED",
                "stop_price": "",
                "target1": "",
                "target2": "",
                "ref_close": cur_price,
                "status": "ACTIVE",
            }
            val = validate_position_strategy(new_strat, live, 0, 0.0)

            atr_info = self._resolve_atr_for_position(
                code, live, new_strat, atr_map, today_date, is_suspended=False
            )

            reconciliation_log.append(f"NEW: {code} {name} qty={qty}, avg={avg_p:.0f} | strategy_status: {val['strategy_status']}")
            logger.info(f"[Reconciler] NEW: {name}({code}) {qty}주 @ {avg_p:.0f}원 (전략 미설정 UNCONFIGURED 편입)")

            new_row = [
                f"'{code}",
                exchange,
                name,
                qty,
                int(round(avg_p)),
                cur_price if cur_price > 0 else avg_p,
                "",  # STOP_PRICE empty
                "ACTIVE",
                today_date,
                "TRUE",
                "",  # TARGET1 empty
                "",  # TARGET2 empty
                "UNCONFIGURED",
                "",  # TARGET_BASE_DATE empty
                val["strategy_status"],
                val["strategy_warning"],
                atr_info["atr_round"] if atr_info["atr_round"] is not None else "",
                atr_info["buy_trailing_dist"] if atr_info["buy_trailing_dist"] is not None else "",
                atr_info["sell_trailing_dist"] if atr_info["sell_trailing_dist"] is not None else "",
                atr_info["atr_base_date"] if atr_info["atr_round"] is not None else "",
            ]
            reconciled_rows.append(new_row)

            final_live_positions.append({
                "ticker": code,
                "name": name,
                "qty": qty,
                "avg_price": avg_p,
                "current_price": cur_price,
                "position_status": "OPEN",
                "strategy_revalidation_required": val["strategy_revalidation_required"],
                "revalidation_reasons": val["revalidation_reasons"],
                "stop_status": val["stop_status"],
                "target1_status": val["target1_status"],
                "target2_status": val["target2_status"],
                "strategy_status": val["strategy_status"],
                "strategy_warning": val["strategy_warning"],
                "stop_price": None,
                "target1": None,
                "target2": None,
                "atr_14": atr_info["atr_14"],
                "atr_round": atr_info["atr_round"],
                "atr_text": atr_info["atr_text"],
                "buy_trailing_dist": atr_info["buy_trailing_dist"],
                "sell_trailing_dist": atr_info["sell_trailing_dist"],
                "atr_detail_text": atr_info["atr_detail_text"],
                "atr_base_date": atr_info["atr_base_date"],
                "synced_at_kst": now_str,
            })

        # 6. Write back to Google Sheet if enabled
        sheet_update_success = False
        if sync_to_sheet and reconciled_rows:
            try:
                self.update_header_if_needed()
                self.write_remote_config(reconciled_rows)
                sheet_update_success = True
                logger.info(f"[PortfolioReconciler] Successfully synced {len(reconciled_rows)} rows to Google Sheet {SHEET_CONFIG_TAB}.")
            except Exception as exc:
                logger.error(f"[PortfolioReconciler] Google Sheets update failed: {exc}", exc_info=True)

        # 7. Construct result bundle
        result = {
            "synced_at_kst": now_str,
            "source": "KIWOOM_API_KT00018",
            "account_snapshot_timestamp": now_str,
            "holdings_count": len(final_live_positions),
            "sync_status": "SUCCESS",
            "sheet_sync_status": "SUCCESS" if sheet_update_success else "SKIPPED_OR_FAILED",
            "positions": final_live_positions,
            "reconciliation_log": reconciliation_log,
        }

        # 8. Persist snapshots locally & for GCS backup
        self._save_snapshots(result)

        return result

    def _save_snapshots(self, result: Dict[str, Any]) -> None:
        """Persists PORTFOLIO_LIVE.json and appends to PORTFOLIO_LIVE_HISTORY.json."""
        try:
            if self.state_dir.name == "data":
                out_dir = self.state_dir
            else:
                out_dir = self.state_dir / "data"
            out_dir.mkdir(parents=True, exist_ok=True)

            live_file = out_dir / "PORTFOLIO_LIVE.json"
            with open(live_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            history_file = out_dir / "PORTFOLIO_LIVE_HISTORY.json"
            history = []
            if history_file.exists():
                try:
                    with open(history_file, "r", encoding="utf-8") as f:
                        history = json.load(f)
                        if not isinstance(history, list):
                            history = []
                except Exception:
                    history = []

            # Append new entry, keeping maximum 100 historical entries
            history.append(result)
            if len(history) > 100:
                history = history[-100:]

            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)

            logger.info(f"[PortfolioReconciler] Snapshots saved to {live_file} and {history_file}.")
        except Exception as exc:
            logger.warning(f"[PortfolioReconciler] Snapshot saving warning: {exc}")
