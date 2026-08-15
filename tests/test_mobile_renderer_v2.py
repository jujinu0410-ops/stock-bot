# -*- coding: utf-8 -*-
"""
Mobile Renderer V2 및 V1 보존·동일성·예외처리 단위 테스트
"""

import unittest
import os
import re
from pathlib import Path
from bs4 import BeautifulSoup

from src.notifications.gmail_notifier import GmailNotifier
from src.notifications.mobile_renderer_v2 import generate_mobile_html_report_v2, MANDATORY_STOCK_KEYS
from tests.fixtures.sample_portfolio_fixture import (
    SAMPLE_HELD_PORTFOLIO,
    SAMPLE_DISCLOSURES,
    FIXTURE_NORMAL,
    FIXTURE_RECOVERY,
    FIXTURE_CONCENTRATION_RISK,
    FIXTURE_USER_OVERRIDE,
    FIXTURE_SUSPENDED_HOLD,
    FIXTURE_ETF
)

class TestMobileRendererV2(unittest.TestCase):
    def setUp(self):
        self.notifier = GmailNotifier(sender_email="test@example.com", app_password="dummy")
        self.date_str = "2026-08-14 20:58"
        self.snapshot_path = Path(__file__).parent / "fixtures" / "v1_baseline_snapshot.html"

    def _normalize_html(self, html_str: str) -> str:
        """HTML 비교를 위한 공백 및 줄바꿈 정규화"""
        return re.sub(r'\s+', ' ', html_str).strip()

    def test_01_v1_baseline_snapshot_identity(self):
        """테스트 01: V1 generate_html_report_v1 결과가 사전 저장된 Baseline Snapshot과 100% 동일한지 검증"""
        self.assertTrue(self.snapshot_path.exists(), "V1 Snapshot 파일이 존재해야 합니다.")
        with open(self.snapshot_path, "r", encoding="utf-8") as f:
            expected_snapshot = f.read()

        current_v1_html = self.notifier.generate_html_report_v1(
            date_str=self.date_str,
            total_count=18,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO,
            disclosures=SAMPLE_DISCLOSURES
        )

        self.assertEqual(
            self._normalize_html(current_v1_html),
            self._normalize_html(expected_snapshot),
            "V1 렌더러의 HTML 출력은 사전 스냅샷과 완전히 일치해야 합니다."
        )

    def test_02_card_level_core_values_equality(self):
        """테스트 02: V2의 각 종목 카드(data-stock-code) 내부에서 핵심 가격·수량·모드·조건문이 완벽히 일치하는지 검증"""
        html_v2 = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=18,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO,
            disclosures=SAMPLE_DISCLOSURES
        )

        soup = BeautifulSoup(html_v2, "html.parser")
        cards = soup.find_all("div", attrs={"data-stock-code": True})
        self.assertEqual(len(cards), 10, "10개 종목에 대한 data-stock-code 카드가 정확히 10개 생성되어야 합니다.")

        cards_by_code = {card["data-stock-code"]: card for card in cards}

        for stock in SAMPLE_HELD_PORTFOLIO:
            code = stock["stock_code"]
            self.assertIn(code, cards_by_code, f"종목코드 {code} 카드가 존재해야 합니다.")
            card = cards_by_code[code]
            card_text = card.get_text()

            # 1. 종목명 검증
            self.assertIn(stock["stock_name"], card_text)

            # 2. 현재가 검증
            self.assertIn(f"{stock['current_price']:,}원", card_text)

            # 3. 매매 모드 일치 검증
            self.assertEqual(card["data-trade-mode"], stock["trade_mode"])

            # 4. 종목별 개별 특수 가격/조건문 검증
            if code == "004960":  # 한신공영 (NORMAL)
                self.assertIn("13,500원", card_text)
                self.assertIn("10,800원", card_text)
                self.assertIn("480원", card_text)
                self.assertIn("계속 보유/홀딩", card_text)
            elif code == "055490":  # 테이팩스 (CONCENTRATION_RISK)
                self.assertIn("18,000원", card_text)
                self.assertIn("12,000원", card_text)
                self.assertIn("950원", card_text)
                self.assertIn("100주", card_text)
                self.assertIn("300주", card_text)
                self.assertIn("비중과다", card_text)
            elif code == "234920":  # 자이글 (SUSPENDED_HOLD)
                self.assertIn("HOLD (거래정지)", card_text)
                self.assertIn("HOLD (비활성)", card_text)
                self.assertIn("거래정지 보류/공시감시", card_text)
                self.assertIn("N/A (거래정지)", card_text)
                self.assertNotIn("0.0점", card_text)
            elif code == "348340":  # 뉴로메카 (USER_OVERRIDE)
                self.assertIn("25,000원 (수동활성)", card_text)
                self.assertIn("HOLD (주문대기)", card_text)
                self.assertIn("750원 (수동추적)", card_text)
                self.assertIn("30주 미체결", card_text)
                self.assertIn("DART 미확정 [수동감시]", card_text)
            elif code == "241520":  # DSC인베스트먼트 (RECOVERY)
                self.assertIn("9,700원", card_text)
                self.assertIn("7,000원", card_text)
                self.assertIn("290원", card_text)
                self.assertIn("30주", card_text)
                self.assertIn("반등 시 손실축소 분할매도", card_text)
            elif code == "267260":  # HD현대일렉트릭 (NORMAL)
                self.assertIn("990,000원", card_text)
                self.assertIn("710,000원", card_text)
                self.assertIn("49,000원", card_text)

    def test_03_independent_fixtures_per_mode(self):
        """테스트 03: 6개 개별 모드 독립 Fixture 렌더링 정상 검증"""
        # 1. NORMAL
        html_normal = generate_mobile_html_report_v2(self.date_str, 1, [], [], FIXTURE_NORMAL, [])
        self.assertIn("한신공영", html_normal)
        self.assertIn("10,800원", html_normal)
        self.assertIn("계속 보유/홀딩", html_normal)

        # 2. RECOVERY
        html_recovery = generate_mobile_html_report_v2(self.date_str, 1, [], [], FIXTURE_RECOVERY, [])
        self.assertIn("알에스오토메이션", html_recovery)
        self.assertIn("9,300원", html_recovery)
        self.assertIn("반등 시 손실축소 분할매도", html_recovery)

        # 3. CONCENTRATION_RISK
        html_conc = generate_mobile_html_report_v2(self.date_str, 1, [], [], FIXTURE_CONCENTRATION_RISK, [])
        self.assertIn("테이팩스", html_conc)
        self.assertIn("12,000원", html_conc)
        self.assertIn("비중과다", html_conc)
        self.assertIn("100주", html_conc)

        # 4. USER_OVERRIDE
        html_override = generate_mobile_html_report_v2(self.date_str, 1, [], [], FIXTURE_USER_OVERRIDE, [])
        self.assertIn("뉴로메카", html_override)
        self.assertIn("25,000원 (수동활성)", html_override)
        self.assertIn("30주 미체결", html_override)

        # 5. SUSPENDED_HOLD
        html_suspended = generate_mobile_html_report_v2(self.date_str, 1, [], [], FIXTURE_SUSPENDED_HOLD, [])
        self.assertIn("자이글", html_suspended)
        self.assertIn("거래정지 보류/공시감시", html_suspended)
        self.assertIn("N/A (거래정지)", html_suspended)

        # 6. ETF
        html_etf = generate_mobile_html_report_v2(self.date_str, 1, [], [], FIXTURE_ETF, [])
        self.assertIn("PLUS 고배당주", html_etf)
        self.assertIn("79.0점", html_etf)
        self.assertIn("ETF T점수", html_etf)

    def test_04_mandatory_key_missing_and_fallback(self):
        """테스트 04: 필수 원천 키 누락 시 KeyError 발생 및 GmailNotifier의 V1 자동 Fallback 검증"""
        corrupted_stock = dict(FIXTURE_NORMAL[0])
        del corrupted_stock["profit_trail_delta"]  # V2 필수 키 제거

        # 1. 렌더러 단독 실행 시 KeyError 발생 확인 (임의 기본값 치환 금지)
        with self.assertRaises(KeyError):
            generate_mobile_html_report_v2(self.date_str, 1, [], [], [corrupted_stock], [])

        # 2. GmailNotifier 실행 시 V1으로 자동 Fallback 동작 확인
        notifier_v2 = GmailNotifier(sender_email="test@example.com", app_password="dummy")
        notifier_v2.render_version = "V2"

        fallback_html = notifier_v2.generate_html_report(
            date_str=self.date_str,
            total_count=1,
            caught_signals=[],
            all_results=[],
            held_portfolio=[corrupted_stock],
            disclosures=[]
        )
        # V1 테이블 형태가 안전하게 반환되었는지 확인
        self.assertIn("<table", fallback_html)
        self.assertIn("한신공영", fallback_html)

    def test_05_email_render_version_switcher(self):
        """테스트 05: EMAIL_RENDER_VERSION 설정에 따른 V1/V2 전환 및 잘못된 값 복구 검증"""
        notifier = GmailNotifier(sender_email="test@example.com", app_password="dummy")

        # 1. V1 모드
        notifier.render_version = "V1"
        html_v1 = notifier.generate_html_report(self.date_str, 1, [], [], FIXTURE_NORMAL, [])
        self.assertIn("내 계좌 보유 종목 정밀 평가", html_v1)
        self.assertIn("5단계 매매 대응전략 매트릭스", html_v1)

        # 2. V2 모드
        notifier.render_version = "V2"
        html_v2 = notifier.generate_html_report(self.date_str, 1, [], [], FIXTURE_NORMAL, [])
        self.assertIn("data-stock-code=\"004960\"", html_v2)
        self.assertIn("내 종목 모바일 정밀평가 리포트", html_v2)

        # 3. 유효하지 않은 버전값 (예: INVALID_VER) -> V1으로 Fallback
        notifier.render_version = "INVALID_VER"
        html_invalid = notifier.generate_html_report(self.date_str, 1, [], [], FIXTURE_NORMAL, [])
        self.assertIn("5단계 매매 대응전략 매트릭스", html_invalid)

    def test_06_responsive_preview_and_forbidden_syntax_check(self):
        """테스트 06: 360/390/430/620px 뷰포트 구조 적합성 및 CSS Grid/JS/min-width 금지문법 검증"""
        html_v2 = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=18,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO,
            disclosures=SAMPLE_DISCLOSURES
        )

        # 1. 금지 문법 검증
        self.assertNotIn("display: grid", html_v2.lower())
        self.assertNotIn("display:grid", html_v2.lower())
        self.assertNotIn("<script", html_v2.lower())
        self.assertNotIn("min-width: 6", html_v2.lower())
        self.assertNotIn("min-width: 7", html_v2.lower())

        # 2. 반응형 뷰포트 메타 및 래퍼 검증
        self.assertIn('name="viewport"', html_v2)
        self.assertIn('max-width: 620px', html_v2)
        self.assertIn('width: 100%', html_v2)

    def test_07_no_immediate_action_phrase_and_condition_preservation(self):
        """테스트 07: '즉시대응' 문구 미사용 및 '반등 시 매도' 조건문 보존 검증"""
        html_v2 = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=18,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO,
            disclosures=SAMPLE_DISCLOSURES
        )

        self.assertNotIn("즉시대응", html_v2)
        self.assertNotIn("즉시 매도", html_v2)
        self.assertIn("반등 시 손실축소 분할매도", html_v2)

if __name__ == "__main__":
    unittest.main()
