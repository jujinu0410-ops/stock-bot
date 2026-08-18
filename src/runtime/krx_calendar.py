"""
Canonical KRX Trading Calendar & 45-Minute Bar Schedule Manager
Phase 8 Runtime Layer (V1.0 Operations)

- Accurate KRX Trading Day detection (weekends, public holidays, substitute holidays, Labor Day, Year-End close).
- 45-minute completed bar slot resolution for Korean Regular Trading Session (09:00 - 15:30 KST).
- Fail-closed behavior on calendar resolution failure.
"""

from datetime import datetime, date, time
from typing import Union, Optional, Tuple, Set

# Known KRX Public Holidays & Closures (YYYY-MM-DD)
# Covers 2024 - 2027 canonical holidays and KRX market closures
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
FIXED_SOLAR_HOLIDAYS = {
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

# Standard 45-minute completed bar timetable during regular trading hours (09:00 - 15:30)
# (Slot Name, Scheduled Time, Bar Start Time, Bar End Time)
COMPLETED_45M_BAR_SCHEDULE = [
    ("09:50", time(9, 50), "09:00", "09:45"),
    ("10:35", time(10, 35), "09:45", "10:30"),
    ("11:35", time(11, 35), "10:30", "11:15"),
    ("12:05", time(12, 5),  "11:15", "12:00"),
    ("12:50", time(12, 50), "12:00", "12:45"),
    ("13:35", time(13, 35), "12:45", "13:30"),
    ("14:20", time(14, 20), "13:30", "14:15"),
    ("15:05", time(15, 5),  "14:15", "15:00")
]

class KRXCalendar:
    """Canonical KRX Trading Calendar & Intraday 45m Bar Resolver"""

    @staticmethod
    def parse_to_date(dt_input: Union[datetime, date, str]) -> date:
        """Helper to parse datetime, date, or str into date object"""
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

    @classmethod
    def is_krx_trading_day(cls, dt_input: Union[datetime, date, str]) -> bool:
        """
        KRX 정규 거래일 여부 판정
        - 주말(토요일=5, 일요일=6) -> False
        - 공휴일/대체공휴일/근로자의날/연말휴장일 -> False
        """
        try:
            d = cls.parse_to_date(dt_input)
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
            # 12월 31일이 토요일/일요일인 경우 마지막 금요일이 휴장일
            dec31 = date(d.year, 12, 31)
            last_weekday_day = 31
            if dec31.weekday() == 5: # 토요일
                last_weekday_day = 30
            elif dec31.weekday() == 6: # 일요일
                last_weekday_day = 29
            if d.day == last_weekday_day:
                return False

        return True

    @classmethod
    def get_krx_trading_date_str(cls, dt_input: Union[datetime, date, str]) -> str:
        """YYYY-MM-DD 형식의 문자열 반환"""
        d = cls.parse_to_date(dt_input)
        return d.strftime("%Y-%m-%d")

    @classmethod
    def get_completed_45m_bar(cls, dt_input: datetime) -> Optional[Tuple[str, str, str]]:
        """
        현재 시각에 해당하는 직전 완료 45분봉 반환
        Returns: (slot_name, bar_start_time, bar_end_time) or None
        """
        t = dt_input.time()
        
        # 09:00 이전에는 완성된 45분봉 없음
        if t < time(9, 45):
            return None
            
        # 15:05 이후 15:30까지는 14:15~15:00 완성봉 유지 (15:00~15:30은 30분 미완성 구간이므로 임의 45분봉 생성 금지)
        for slot_name, sched_t, b_start, b_end in reversed(COMPLETED_45M_BAR_SCHEDULE):
            # sched_t보다 늦거나 같은 시각이면 해당 슬롯의 완성봉 매칭
            # 단, 5분 전(예: 09:45)부터 해당 완성봉이 성립됨
            b_end_time = datetime.strptime(b_end, "%H:%M").time()
            if t >= b_end_time:
                return (slot_name, b_start, b_end)

        return None

    @classmethod
    def get_expected_45m_bar_for_slot(cls, slot_name: str) -> Optional[Tuple[str, str, str]]:
        """특정 슬롯 이름('09:50', '10:35', ...)에 해당하는 완성 45분봉 반환"""
        for s_name, _, b_start, b_end in COMPLETED_45M_BAR_SCHEDULE:
            if s_name == slot_name:
                return (s_name, b_start, b_end)
        return None
