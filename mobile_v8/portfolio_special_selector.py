from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

ETF_KEYWORDS = (
    "KODEX", "TIGER", "ACE", "PLUS", "RISE", "SOL", "TIMEFOLIO",
    "ARIRANG", "KBSTAR", "HANARO", "WOORI", "KOSEF", "ETF", "ETN",
    "고배당주", "커버드콜", "차이나전기차", "AI밸류체인", "미국AI",
)


def normalize_code(value: object) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def classify_asset_type(stock_name: str) -> str:
    compact = str(stock_name or "").upper().replace(" ", "")
    return "ETF" if any(keyword in compact for keyword in ETF_KEYWORDS) else "STOCK"


def parse_portfolio_config(values: List[List[Any]]) -> Dict[str, Dict[str, str]]:
    """Parse PORTFOLIO_CONFIG rows without depending on a fixed header row number."""
    header_idx = -1
    header: List[str] = []
    for idx, row in enumerate(values):
        normalized = [str(cell or "").strip() for cell in row]
        if "종목코드" in normalized and "종목명" in normalized and "매매모드" in normalized:
            header_idx = idx
            header = normalized
            break
    if header_idx < 0:
        raise RuntimeError("PORTFOLIO_CONFIG_HEADER_NOT_FOUND")

    col = {name: header.index(name) for name in ("Active", "종목명", "종목코드", "매매모드") if name in header}
    if "종목명" not in col or "종목코드" not in col or "매매모드" not in col:
        raise RuntimeError("PORTFOLIO_CONFIG_REQUIRED_COLUMNS_MISSING")

    result: Dict[str, Dict[str, str]] = {}
    for row in values[header_idx + 1 :]:
        padded = list(row) + [""] * max(0, len(header) - len(row))
        code = normalize_code(padded[col["종목코드"]])
        if not code:
            continue
        result[code] = {
            "active": str(padded[col["Active"]]).strip().upper() if "Active" in col else "Y",
            "stock_name": str(padded[col["종목명"]]).strip(),
            "mode": str(padded[col["매매모드"]]).strip().upper(),
        }
    return result


def filter_eligible_holdings(
    holdings: Iterable[Dict[str, Any]],
    config_by_code: Dict[str, Dict[str, str]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    eligible: List[Dict[str, Any]] = []
    excluded: List[Dict[str, str]] = []

    for raw in holdings:
        item = dict(raw)
        code = normalize_code(item.get("stock_code"))
        name = str(item.get("stock_name") or "").strip()
        if not code or not name:
            excluded.append({"stock_code": code or "UNKNOWN", "reason": "INVALID_HOLDING"})
            continue

        cfg = config_by_code.get(code)
        if cfg is None:
            excluded.append({"stock_code": code, "reason": "CONFIG_MISSING"})
            continue
        if cfg.get("active", "Y") not in {"Y", "YES", "TRUE", "1"}:
            excluded.append({"stock_code": code, "reason": "INACTIVE"})
            continue
        if classify_asset_type(name) == "ETF":
            excluded.append({"stock_code": code, "reason": "ETF_EXCLUDED"})
            continue

        mode = str(cfg.get("mode") or "").upper()
        if "SUSPENDED" in mode or "거래정지" in mode:
            excluded.append({"stock_code": code, "reason": "SUSPENDED_EXCLUDED"})
            continue

        item["stock_code"] = code
        item["stock_name"] = name
        item["portfolio_mode"] = mode or "UNKNOWN"
        item["quantity"] = int(float(item.get("quantity") or 0))
        item["avg_buy_price"] = float(item.get("avg_buy_price") or 0.0)
        item["invested_cost"] = item["quantity"] * item["avg_buy_price"]
        eligible.append(item)

    return eligible, excluded


def sync_state(
    state: Dict[str, Any],
    all_holdings: Iterable[Dict[str, Any]],
    now: datetime,
) -> Tuple[Dict[str, Any], bool, set[str]]:
    state = dict(state or {})
    records = dict(state.get("holdings") or {})
    initial = not bool(records)
    current_codes: set[str] = set()
    new_codes: set[str] = set()
    now_iso = now.isoformat()

    for holding in all_holdings:
        code = normalize_code(holding.get("stock_code"))
        if not code:
            continue
        current_codes.add(code)
        rec = dict(records.get(code) or {})
        if not rec:
            rec = {
                "first_seen_at": now_iso,
                "analysis_count": 0,
                "last_analyzed_at": None,
            }
            if not initial:
                new_codes.add(code)
        rec["stock_name"] = str(holding.get("stock_name") or rec.get("stock_name") or code)
        rec["last_seen_at"] = now_iso
        rec["currently_held"] = True
        records[code] = rec

    for code, rec0 in list(records.items()):
        rec = dict(rec0)
        if code not in current_codes:
            rec["currently_held"] = False
        records[code] = rec

    state["version"] = 1
    state.setdefault("initialized_at", now_iso)
    state["updated_at"] = now_iso
    state["holdings"] = records
    return state, initial, new_codes


def _parse_dt(value: object, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None and fallback.tzinfo is not None:
            dt = dt.replace(tzinfo=fallback.tzinfo)
        return dt
    except Exception:
        return fallback


def choose_holding(
    eligible: List[Dict[str, Any]],
    state: Dict[str, Any],
    new_codes: set[str],
    now: datetime,
    priority_codes: List[str] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not eligible:
        raise RuntimeError("NO_ELIGIBLE_HOLDING")

    priority_codes = [normalize_code(code) for code in (priority_codes or []) if normalize_code(code)]
    priority_rank = {code: len(priority_codes) - idx for idx, code in enumerate(priority_codes)}
    records = state.get("holdings") or {}
    scored = []

    for item in eligible:
        code = item["stock_code"]
        rec = records.get(code) or {}
        last_raw = rec.get("last_analyzed_at")
        never = not bool(last_raw)
        last_dt = _parse_dt(last_raw, now)
        days_since = 999999.0 if never else max(0.0, (now - last_dt).total_seconds() / 86400.0)
        score = (
            priority_rank.get(code, 0),
            1 if code in new_codes else 0,
            1 if never else 0,
            days_since,
            float(item.get("invested_cost") or 0.0),
        )
        scored.append((score, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    score, selected = scored[0]
    reason = {
        "manual_priority": bool(score[0]),
        "new_holding": bool(score[1]),
        "never_analyzed": bool(score[2]),
        "days_since_last_analysis": None if score[2] else round(score[3], 2),
        "invested_cost": round(score[4], 2),
        "eligible_count": len(eligible),
    }
    return selected, reason


def mark_analysis_success(
    state: Dict[str, Any],
    stock_code: str,
    now: datetime,
    drive_file_id: str,
    drive_filename: str,
) -> Dict[str, Any]:
    state = dict(state)
    records = dict(state.get("holdings") or {})
    code = normalize_code(stock_code)
    rec = dict(records.get(code) or {})
    rec["last_analyzed_at"] = now.isoformat()
    rec["analysis_count"] = int(rec.get("analysis_count") or 0) + 1
    rec["last_drive_file_id"] = drive_file_id
    rec["last_drive_filename"] = drive_filename
    records[code] = rec
    state["holdings"] = records
    state["last_success"] = {
        "stock_code": code,
        "at": now.isoformat(),
        "drive_file_id": drive_file_id,
        "drive_filename": drive_filename,
    }
    state["updated_at"] = now.isoformat()
    return state
