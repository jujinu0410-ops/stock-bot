"""
src/policy/policy_shadow_observer.py

Policy Shadow Single-Shot Observation Bridge for Cloud Run Phase 1
- Captures authoritative Policy observation at 11:20 and 15:35
- Uses existing V4 indicators, completed 45m bar states, DART validity, and historical cycles
- Persists to policy_shadow.db without running full 45m background scans
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from src.database.db_manager import DatabaseManager
from src.policy.policy_shadow_store import PolicyShadowService, PolicyShadowStore
from src.utils.logger import logger


def policy_validity(value):
    """Adapt observed validity to the nullable INTEGER downstream contract."""
    if isinstance(value, str):
        value = value.strip().upper()
        return {"VALID": 1, "INVALID": 0, "PARTIAL": 0, "1": 1, "0": 0}.get(value)
    return int(value) if value is not None and value in (0, 1) else None


def observed_completed_timestamp(value, asof):
    """Accept only observed, aligned, already completed timestamps."""
    try:
        stamp = datetime.fromisoformat(str(value))
        observed_at = datetime.fromisoformat(str(asof))
        if stamp.tzinfo is not None or observed_at.tzinfo is not None:
            from zoneinfo import ZoneInfo
            kst = ZoneInfo("Asia/Seoul")
            stamp = stamp.replace(tzinfo=kst) if stamp.tzinfo is None else stamp.astimezone(kst)
            observed_at = observed_at.replace(tzinfo=kst) if observed_at.tzinfo is None else observed_at.astimezone(kst)
        ends = {"09:45", "10:30", "11:15", "12:00", "12:45", "13:30", "14:15", "15:00"}
        if stamp <= observed_at and stamp.second == 0 and stamp.microsecond == 0 and stamp.strftime("%H:%M") in ends:
            return stamp.strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        pass
    return None


def observe_report_policy_shadow(
    db: Optional[DatabaseManager],
    held_status: List[Dict[str, Any]],
    run_id: str,
    asof_dt: Optional[datetime] = None,
    policy_store: Optional[PolicyShadowStore] = None,
) -> List[Dict[str, Any]]:
    """
    Executes a single-shot Policy observation for all held stocks at report generation time (11:20 / 15:35).
    Reuses authoritative V4 inputs and persists snapshots to policy_shadow.db.
    """
    if not held_status:
        return []

    dt = asof_dt or datetime.now()
    now_str = dt.strftime("%Y-%m-%d %H:%M:%S")

    store = policy_store or PolicyShadowStore()
    service = PolicyShadowService(store)

    all_held_codes = [str(h.get("stock_code", "")).zfill(6) for h in held_status if int(h.get("quantity", 0) or 0) > 0]

    snapshots = []
    for h in held_status:
        code = str(h.get("stock_code", "")).zfill(6)
        qty = int(h.get("quantity", 0) or 0)
        if qty <= 0:
            continue

        avg_price = h.get("avg_buy_price")
        current_price = h.get("current_price") or h.get("market_price")
        loss_pct = ((float(current_price) / float(avg_price) - 1.0) * 100.0) if avg_price and current_price else None

        is_etf = bool(h.get("is_etf", False)) or code in ["371460", "484730", "490590", "161510", "088500"]
        f_score_val = None if is_etf else h.get("f_score")

        trade_mode = str(h.get("trade_mode") or "NORMAL")
        data_hold = str(h.get("data_hold_reason") or "")

        # 45m authoritative fields from Intraday45mAnalyzer / held_status
        # Legacy intraday_last_timestamp is a left-labelled resample index;
        # alignment alone does not prove that this candle is complete.
        raw_45m_ts = h.get("completed_45m_timestamp")
        completed_45m_bar_ts = observed_completed_timestamp(raw_45m_ts, dt)

        is_bearish_2plus = int(bool(h.get("is_45m_bearish_2plus")))
        is_breakdown = int(bool(h.get("is_45m_breakdown")))

        raw_input = {
            "asof_timestamp": now_str,
            "source_identifier": f"{run_id}:{code}",
            "stock_code": code,
            "market_price": current_price,
            "quantity": qty,
            "weighted_avg_price": avg_price,
            "loss_pct": loss_pct,
            "atr14": h.get("completed_atr") or h.get("atr_14") or h.get("current_completed_atr"),
            "f_score": f_score_val,
            "t_score": h.get("t_score"),
            "is_etf": is_etf,
            "risk_target_qty": h.get("risk_target_qty"),
            "daily_state": h.get("technical_state") or h.get("signal_stage1_daily_state"),
            "is_45m_bearish_2plus": is_bearish_2plus,
            "is_45m_breakdown": is_breakdown,
            "is_45m_bearish_gate": int(bool(is_bearish_2plus or is_breakdown)),
            "completed_45m_timestamp": completed_45m_bar_ts,
            "concentration_state": "BLOCKED" if trade_mode == "CONCENTRATION_RISK" else "CLEAR",
            "data_validity": policy_validity(h.get("data_validity_flag")),
            "suspension_state": "SUSPENDED" if trade_mode == "SUSPENDED_HOLD" or "거래정지" in data_hold else "ACTIVE",
            "v4_effective_stop": h.get("effective_exit_line"),
        }

        try:
            snap = service.observe(raw_input, all_held_codes)
            snapshots.append(snap)
            logger.info(
                f"✅ [PolicyShadow] Single-shot observation captured for {h.get('stock_name', code)}({code}): "
                f"Action={snap.get('shadow_action')}, ExitLine={snap.get('shadow_effective_exit')}"
            )
        except Exception as e:
            logger.warning(f"[PolicyShadow] Observation failed for {code}: {e}", exc_info=True)

    return snapshots
