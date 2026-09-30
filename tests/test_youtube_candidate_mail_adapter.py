# -*- coding: utf-8 -*-
import unittest
from datetime import date

from src.analysis.youtube_candidate_mail_adapter import (
    extract_live_card_mentions,
    parse_and_resolve_mail,
)


class YouTubeCandidateMailAdapterTests(unittest.TestCase):
    def test_current_live_heading_and_vertical_cards(self):
        body = """Economic Intelligence V2 · TREND
분석 기준일: 2026-09-30 (TREND)

📌 오늘 언급·주목 종목 (V8 분석 후보군)
※ 매수추천이 아닌 후속 분석 후보 제시 목적

삼성전기
(009150)
[직접 언급]
2개 채널 교차 언급
이유: 6.8조 원 규모 FC-BGA 시설 투자 및 ABF 기판 수혜
근거(Evidence): 영상 링크

DB하이텍
(000990)
[AI 연계]
1개 채널 언급
이유: 파운드리 업황 회복 기대

HPSP
(403870)
[직접 언급]
이유: 고압 수소 어닐링 장비 수요

📺 채널 수집 감사
ACTIVE 30 · 정상수집 30
"""
        result = parse_and_resolve_mail(
            "[경제 Intelligence] 9월30일 | AI·반도체",
            body,
            # Intentionally incomplete registry. Explicit codes printed in the
            # report are authoritative candidate identity.
            [{"stock_code": "009150", "stock_name": "삼성전기", "market_type": "KOSPI"}],
        )
        self.assertEqual([x.ticker for x in result.resolved], ["009150", "000990", "403870"])
        self.assertEqual([x.name for x in result.resolved], ["삼성전기", "DB하이텍", "HPSP"])
        self.assertEqual(result.report_date, date(2026, 9, 30))
        self.assertEqual(len(result.unresolved), 0)

    def test_card_metadata_and_reason_are_not_candidates(self):
        body = """분석 기준일: 2026-09-30
📌 오늘 언급·주목 종목 (V8 분석 후보군)
※ 매수추천이 아닌 후속 분석 후보 제시 목적
대한전선
(001440)
[테마 수혜]
3개 채널 교차 언급
이유: 전력망 투자 확대에 따른 전선 수요 증가
근거(Evidence): [영상]
리노공업
(058470)
[직접 언급]
이유: 테스트 소켓 수요
📺 채널 수집 감사
"""
        raw = extract_live_card_mentions(body)
        self.assertEqual([(x.raw_text, x.explicit_code) for x in raw], [
            ("대한전선", "001440"),
            ("리노공업", "058470"),
        ])

    def test_same_line_explicit_code_supported(self):
        body = """분석 기준일: 2026-09-30
📌 오늘 언급·주목 종목 (V8 분석 후보군)
삼성전자 (005930)
SK하이닉스 (000660)
📺 채널 수집 감사
"""
        result = parse_and_resolve_mail(
            "[경제 Intelligence] 9월30일",
            body,
            [],
        )
        self.assertEqual([x.ticker for x in result.resolved], ["005930", "000660"])

    def test_explicit_no_new_candidates_is_valid_empty_event(self):
        body = """Economic Intelligence V2 · TREND
분석 기준일: 2026-09-28 (TREND)

📌 오늘 언급·주목 종목 (V8 분석 후보군)
※ 매수추천이 아닌 후속 분석 후보 제시 목적

오늘 신규 주목 종목 없음

📺 채널 수집 감사
ACTIVE 30 · 정상수집 30
"""
        result = parse_and_resolve_mail(
            "[경제 Intelligence] 9월28일 | 금리·통화정책 · AI·반도체",
            body,
            [],
        )
        self.assertEqual(result.report_date, date(2026, 9, 28))
        self.assertEqual(result.raw_mentions, tuple())
        self.assertEqual(result.resolved, tuple())
        self.assertEqual(result.unresolved, tuple())

    def test_legacy_heading_falls_back_to_core(self):
        body = """분석 기준일: 2026-09-30
언급종목
- 삼성전자
- SK하이닉스
"""
        rows = [
            {"stock_code": "005930", "stock_name": "삼성전자", "market_type": "KOSPI"},
            {"stock_code": "000660", "stock_name": "SK하이닉스", "market_type": "KOSPI"},
        ]
        result = parse_and_resolve_mail("[경제 Intelligence] 9월30일", body, rows)
        self.assertEqual([x.ticker for x in result.resolved], ["005930", "000660"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
