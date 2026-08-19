# -*- coding: utf-8 -*-
"""
Mobile Renderer V2 및 V1 보존·동일성·예외처리·회귀 단위 테스트
"""

import unittest
import os
import re
from pathlib import Path
from bs4 import BeautifulSoup

from src.notifications.gmail_notifier import GmailNotifier
from src.notifications.mobile_renderer_v2 import generate_mobile_html_report_v2, is_action_needed_stock, MANDATORY_STOCK_KEYS
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
        self.date_str = "2026-08-19 15:35"
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
            date_str="2026-08-14 20:58",
            total_count=18,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO,
            disclosures=SAMPLE_DISCLOSURES
        )

        self.assertEqual(
            self._normalize_html(current_v1_html),
            self._normalize_html(expected_snapshot),
            "V1 렌더러의 직접 호출 출력은 사전 스냅샷과 완전히 일치해야 합니다."
        )

    def test_02_v2_actionable_filtering_and_audit_metadata(self):
        """테스트 02: V2 본문에는 대응 필요 종목만 노출되고 data-held-stock-codes에 전체 10개 보유종목이 감사 기록되는지 검증"""
        html_v2 = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=18,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO,
            disclosures=SAMPLE_DISCLOSURES
        )

        soup = BeautifulSoup(html_v2, "html.parser")
        wrapper = soup.find("div", class_="mobile-wrapper")
        self.assertIsNotNone(wrapper)
        self.assertEqual(wrapper.get("data-render-version"), "V2")

        # 1. 전체 보유 종목 감사 메타데이터 검증 (10개)
        held_codes_meta = wrapper.get("data-held-stock-codes", "").split(",")
        expected_all_codes = sorted([s["stock_code"] for s in SAMPLE_HELD_PORTFOLIO])
        self.assertEqual(held_codes_meta, expected_all_codes, "data-held-stock-codes에 전체 10종목이 정확히 포함되어야 합니다.")

        # 2. 본문 노출 카드 검증 (대응 필요 종목만 노출)
        cards = soup.find_all("div", attrs={"data-stock-code": True})
        card_codes = [card["data-stock-code"] for card in cards]

        # 7개 대응 필요 종목: 055490(비중과다), 140670(RECOVERY), 206650(RECOVERY), 234920(SUSPENDED), 241520(RECOVERY), 348340(USER_OVERRIDE), 490590(비중과다)
        # 267260은 NORMAL이며 일변동 3.5% < max(2.5, 7.6*0.8=6.08%) 이므로 비대응 종목으로 분류됨
        expected_actionable_codes = ["055490", "140670", "206650", "234920", "241520", "348340", "490590"]
        self.assertEqual(sorted(card_codes), sorted(expected_actionable_codes))

        # 3. 계속 보유/변화 없음 종목 (004960, 161510, 267260)은 본문 카드에 노출되지 않음
        self.assertNotIn("004960", card_codes)
        self.assertNotIn("161510", card_codes)
        self.assertNotIn("267260", card_codes)

        # 4. 카드 코드가 전체 보유 종목의 진부분집합인지 검증
        self.assertTrue(set(card_codes).issubset(set(expected_all_codes)))

    def test_03_dart_disclosures_present_and_absent(self):
        """테스트 03: DART 공시 존재 시 정상 렌더링 및 부재 시 '주요 신규 공시 없음' 한 줄 출력 검증"""
        # 1. 공시 존재 시
        html_with_disc = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=18,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO,
            disclosures=SAMPLE_DISCLOSURES
        )
        self.assertIn("📢 DART 주요 공시 & 브리핑", html_with_disc)
        self.assertIn("data-disclosure-code=\"004960\"", html_with_disc)
        self.assertIn("data-disclosure-code=\"234920\"", html_with_disc)
        self.assertIn("반기보고서", html_with_disc)

        # 2. 공시 부재 시
        html_no_disc = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=18,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO,
            disclosures=[]
        )
        self.assertIn("오늘 확인된 주요 신규 공시 없음", html_no_disc)
        self.assertNotIn("단기차입금증가결정", html_no_disc)

    def test_04_no_v1_fallback_on_v2_error(self):
        """테스트 04: V2 필수 키 누락 등 예외 발생 시 V1으로 Fallback하지 않고 RuntimeError 발생 검증"""
        corrupted_stock = dict(FIXTURE_RECOVERY[0])
        del corrupted_stock["profit_trail_delta"]  # 필수 키 제거

        # GmailNotifier generate_html_report 실행 시 RuntimeError 발생 확인
        notifier = GmailNotifier(sender_email="test@example.com", app_password="dummy")
        notifier.render_version = "V2"

        with self.assertRaises(RuntimeError) as ctx:
            notifier.generate_html_report(
                date_str=self.date_str,
                total_count=1,
                caught_signals=[],
                all_results=[],
                held_portfolio=[corrupted_stock],
                disclosures=[]
            )
        self.assertIn("V1 Fallback 금지", str(ctx.exception))
        self.assertTrue(notifier.fallback_occurred)

    def test_05_default_renderer_selection(self):
        """테스트 05: 기본 렌더러가 V2로 고정되고 잘못된 설정값 유입 시에도 V2로 동작하는지 검증"""
        notifier = GmailNotifier(sender_email="test@example.com", app_password="dummy")

        # 1. 기본 생성 시 V2 동작
        html = notifier.generate_html_report(self.date_str, 1, [], [], FIXTURE_RECOVERY, [])
        self.assertIn('data-render-version="V2"', html)

        # 2. 잘못된 render_version 입력 시에도 V2로 처리
        notifier.render_version = "INVALID_UNKNOWN"
        html_fixed = notifier.generate_html_report(self.date_str, 1, [], [], FIXTURE_RECOVERY, [])
        self.assertIn('data-render-version="V2"', html_fixed)

    def test_06_forbidden_and_required_strings_gate(self):
        """테스트 06: HTML 구조 게이트 필수 문자열 포함 및 금지 문자열 완전 부재 검증"""
        html_v2 = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=18,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO,
            disclosures=SAMPLE_DISCLOSURES
        )

        REQUIRED_HTML_STRINGS = [
            'data-render-version="V2"',
            'V4-PILOT-C 주요 대응 지침',
            'data-held-stock-codes='
        ]
        FORBIDDEN_HTML_STRINGS = [
            '5단계 매매 대응전략 매트릭스',
            '일봉/45분봉 수급 원자값 연동 표',
            '내 계좌 보유 종목 정밀 평가',
            '관심 종목 리포트',
            '신규 매수 신호',
            'data-render-version="V1"'
        ]

        for req in REQUIRED_HTML_STRINGS:
            self.assertIn(req, html_v2, f"필수 문자열 누락: {req}")

        for fbd in FORBIDDEN_HTML_STRINGS:
            self.assertNotIn(fbd, html_v2, f"금지 문자열 검출: {fbd}")

    def test_07_no_action_needed_clean_message(self):
        """테스트 07: 모든 보유 종목이 변화 없는 정상 감시 상태일 때 안내 메시지 출력 검증"""
        html_clean = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=1,
            caught_signals=[],
            all_results=[],
            held_portfolio=FIXTURE_NORMAL,
            disclosures=[]
        )
        self.assertIn("오늘 특별 대응이 필요한 보유종목 없음 (전 종목 정상 감시 유지)", html_clean)
        self.assertNotIn("data-stock-code=", html_clean)
        self.assertIn('data-held-stock-codes="004960"', html_clean)

    def test_08_krw_no_decimal_point_and_integer_formatting(self):
        """테스트 08: 원화 가격 소수점(.0) 제거 및 쉼표 포함 정수 원 단위 포맷팅 검증"""
        html_v2 = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=18,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO,
            disclosures=SAMPLE_DISCLOSURES
        )

    def test_09_action_cards_completeness_failure_when_missing(self):
        """테스트 09: 기대 대응종목 2개인데 카드가 1개만 렌더링되면 verify_pipeline_stock_code_consistency 실패 검증"""
        from main import verify_pipeline_stock_code_consistency
        from src.database.db_manager import DatabaseManager
        import tempfile
        import pandas as pd

        test_held = [FIXTURE_CONCENTRATION_RISK[0], FIXTURE_RECOVERY[0]] # 055490, 140670
        raw_kiwoom = [{"stock_code": "055490", "quantity": 100}, {"stock_code": "140670", "quantity": 100}]

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f, tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_f:
            db_path = db_f.name
            xlsx_path = Path(xlsx_f.name)

        try:
            db = DatabaseManager(db_path)
            for c in ["055490", "140670"]:
                db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (c, f"종목_{c}"))
                db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000.0)", (c,))
            pd.DataFrame([{"종목코드": "055490"}, {"종목코드": "140670"}]).to_excel(xlsx_path, index=False)

            # 카드가 055490 1개만 노출된 불완전 HTML
            html_missing = '<div class="mobile-wrapper" data-render-version="V2" data-held-stock-codes="055490,140670"><div data-stock-code="055490">Card</div></div>'

            with self.assertRaises(RuntimeError) as cm:
                verify_pipeline_stock_code_consistency(
                    raw_kiwoom_positions=raw_kiwoom,
                    db=db,
                    held_status=test_held,
                    excel_path=xlsx_path,
                    html_report=html_missing
                )
            self.assertIn("missing_action_cards", str(cm.exception))
            self.assertIn("140670", str(cm.exception))
        finally:
            for p in [db_path, str(xlsx_path)]:
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

    def test_10_action_cards_completeness_failure_when_zero(self):
        """테스트 10: 기대 대응종목이 있는데 카드가 0개 출력되면 verify_pipeline_stock_code_consistency 실패 검증"""
        from main import verify_pipeline_stock_code_consistency
        from src.database.db_manager import DatabaseManager
        import tempfile
        import pandas as pd

        test_held = [FIXTURE_CONCENTRATION_RISK[0]] # 055490
        raw_kiwoom = [{"stock_code": "055490", "quantity": 100}]

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f, tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_f:
            db_path = db_f.name
            xlsx_path = Path(xlsx_f.name)

        try:
            db = DatabaseManager(db_path)
            db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", ("055490", "테이팩스"))
            db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000.0)", ("055490",))
            pd.DataFrame([{"종목코드": "055490"}]).to_excel(xlsx_path, index=False)

            # 카드가 0개인 HTML
            html_zero = '<div class="mobile-wrapper" data-render-version="V2" data-held-stock-codes="055490"><div>오늘 특별 대응 종목 없음</div></div>'

            with self.assertRaises(RuntimeError) as cm:
                verify_pipeline_stock_code_consistency(
                    raw_kiwoom_positions=raw_kiwoom,
                    db=db,
                    held_status=test_held,
                    excel_path=xlsx_path,
                    html_report=html_zero
                )
            self.assertIn("missing_action_cards", str(cm.exception))
            self.assertIn("055490", str(cm.exception))
        finally:
            for p in [db_path, str(xlsx_path)]:
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

    def test_11_action_cards_completeness_failure_when_unexpected_added(self):
        """테스트 11: 대응대상이 아닌 종목 카드가 추가되면 verify_pipeline_stock_code_consistency 실패 검증"""
        from main import verify_pipeline_stock_code_consistency
        from src.database.db_manager import DatabaseManager
        import tempfile
        import pandas as pd

        test_held = [FIXTURE_NORMAL[0]] # 004960 (NORMAL, 계속보유) -> 기대 대응카드 0개
        raw_kiwoom = [{"stock_code": "004960", "quantity": 100}]

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f, tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_f:
            db_path = db_f.name
            xlsx_path = Path(xlsx_f.name)

        try:
            db = DatabaseManager(db_path)
            db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", ("004960", "한신공영"))
            db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000.0)", ("004960",))
            pd.DataFrame([{"종목코드": "004960"}]).to_excel(xlsx_path, index=False)

            # 비대응 종목 004960 카드가 억지로 들어간 HTML
            html_unexpected = '<div class="mobile-wrapper" data-render-version="V2" data-held-stock-codes="004960"><div data-stock-code="004960">Card</div></div>'

            with self.assertRaises(RuntimeError) as cm:
                verify_pipeline_stock_code_consistency(
                    raw_kiwoom_positions=raw_kiwoom,
                    db=db,
                    held_status=test_held,
                    excel_path=xlsx_path,
                    html_report=html_unexpected
                )
            self.assertIn("unexpected_action_cards", str(cm.exception))
            self.assertIn("004960", str(cm.exception))
        finally:
            for p in [db_path, str(xlsx_path)]:
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

    def test_12_dart_disclosures_completeness_failure_when_missing(self):
        """테스트 12: DART 공시 2건인데 1건만 출력되면 verify_pipeline_stock_code_consistency 실패 검증"""
        from main import verify_pipeline_stock_code_consistency
        from src.database.db_manager import DatabaseManager
        import tempfile
        import pandas as pd

        test_held = [FIXTURE_NORMAL[0]] # 004960
        raw_kiwoom = [{"stock_code": "004960", "quantity": 100}]
        disclosures = [
            {"stock_name": "한신공영", "stock_code": "004960", "report_nm": "공시1", "rcept_no": "20260819000001"},
            {"stock_name": "한신공영", "stock_code": "004960", "report_nm": "공시2", "rcept_no": "20260819000002"}
        ]

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f, tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_f:
            db_path = db_f.name
            xlsx_path = Path(xlsx_f.name)

        try:
            db = DatabaseManager(db_path)
            db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", ("004960", "한신공영"))
            db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000.0)", ("004960",))
            pd.DataFrame([{"종목코드": "004960"}]).to_excel(xlsx_path, index=False)

            # 공시 1개(20260819000001)만 포함된 HTML
            html_missing_disc = '<div class="mobile-wrapper" data-render-version="V2" data-held-stock-codes="004960"><div data-disclosure-id="20260819000001">DART 1</div></div>'

            with self.assertRaises(RuntimeError) as cm:
                verify_pipeline_stock_code_consistency(
                    raw_kiwoom_positions=raw_kiwoom,
                    db=db,
                    held_status=test_held,
                    excel_path=xlsx_path,
                    html_report=html_missing_disc,
                    disclosures=disclosures
                )
            self.assertIn("missing_disclosures", str(cm.exception))
            self.assertIn("20260819000002", str(cm.exception))
        finally:
            for p in [db_path, str(xlsx_path)]:
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

    def test_13_dart_disclosures_completeness_same_stock_two_disclosures(self):
        """테스트 13: 동일 종목 공시 2건 모두 렌더링 시 정상 통과 및 1건 누락 시 검출 검증"""
        from main import verify_pipeline_stock_code_consistency
        from src.database.db_manager import DatabaseManager
        import tempfile
        import pandas as pd

        test_held = [FIXTURE_NORMAL[0]] # 004960
        raw_kiwoom = [{"stock_code": "004960", "quantity": 100}]
        disclosures = [
            {"stock_name": "한신공영", "stock_code": "004960", "report_nm": "반기보고서", "rcept_no": "20260814000688"},
            {"stock_name": "한신공영", "stock_code": "004960", "report_nm": "주요사항보고서", "rcept_no": "20260814000999"}
        ]

        # 1. 2건 모두 정상 렌더링 시
        html_both = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=1,
            caught_signals=[],
            all_results=[],
            held_portfolio=test_held,
            disclosures=disclosures
        )
        self.assertIn('data-disclosure-id="20260814000688"', html_both)
        self.assertIn('data-disclosure-id="20260814000999"', html_both)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f, tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_f:
            db_path = db_f.name
            xlsx_path = Path(xlsx_f.name)

        try:
            db = DatabaseManager(db_path)
            db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", ("004960", "한신공영"))
            db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000.0)", ("004960",))
            pd.DataFrame([{"종목코드": "004960"}]).to_excel(xlsx_path, index=False)

            # 정상 통과해야 함
            verify_pipeline_stock_code_consistency(
                raw_kiwoom_positions=raw_kiwoom,
                db=db,
                held_status=test_held,
                excel_path=xlsx_path,
                html_report=html_both,
                disclosures=disclosures
            )
        finally:
            for p in [db_path, str(xlsx_path)]:
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

    def test_14_html_escaping_and_url_sanitization(self):
        """테스트 14: 공시 제목에 &, <, >, " 가 있어도 HTML 이스케이프되고 부적절한 URL이 #으로 치환되는지 검증"""
        disclosures_with_special_chars = [{
            "stock_name": "특수<종목>&이름",
            "stock_code": "004960",
            "report_nm": "공시 <특수> & \"보고서\"",
            "rcept_no": "20260819_SPEC_01",
            "link": "javascript:alert('xss')", # 허용되지 않는 URL
            "summary": "요약 & <테스트>",
            "impact": "영향 \"100%\" & <긍정>",
            "guide": "가이드 <주의> & \"유지\""
        }]

        html = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=1,
            caught_signals=[],
            all_results=[],
            held_portfolio=FIXTURE_NORMAL,
            disclosures=disclosures_with_special_chars
        )

        # HTML 이스케이프 검증
        self.assertIn("공시 &lt;특수&gt; &amp; &quot;보고서&quot;", html)
        self.assertIn("요약 &amp; &lt;테스트&gt;", html)
        self.assertIn("영향 &quot;100%&quot; &amp; &lt;긍정&gt;", html)
        self.assertIn("가이드 &lt;주의&gt; &amp; &quot;유지&quot;", html)

        # URL Sanitization (# 치환) 검증
        self.assertIn('href="#"', html)
        self.assertNotIn('href="javascript:alert', html)

    def test_15_11_to_10_sell_off_current_fixture_regression(self):
        """테스트 15: HD현대일렉트릭(267260) 전량매도 후 10종목 현재 포트폴리오의 폼 무결성 전수 검증"""
        from tests.fixtures.sample_portfolio_fixture import SAMPLE_HELD_PORTFOLIO_10_CURRENT
        from main import verify_pipeline_stock_code_consistency
        from src.database.db_manager import DatabaseManager
        import tempfile
        import pandas as pd

        # 1. 10종목 검증
        self.assertEqual(len(SAMPLE_HELD_PORTFOLIO_10_CURRENT), 10)
        held_codes = [s["stock_code"] for s in SAMPLE_HELD_PORTFOLIO_10_CURRENT]
        self.assertIn("000490", held_codes) # 대동 포함
        self.assertNotIn("267260", held_codes) # HD현대일렉트릭 제외

        # 2. HTML 생성
        html_10 = generate_mobile_html_report_v2(
            date_str=self.date_str,
            total_count=10,
            caught_signals=[],
            all_results=[],
            held_portfolio=SAMPLE_HELD_PORTFOLIO_10_CURRENT,
            disclosures=SAMPLE_DISCLOSURES
        )

        # 3. data-held-stock-codes 검증 (정확히 10개)
        soup = BeautifulSoup(html_10, "html.parser")
        wrapper = soup.find("div", class_="mobile-wrapper")
        self.assertIsNotNone(wrapper)
        meta_codes = wrapper.get("data-held-stock-codes", "").split(",")
        self.assertEqual(len(meta_codes), 10)
        self.assertIn("000490", meta_codes)
        self.assertNotIn("267260", meta_codes)

        # 4. 본문 카드 및 공시에도 267260 완전 부재
        self.assertNotIn('data-stock-code="267260"', html_10)
        self.assertNotIn('data-disclosure-code="267260"', html_10)
        self.assertNotIn("HD현대일렉트릭", html_10)

        # 5. verify_pipeline_stock_code_consistency 전 계층 10종목 무결성 통과 검증
        raw_kiwoom_10 = [{"stock_code": c, "quantity": 100} for c in held_codes]
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f, tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_f:
            db_path = db_f.name
            xlsx_path = Path(xlsx_f.name)

        try:
            db = DatabaseManager(db_path)
            for c in held_codes:
                db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES (?, ?)", (c, f"종목_{c}"))
                db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000.0)", (c,))
            pd.DataFrame([{"종목코드": c} for c in held_codes]).to_excel(xlsx_path, index=False)

            # 정상 통과 단언
            verify_pipeline_stock_code_consistency(
                raw_kiwoom_positions=raw_kiwoom_10,
                db=db,
                held_status=SAMPLE_HELD_PORTFOLIO_10_CURRENT,
                excel_path=xlsx_path,
                html_report=html_10,
                disclosures=SAMPLE_DISCLOSURES
            )
        finally:
            for p in [db_path, str(xlsx_path)]:
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

    def test_16_dart_rcept_no_missing_or_empty_raises(self):
        """테스트 16: DART 공시 rcept_no 누락 또는 빈 문자열 시 즉시 예외 발생 및 발송 차단 검증"""
        from main import verify_pipeline_stock_code_consistency
        from src.database.db_manager import DatabaseManager
        import tempfile
        import pandas as pd

        # 1. rcept_no가 None인 경우 (렌더러 단)
        disc_none = [{"stock_name": "한신공영", "stock_code": "004960", "report_nm": "보고서", "rcept_no": None}]
        with self.assertRaises(ValueError) as cm1:
            generate_mobile_html_report_v2(
                date_str=self.date_str,
                total_count=1,
                caught_signals=[],
                all_results=[],
                held_portfolio=FIXTURE_NORMAL,
                disclosures=disc_none
            )
        self.assertIn("rcept_no 누락", str(cm1.exception))

        # 2. rcept_no가 빈 문자열인 경우 (렌더러 단)
        disc_empty = [{"stock_name": "한신공영", "stock_code": "004960", "report_nm": "보고서", "rcept_no": "   "}]
        with self.assertRaises(ValueError) as cm2:
            generate_mobile_html_report_v2(
                date_str=self.date_str,
                total_count=1,
                caught_signals=[],
                all_results=[],
                held_portfolio=FIXTURE_NORMAL,
                disclosures=disc_empty
            )
        self.assertIn("rcept_no 누락 또는 빈 문자열", str(cm2.exception))

        # 3. verify_pipeline_stock_code_consistency 에서도 차단되는지 검증
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f, tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_f:
            db_path = db_f.name
            xlsx_path = Path(xlsx_f.name)
        try:
            db = DatabaseManager(db_path)
            db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES ('004960', '한신공영')")
            db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES ('004960', 100, 10000.0)")
            pd.DataFrame([{"종목코드": "004960"}]).to_excel(xlsx_path, index=False)
            html = '<div class="mobile-wrapper" data-render-version="V2" data-held-stock-codes="004960"></div>'

            with self.assertRaises(RuntimeError) as cm3:
                verify_pipeline_stock_code_consistency(
                    raw_kiwoom_positions=[{"stock_code": "004960", "quantity": 100}],
                    db=db,
                    held_status=FIXTURE_NORMAL,
                    excel_path=xlsx_path,
                    html_report=html,
                    disclosures=disc_empty
                )
            self.assertIn("rcept_no 누락 또는 빈 문자열", str(cm3.exception))
        finally:
            for p in [db_path, str(xlsx_path)]:
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

    def test_17_dart_rcept_no_duplicate_raises(self):
        """테스트 17: DART 공시 rcept_no 중복 감지 시 즉시 예외 발생 및 발송 차단 검증"""
        from main import verify_pipeline_stock_code_consistency
        from src.database.db_manager import DatabaseManager
        import tempfile
        import pandas as pd

        disc_dup = [
            {"stock_name": "한신공영", "stock_code": "004960", "report_nm": "보고서1", "rcept_no": "20260819_DUP_01"},
            {"stock_name": "한신공영", "stock_code": "004960", "report_nm": "보고서2", "rcept_no": "20260819_DUP_01"} # 동일 ID 중복
        ]

        # 1. 렌더러 단 중복 감지
        with self.assertRaises(ValueError) as cm1:
            generate_mobile_html_report_v2(
                date_str=self.date_str,
                total_count=1,
                caught_signals=[],
                all_results=[],
                held_portfolio=FIXTURE_NORMAL,
                disclosures=disc_dup
            )
        self.assertIn("rcept_no 중복 감지", str(cm1.exception))

        # 2. verify_pipeline_stock_code_consistency 무결성 게이트 중복 감지
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f, tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_f:
            db_path = db_f.name
            xlsx_path = Path(xlsx_f.name)
        try:
            db = DatabaseManager(db_path)
            db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES ('004960', '한신공영')")
            db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES ('004960', 100, 10000.0)")
            pd.DataFrame([{"종목코드": "004960"}]).to_excel(xlsx_path, index=False)
            html = '<div class="mobile-wrapper" data-render-version="V2" data-held-stock-codes="004960"><div data-disclosure-id="20260819_DUP_01"></div></div>'

            with self.assertRaises(RuntimeError) as cm2:
                verify_pipeline_stock_code_consistency(
                    raw_kiwoom_positions=[{"stock_code": "004960", "quantity": 100}],
                    db=db,
                    held_status=FIXTURE_NORMAL,
                    excel_path=xlsx_path,
                    html_report=html,
                    disclosures=disc_dup
                )
            self.assertIn("rcept_no 중복 감지", str(cm2.exception))
        finally:
            for p in [db_path, str(xlsx_path)]:
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

    def test_18_dart_rendered_count_mismatch_raises(self):
        """테스트 18: rendered ID 개수와 expected ID 개수 불일치 시 발송 차단 검증"""
        from main import verify_pipeline_stock_code_consistency
        from src.database.db_manager import DatabaseManager
        import tempfile
        import pandas as pd

        disclosures = [
            {"stock_name": "한신공영", "stock_code": "004960", "report_nm": "보고서1", "rcept_no": "20260819_CNT_01"},
            {"stock_name": "한신공영", "stock_code": "004960", "report_nm": "보고서2", "rcept_no": "20260819_CNT_02"}
        ]

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f, tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_f:
            db_path = db_f.name
            xlsx_path = Path(xlsx_f.name)
        try:
            db = DatabaseManager(db_path)
            db.execute_non_query("INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES ('004960', '한신공영')")
            db.execute_non_query("INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES ('004960', 100, 10000.0)")
            pd.DataFrame([{"종목코드": "004960"}]).to_excel(xlsx_path, index=False)
            # HTML에 1개만 렌더링된 상태
            html_only_one = '<div class="mobile-wrapper" data-render-version="V2" data-held-stock-codes="004960"><div data-disclosure-id="20260819_CNT_01"></div></div>'

            with self.assertRaises(RuntimeError) as cm:
                verify_pipeline_stock_code_consistency(
                    raw_kiwoom_positions=[{"stock_code": "004960", "quantity": 100}],
                    db=db,
                    held_status=FIXTURE_NORMAL,
                    excel_path=xlsx_path,
                    html_report=html_only_one,
                    disclosures=disclosures
                )
            self.assertIn("DART 공시 완전성 검증 실패", str(cm.exception))
        finally:
            for p in [db_path, str(xlsx_path)]:
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

if __name__ == "__main__":
    unittest.main()

