from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Set, Union

# Known KRX Public Holidays & Closures (YYYY-MM-DD)
# Canonical coverage from 2024 to 2027
KNOWN_KRX_HOLIDAYS: Set[str] = {
    # 2024
    "2024-01-01", "2024-02-09", "2024-02-12", "2024-03-01", "2024-04-10",
    "2024-05-01", "2024-05-06", "2024-05-15", "2024-06-06", "2024-08-15",
    "2024-09-16", "2024-09-17", "2024-09-18", "2024-10-01", "2024-10-03",
    "2024-10-09", "2024-12-25", "2024-12-31",
    # 2025
    "2025-01-01", "2025-01-27", "2025-01-28", "2025-01-29", "2025-01-30",
    "2025-03-01", "2025-03-03", "2025-05-01", "2025-05-05", "2025-05-06",
    "2025-06-06", "2025-08-15", "2025-10-03", "2025-10-05", "2025-10-06",
    "2025-10-07", "2025-10-08", "2025-10-09", "2025-12-25", "2025-12-31",
    # 2026
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-03-01", "2026-03-02", "2026-05-01", "2026-05-05", "2026-05-24",
    "2026-05-25", "2026-06-06", "2026-08-15", "2026-08-17", "2026-09-24",
    "2026-09-25", "2026-09-26", "2026-09-28", "2026-10-03", "2026-10-05",
    "2026-10-09", "2026-12-25", "2026-12-31",
    # 2027
    "2027-01-01", "2027-02-06", "2027-02-07", "2027-02-08", "2027-02-09",
    "2027-03-01", "2027-05-01", "2027-05-05", "2027-05-13", "2027-06-06",
    "2027-06-07", "2027-08-15", "2027-08-16", "2027-09-14", "2027-09-15",
    "2027-09-16", "2027-10-03", "2027-10-04", "2027-10-09", "2027-10-11",
    "2027-12-25", "2027-12-31"
}

# Fixed annual solar holidays (MM-DD)
FIXED_SOLAR_HOLIDAYS: Set[str] = {
    "01-01",  # 신정 (New Year)
    "03-01",  # 삼일절 (Independence Movement Day)
    "05-01",  # 근로자의 날 (Labor Day / KRX Closed)
    "05-05",  # 어린이날 (Children's Day)
    "06-06",  # 현충일 (Memorial Day)
    "08-15",  # 광복절 (Liberation Day)
    "10-03",  # 개천절 (National Foundation Day)
    "10-09",  # 한글날 (Hangul Day)
    "12-25",  # 성탄절 (Christmas)
    "12-31",  # 연말 휴장일 (KRX Year-end close)
}


def parse_to_date(dt_input: Union[datetime, date, str]) -> date:
    """Helper to parse datetime, date, or str into date object."""
    if isinstance(dt_input, datetime):
        return dt_input.date()
    elif isinstance(dt_input, date):
        return dt_input
    elif isinstance(dt_input, str):
        clean_s = dt_input.strip().replace("/", "-")
        if " " in clean_s:
            clean_s = clean_s.split(" ")[0]
        if "T" in clean_s:
            clean_s = clean_s.split("T")[0]
        if len(clean_s) == 8 and clean_s.isdigit():
            return date(int(clean_s[:4]), int(clean_s[4:6]), int(clean_s[6:8]))
        parts = clean_s.split("-")
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    raise ValueError(f"Unable to parse date from input: {dt_input}")


def is_krx_trading_day(dt_input: Union[datetime, date, str]) -> bool:
    """KRX 정규 거래일 여부 판정.
    - 주말(토=5, 일=6) -> False
    - 등록 공휴일/대체공휴일/근로자의날/연말휴장일 -> False
    """
    try:
        d = parse_to_date(dt_input)
    except Exception:
        return False

    # 1. 주말 체크 (0=월, ..., 5=토, 6=일)
    if d.weekday() >= 5:
        return False

    date_str = d.strftime("%Y-%m-%d")
    mm_dd = d.strftime("%m-%d")

    # 2. 기등록된 공휴일/휴장일 목록 체크
    if date_str in KNOWN_KRX_HOLIDAYS:
        return False

    # 3. 고정 공휴일 체크
    if mm_dd in FIXED_SOLAR_HOLIDAYS:
        return False

    # 4. 연말 마지막 영업일 처리 (12월 31일이 주말인 경우 12월 30일 또는 29일 휴장)
    if d.month == 12 and d.day >= 29:
        dec31 = date(d.year, 12, 31)
        last_weekday_day = 31
        if dec31.weekday() == 5:
            last_weekday_day = 30
        elif dec31.weekday() == 6:
            last_weekday_day = 29
        if d.day == last_weekday_day:
            return False

    return True


def get_last_completed_trading_day(asof_input: Union[datetime, date, str]) -> date:
    """현재 날짜 직전의 가장 최근 정규 KRX 거래일을 반환합니다.
    연휴/주말/공휴일을 거슬러 올라가 마지막 정상 거래일을 탐색합니다.
    """
    asof = parse_to_date(asof_input)
    candidate = asof - timedelta(days=1)
    # 안전 상한선: 최대 30일 역방향 탐색
    for _ in range(30):
        if is_krx_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise RuntimeError(f"KRX_CALENDAR_FAILED_TO_FIND_PREVIOUS_TRADING_DAY:{asof}")
