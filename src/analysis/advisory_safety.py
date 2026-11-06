"""Existing held-position protection takes precedence over positive advice."""
import math

def positive(value):
    if isinstance(value, bool): return None
    try:
        n=float(value)
        return n if math.isfinite(n) and n>0 else None
    except (TypeError,ValueError): return None

def stop_breached(item):
    price=positive(item.get("current_price",item.get("market_price")))
    stops=[positive(item.get(k)) for k in ("confirmed_stop_price","kiwoom_stop_tick_price","previous_confirmed_stop","prev_confirmed_stop")]
    stops=[v for v in stops if v is not None]
    if price is not None and stops:
        return price <= max(stops)
    return item.get("is_stop_breached") is True

def priority(item):
    if not item: return None
    if item.get("hard_stop_active") or item.get("trade_mode") in ("HARD_STOP","EMERGENCY"): return "HARD_STOP"
    if stop_breached(item): return "STOP_BREACHED"
    if item.get("is_suspended") or item.get("trade_mode")=="SUSPENDED_HOLD": return "SUSPENDED"
    if item.get("data_validity_flag")==0 or item.get("trade_mode")=="HOLD": return "DATA_HOLD"
    return None

def apply_priority(entry,item):
    reason=priority(item)
    if not reason: return entry
    return {**entry,"add_advisory_state":"ADD_BLOCKED","alert_candidate":0,
            "reason_codes":reason+","+str(entry.get("reason_codes") or "")}
