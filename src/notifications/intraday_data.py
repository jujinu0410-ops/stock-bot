"""Read-only inputs for intraday mail; never interpret strategy states as alerts."""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def kst_time(value):
    if isinstance(value, (int, float)):
        result = datetime(1899, 12, 30) + timedelta(days=value)
    elif isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace(" KST", "").replace("Z", "+00:00"))
    return result.replace(tzinfo=KST) if result.tzinfo is None else result.astimezone(KST)


def select_latest_alerts(values, report_asof):
    """ALERT_LOG is appended after actual delivery. Keep one log event per code.

    Read raw Sheets serial dates: the sheet's displayed date hides HH:mm.
    Reject malformed rows rather than claiming there was no signal today.
    """
    cutoff = kst_time(report_asof)
    required = {"TIMESTAMP", "TICKER", "EVENT_KEY", "MESSAGE"}
    if not values or not required.issubset(values[0]):
        raise ValueError("ALERT_LOG schema unavailable")
    latest = {}
    for row in values[1:]:
        item = dict(zip(values[0], row))
        if not item.get("TIMESTAMP"):
            continue
        stamp = kst_time(item["TIMESTAMP"])
        if stamp.date() != cutoff.date() or stamp > cutoff:
            continue
        ticker = str(item.get("TICKER", "")).strip().removesuffix(".0")
        code = ticker.rsplit(":", 1)[-1].zfill(6)
        key = str(item.get("EVENT_KEY") or "").strip()
        if not code.isdigit() or len(code) != 6 or not key:
            raise ValueError("Malformed ALERT_LOG event")
        if code not in latest or stamp >= kst_time(latest[code]["occurred_at"]):
            latest[code] = {"event_key": key, "message": str(item.get("MESSAGE") or ""),
                            "occurred_at": stamp.isoformat(), "source": "ALERT_LOG"}
    return {"status": "AVAILABLE", "asof": cutoff.isoformat(), "by_code": latest}


def load_recent_alerts(report_asof):
    """Read the same Sheet as the holdings monitoring center. No writes or sends."""
    try:
        import requests
        from src.engine.portfolio_reconciler import PortfolioReconciler
        reader = PortfolioReconciler()
        response = requests.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{reader.spreadsheet_id}/values/ALERT_LOG!A1:K2001",
            headers={"Authorization": "Bearer " + reader._get_token()},
            params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"},
            timeout=20,
        )
        response.raise_for_status()
        return select_latest_alerts(response.json().get("values", []), report_asof)
    except Exception:
        # Access failures are distinct from a successful lookup with no events.
        return {"status": "UNAVAILABLE", "by_code": {}}


def prepare_intraday_holdings(held, live_positions):
    """Keep engine inputs intact, preserving account average-price precision."""
    live = {str(p["stock_code"]).zfill(6): p for p in live_positions}
    codes = [str(h["stock_code"]).zfill(6) for h in held]
    if len(codes) != len(set(codes)) or set(codes) != set(live):
        raise ValueError("Intraday holdings/account code mismatch")
    result = []
    for h in held:
        account = live[str(h["stock_code"]).zfill(6)]
        if number(h.get("quantity")) != number(account.get("quantity")):
            raise ValueError("Intraday holdings/account quantity mismatch")
        copy = dict(h)
        avg = number(account.get("avg_buy_price"))
        displayed = number(h.get("avg_buy_price"))
        if avg is not None and displayed is not None and abs(avg - displayed) >= 1:
            raise ValueError("Intraday holdings/account average mismatch")
        copy["avg_buy_price"] = account.get("avg_buy_price")
        valuation(copy)  # Validate against the engine's existing P&L before dispatch.
        result.append(copy)
    return result


def valuation(item):
    """Reuse verified engine P&L. Missing fields stay missing, including quantity."""
    price, avg, qty, amount, pct = (number(item.get(k)) for k in
                                  ("current_price", "avg_buy_price", "quantity", "pnl_amount", "pnl_pct"))
    if price is None or avg is None or qty is None or price <= 0 or avg <= 0 or qty <= 0:
        return price if price is not None and price > 0 else None, avg if avg is not None and avg > 0 else None, None, None
    expected = (price - avg) * qty
    rate = (price - avg) / avg * 100
    # Engine truncates won to int and rounds percentage to 2 places.
    if amount is not None and (abs(amount - expected) >= 1 or amount * expected < 0):
        raise ValueError(f"P&L amount mismatch: {item.get('stock_code')}")
    if pct is not None and (abs(pct - rate) > Decimal("0.011") or pct * expected < 0):
        raise ValueError(f"P&L percentage mismatch: {item.get('stock_code')}")
    return price, avg, amount, pct
