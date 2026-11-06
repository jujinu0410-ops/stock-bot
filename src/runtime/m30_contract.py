"""Strict M30 metadata contracts. Missing evidence never becomes valid evidence."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo
from src.runtime.krx_calendar import KRXCalendar, COMPLETED_30M_BAR_SCHEDULE

KST = ZoneInfo('Asia/Seoul')

def normalize_validity(value):
    if value is None: return None
    if isinstance(value, (bool, int, float)):
        return int(value) if value in (0, 1) else None
    if isinstance(value, str):
        text=value.strip().upper()
        if re.match(r'^[^A-Z0-9]*(INVALID|PARTIAL)\b',text): return 0
        if re.match(r'^[^A-Z0-9]*VALID\b',text): return 1
        if text in ('0','1'): return int(text)
    return None

def kst_datetime(value):
    if isinstance(value,datetime): result=value
    elif isinstance(value,str): result=datetime.fromisoformat(value.strip().removesuffix(' KST'))
    else: raise ValueError('INVALID_TIMESTAMP_TYPE')
    return result.astimezone(KST).replace(tzinfo=None) if result.tzinfo else result

def completed_timestamp(value, asof=None, trading_date=None):
    try:
        stamp=kst_datetime(value)
        if stamp.second or stamp.microsecond: return None
        if not KRXCalendar.is_krx_trading_day(stamp): return None
        if stamp.strftime('%H:%M') not in {x[3] for x in COMPLETED_30M_BAR_SCHEDULE}: return None
        if trading_date is not None and stamp.date().isoformat()!=str(trading_date): return None
        if asof is not None:
            observed=kst_datetime(asof)
            if stamp>observed or stamp.date()!=observed.date(): return None
        return stamp.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError,TypeError,OverflowError):return None
