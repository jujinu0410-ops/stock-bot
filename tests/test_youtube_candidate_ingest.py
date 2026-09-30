# -*- coding: utf-8 -*-
import unittest
from datetime import date

from src.analysis.youtube_candidate_ingest import (
    CandidateParseError,
    CandidateRecord,
    aggregate_candidate_pool,
    extract_mentions_section,
    extract_raw_mentions,
    parse_and_resolve_mail,
    parse_report_date,
    upsert_candidate_record,
    expire_candidate,
)


STOCK_ROWS = [
    {"stock_code": "005930", "stock_name": "삼성전자", "market_type": "KOSPI"},
    {"stock_code": "000660", "stock_name": "SK하이닉스", "market_type": "KOSPI"},
    {"stock_code": "009150", "stock_name": "삼성전기", "market_type": "KOSPI"},
    {"stock_code": "010140", "stock_name": "삼성중공업", "market_type": "KOSPI"},
    {"stock_code": "086450", "stock_name": "동국제약", "market_type": "KOSDAQ"},
    {"stock_code": "034020", "stock_name": "두산에너빌리티", "market_type": "KOSPI"},
]


class YouTubeCandidateIngestTests(unittest.TestCase):
    def test_report_date_prefers_body(self):
        subject = "[경제 Intelligence] 9월29일 | AI·반도체"
        body = "분석 기준일: 2026-09-30 (TREND)"
        self.assertEqual(parse_report_date(subject, body), date(2026, 9, 30))

    def test_last_mentions_section_wins(self):
        body = """앞부분
언급종목
- 과거가짜
중간
### 주요 언급종목
- 삼성전자
- SK하이닉스
"""
        section = extract_mentions_section(body)
        self.assertIn("삼성전자", section)
        self.assertNotIn("과거가짜", section)

    def test_inline_heading_and_delimiters(self):
        body = """분석 기준일: 2026-09-30
📌 언급종목: 삼성전자 · SK하이닉스, 삼성전기
"""
        section = extract_mentions_section(body)
        raw = extract_raw_mentions(section)
        self.assertEqual([x.raw_text for x in raw], ["삼성전자", "SK하이닉스", "삼성전기"])

    def test_heading_descriptor_is_not_treated_as_stock(self):
        body = """분석 기준일: 2026-09-30
📌 주요 언급종목 (빈도순)
- 삼성전자
- 삼성전기
"""
        result = parse_and_resolve_mail("[경제 Intelligence] 9월30일", body, STOCK_ROWS)
        self.assertEqual([x.ticker for x in result.resolved], ["005930", "009150"])

    def test_explicit_codes_are_supported_and_deduped(self):
        body = """분석 기준일: 2026-09-30
## 언급 종목
- 삼성전자 (005930)
- 005930 삼성전자
- 동국제약(086450)
"""
        result = parse_and_resolve_mail("[경제 Intelligence] 9월30일", body, STOCK_ROWS)
        self.assertEqual([x.ticker for x in result.resolved], ["005930", "086450"])

    def test_name_resolution_and_unresolved_are_separated(self):
        body = """분석 기준일: 2026-09-30
언급종목
- 삼성중공업
- 존재하지않는회사
- 두산에너빌리티
"""
        result = parse_and_resolve_mail("[경제 Intelligence] 9월30일", body, STOCK_ROWS)
        self.assertEqual({x.ticker for x in result.resolved}, {"010140", "034020"})
        self.assertEqual(len(result.unresolved), 1)
        self.assertEqual(result.unresolved[0].raw_text, "존재하지않는회사")

    def test_missing_section_fails_closed(self):
        body = "분석 기준일: 2026-09-30\n오늘은 언급할 내용이 없습니다."
        with self.assertRaises(CandidateParseError):
            parse_and_resolve_mail("[경제 Intelligence] 9월30일", body, STOCK_ROWS)

    def test_empty_section_fails_closed(self):
        body = "분석 기준일: 2026-09-30\n언급종목\n"
        with self.assertRaises(CandidateParseError):
            parse_and_resolve_mail("[경제 Intelligence] 9월30일", body, STOCK_ROWS)

    def test_all_unresolved_fails_closed(self):
        body = "분석 기준일: 2026-09-30\n언급종목\n- 가짜기업A\n- 가짜기업B"
        with self.assertRaises(CandidateParseError):
            parse_and_resolve_mail("[경제 Intelligence] 9월30일", body, STOCK_ROWS)

    def test_rolling_rebuild_dedupes_duplicate_mail_and_counts_distinct_days(self):
        body_29 = "분석 기준일: 2026-09-29\n언급종목\n- 삼성전자\n- 삼성전기"
        body_30 = "분석 기준일: 2026-09-30\n언급종목\n- 삼성전자"
        r29a = parse_and_resolve_mail("[경제 Intelligence] 9월29일 A", body_29, STOCK_ROWS)
        r29b = parse_and_resolve_mail("[경제 Intelligence] 9월29일 duplicate", body_29, STOCK_ROWS)
        r30 = parse_and_resolve_mail("[경제 Intelligence] 9월30일", body_30, STOCK_ROWS)
        pool = aggregate_candidate_pool([r29a, r29b, r30], as_of_date=date(2026, 9, 30))
        by_code = {x.ticker: x for x in pool}
        self.assertEqual(by_code["005930"].mention_count_30d, 2)
        self.assertEqual(by_code["009150"].mention_count_30d, 1)
        self.assertEqual(by_code["005930"].last_seen_date, date(2026, 9, 30))
        self.assertEqual(by_code["005930"].expires_at, date(2026, 10, 30))

    def test_rolling_rebuild_excludes_events_outside_30_day_window(self):
        old = parse_and_resolve_mail(
            "[경제 Intelligence] old",
            "분석 기준일: 2026-08-31\n언급종목\n- 삼성전자",
            STOCK_ROWS,
        )
        recent = parse_and_resolve_mail(
            "[경제 Intelligence] recent",
            "분석 기준일: 2026-09-30\n언급종목\n- 삼성전자",
            STOCK_ROWS,
        )
        pool = aggregate_candidate_pool([old, recent], as_of_date=date(2026, 9, 30))
        self.assertEqual(pool[0].mention_count_30d, 1)
        self.assertEqual(pool[0].last_seen_date, date(2026, 9, 30))

    def test_rolling_rebuild_preserves_existing_expired_history(self):
        existing = CandidateRecord(
            ticker="005930",
            name="삼성전자",
            first_seen_date=date(2026, 7, 1),
            last_seen_date=date(2026, 8, 1),
            mention_count_30d=4,
            source_subject_latest="old mail",
            expires_at=date(2026, 8, 31),
        )
        pool = aggregate_candidate_pool(
            [],
            as_of_date=date(2026, 9, 30),
            existing_by_ticker={"005930": existing},
        )
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0].candidate_status, "EXPIRED")
        self.assertEqual(pool[0].mention_count_30d, 0)
        self.assertEqual(pool[0].first_seen_date, date(2026, 7, 1))

    def test_duplicate_same_day_is_idempotent(self):
        body = "분석 기준일: 2026-09-29\n언급종목\n- 삼성전자"
        result = parse_and_resolve_mail("[경제 Intelligence] 9월29일", body, STOCK_ROWS)
        mention = result.resolved[0]
        first = upsert_candidate_record(None, mention, result.report_date, "mail A")
        duplicate = upsert_candidate_record(first, mention, result.report_date, "mail A duplicate")
        self.assertEqual(first.mention_count_30d, 1)
        self.assertEqual(duplicate.mention_count_30d, 1)
        self.assertEqual(duplicate.expires_at, date(2026, 10, 29))

    def test_next_day_increments_and_refreshes_ttl(self):
        body = "분석 기준일: 2026-09-29\n언급종목\n- 삼성전자"
        result = parse_and_resolve_mail("[경제 Intelligence] 9월29일", body, STOCK_ROWS)
        mention = result.resolved[0]
        first = upsert_candidate_record(None, mention, date(2026, 9, 29), "mail 9/29")
        second = upsert_candidate_record(first, mention, date(2026, 9, 30), "mail 9/30")
        self.assertEqual(second.mention_count_30d, 2)
        self.assertEqual(second.first_seen_date, date(2026, 9, 29))
        self.assertEqual(second.last_seen_date, date(2026, 9, 30))
        self.assertEqual(second.expires_at, date(2026, 10, 30))

    def test_expiry_marks_but_does_not_delete(self):
        r = CandidateRecord(
            ticker="005930",
            name="삼성전자",
            first_seen_date=date(2026, 8, 1),
            last_seen_date=date(2026, 8, 31),
            mention_count_30d=3,
            source_subject_latest="x",
            expires_at=date(2026, 9, 30),
        )
        self.assertEqual(expire_candidate(r, date(2026, 9, 30)).candidate_status, "ACTIVE")
        self.assertEqual(expire_candidate(r, date(2026, 10, 1)).candidate_status, "EXPIRED")

    def test_held_candidate_is_never_expired_by_ttl(self):
        r = CandidateRecord(
            ticker="005930",
            name="삼성전자",
            first_seen_date=date(2026, 8, 1),
            last_seen_date=date(2026, 8, 1),
            mention_count_30d=1,
            source_subject_latest="x",
            expires_at=date(2026, 8, 31),
            holding_status="HELD",
            candidate_status="HELD",
        )
        self.assertEqual(expire_candidate(r, date(2026, 10, 1)).candidate_status, "HELD")


if __name__ == "__main__":
    unittest.main(verbosity=2)
