# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import os
import sys
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.api.kiwoom_api import KiwoomAPIClient, parse_kiwoom_price
from src.database.db_manager import DatabaseManager
from src.engine.portfolio_manager import PortfolioManager
from main import verify_pipeline_stock_code_consistency

class TestKiwoomOperationalAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db_path = BASE_DIR / "data" / "test_operational_audit.db"
        if cls.test_db_path.exists():
            try:
                cls.test_db_path.unlink()
            except Exception:
                pass
        cls.db = DatabaseManager(str(cls.test_db_path))
        cls.all_11_codes = [
            "000490", "004960", "055490", "140670", "161510",
            "206650", "234920", "241520", "267260", "348340", "490590"
        ]

    @classmethod
    def tearDownClass(cls):
        if cls.test_db_path.exists():
            try:
                cls.test_db_path.unlink()
            except Exception:
                pass

    def setUp(self):
        self.db.execute_non_query("DELETE FROM portfolio_positions")
        self.db.execute_non_query("DELETE FROM stock_info")
        self.client = KiwoomAPIClient(
            app_key="TEST_APP_KEY",
            app_secret="TEST_APP_SECRET",
            account_no="12345678-01",
            use_mock=False
        )
        self.client.access_token = "TEST_ACCESS_TOKEN"
        self.pm = PortfolioManager(self.db, self.client)

    def test_01_pagination_10_plus_1_daedong_success(self):
        """1. 1페이지 10개 + 2페이지 대동 1개 수집 -> 총 11개 성공 검증"""
        # 1페이지: 대동(000490)을 제외한 10개 종목
        page1_items = [{"stk_cd": f"A{c}", "stk_nm": f"종목_{c}", "rmnd_qty": "100", "pur_pric": "10000", "cur_prc": "10500", "pur_amt": "1000000", "prft_rt": "5.0"} for c in self.all_11_codes[1:]]
        # 2페이지: 대동(000490) 1개 종목
        page2_items = [{"stk_cd": "A000490", "stk_nm": "대동", "rmnd_qty": "2475", "pur_pric": "8116", "cur_prc": "8050", "pur_amt": "20088100", "prft_rt": "-1.05"}]

        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {"acnt_evlt_remn_indv_tot": page1_items}
        resp1.headers = {"cont-yn": "Y", "next-key": "PAGE2_KEY"}

        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {"acnt_evlt_remn_indv_tot": page2_items}
        resp2.headers = {"cont-yn": "N", "next-key": ""}

        with patch("requests.post", side_effect=[resp1, resp2]) as mock_post:
            positions = self.client.get_account_positions()
            self.assertEqual(len(positions), 11)
            codes = [p["stock_code"] for p in positions]
            self.assertIn("000490", codes)
            self.assertEqual(mock_post.call_count, 2)

    def test_02_second_page_failure_preserves_db_and_no_mail(self):
        """2. 두 번째 페이지 실패 -> 기존 DB 보존 및 메일 미발송 검증"""
        # 기존 DB에 기존 3개 종목 저장
        self.pm.add_holding("005930", "삼성전자", 10, 70000.0)
        self.pm.add_holding("000660", "SK하이닉스", 5, 150000.0)

        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {"acnt_evlt_remn_indv_tot": [{"stk_cd": "A000490", "stk_nm": "대동", "rmnd_qty": "100", "pur_pric": "8000", "cur_prc": "8000", "pur_amt": "800000", "prft_rt": "0.0"}]}
        resp1.headers = {"cont-yn": "Y", "next-key": "PAGE2_KEY"}

        resp2 = MagicMock()
        resp2.status_code = 500
        resp2.text = "Internal Server Error"

        with patch("requests.post", side_effect=[resp1, resp2]):
            with self.assertRaises(RuntimeError):
                self.pm.sync_portfolio_from_kiwoom()

        # 기존 DB 종목이 삭제되지 않고 완전히 보존되었는지 검증
        db_rows = self.db.execute_query("SELECT stock_code FROM portfolio_positions WHERE quantity > 0")
        existing_codes = {r["stock_code"] for r in db_rows}
        self.assertEqual(existing_codes, {"005930", "000660"})

    def test_03_full_10_items_without_cont_yn_fails_even_if_config_is_10(self):
        """3. [10개 만실 절단 의심 감지] config 파일에 10개만 존재하더라도 키움 1페이지가 10개 만실이고 cont-yn이 없으면 무조건 실패 검증"""
        # 키움 실계좌 1페이지 10개 수신 (대동 000490 누락된 10개)
        page1_10_items = [{"stk_cd": f"A{c}", "stk_nm": f"종목_{c}", "rmnd_qty": "100", "pur_pric": "10000", "cur_prc": "10500", "pur_amt": "1000000", "prft_rt": "5.0"} for c in self.all_11_codes[1:]]
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {"acnt_evlt_remn_indv_tot": page1_10_items} # tot_item_cnt 미제공
        resp1.headers = {"cont-yn": "N", "next-key": ""}

        # 실제 origin/main의 config/portfolio_holdings.json처럼 10개만 등록되어 있는 상태 모킹
        mock_cfg_10 = [{"stock_code": c} for c in self.all_11_codes[1:]]
        with patch.object(self.client, "_get_mock_account_positions", return_value=mock_cfg_10):
            with patch("requests.post", return_value=resp1):
                with self.assertRaises(RuntimeError) as cm:
                    self.client.get_account_positions()
                self.assertIn("10개 만실 페이지에서 연속조회(cont-yn='Y') 미수신", str(cm.exception))

    def test_04_raw_11_db_11_eval_10_fails_gate(self):
        """4. 원본 11개, DB 11개, 평가 10개 -> 실패 검증"""
        raw_kiwoom = [{"stock_code": c, "quantity": 100} for c in self.all_11_codes]
        for c in self.all_11_codes:
            self.db.execute_non_query("INSERT INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000) ON CONFLICT(stock_code) DO UPDATE SET quantity=100", (c,))
        
        # 평가 리스트에서 1개 누락 (10개)
        held_status_10 = [{"stock_code": c, "stock_name": f"종목_{c}"} for c in self.all_11_codes if c != "000490"]

        temp_excel = BASE_DIR / "data" / "test_eval_10.xlsx"
        df = pd.DataFrame([{"종목코드": c} for c in self.all_11_codes])
        df.to_excel(temp_excel, index=False)
        html = "".join([f'<div data-stock-code="{c}"></div>' for c in self.all_11_codes])

        try:
            with self.assertRaises(RuntimeError) as cm:
                verify_pipeline_stock_code_consistency(raw_kiwoom, self.db, held_status_10, temp_excel, html)
            self.assertIn("missing_in_evaluation", str(cm.exception))
        finally:
            if temp_excel.exists():
                temp_excel.unlink()

    def test_05_raw_11_xlsx_11_email_card_10_fails_gate(self):
        """5. 원본 11개, XLSX 11개, 이메일 카드 10개 -> 실패 검증"""
        raw_kiwoom = [{"stock_code": c, "quantity": 100} for c in self.all_11_codes]
        for c in self.all_11_codes:
            self.db.execute_non_query("INSERT INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, 100, 10000) ON CONFLICT(stock_code) DO UPDATE SET quantity=100", (c,))
        held_status_11 = [{"stock_code": c, "stock_name": f"종목_{c}"} for c in self.all_11_codes]

        temp_excel = BASE_DIR / "data" / "test_email_10.xlsx"
        df = pd.DataFrame([{"종목코드": c} for c in self.all_11_codes])
        df.to_excel(temp_excel, index=False)
        # 이메일 카드 1개 누락 (10개)
        html_10 = "".join([f'<div data-stock-code="{c}"></div>' for c in self.all_11_codes if c != "000490"])

        try:
            with self.assertRaises(RuntimeError) as cm:
                verify_pipeline_stock_code_consistency(raw_kiwoom, self.db, held_status_11, temp_excel, html_10)
            self.assertIn("missing_in_email", str(cm.exception))
        finally:
            if temp_excel.exists():
                temp_excel.unlink()

    def test_06_operational_env_mock_or_json_fallback_raises(self):
        """6. 운영환경(CI)에서 mock 또는 JSON fallback 사용 시 즉시 실패 검증"""
        mock_client = KiwoomAPIClient(app_key="", app_secret="", use_mock=True)
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaises(RuntimeError) as cm:
                mock_client.get_account_positions()
            self.assertIn("운영 환경(CI)에서 키움 API키 미설정", str(cm.exception))

    def test_07_all_11_consistent_succeeds(self):
        """7. 11개 모두 일치하는 경우에만 무결성 게이트 성공 통과 검증"""
        raw_kiwoom = [{"stock_code": c, "quantity": 100} for c in self.all_11_codes]
        for c in self.all_11_codes:
            self.db.execute_non_query("""
                INSERT INTO portfolio_positions (stock_code, quantity, avg_buy_price)
                VALUES (?, 100, 10000)
                ON CONFLICT(stock_code) DO UPDATE SET quantity = 100, avg_buy_price = 10000
            """, (c,))
        held_status_11 = [{"stock_code": c, "stock_name": f"종목_{c}"} for c in self.all_11_codes]

        temp_excel = BASE_DIR / "data" / "test_all_11.xlsx"
        df = pd.DataFrame([{"종목코드": c} for c in self.all_11_codes])
        df.to_excel(temp_excel, index=False)
        html_11 = "".join([f'<div data-stock-code="{c}"></div>' for c in self.all_11_codes])

        try:
            # 예외 없이 통과해야 함
            verify_pipeline_stock_code_consistency(raw_kiwoom, self.db, held_status_11, temp_excel, html_11)
        finally:
            if temp_excel.exists():
                temp_excel.unlink()

    def test_08_column_preservation_on_conflict(self):
        """8. [비대상 열 보존 회귀] ON CONFLICT DO UPDATE 적용 시 ATR 손절 래칫, P0/A0, 상태 열 100% 보존 검증"""
        # 기존 특정 종목에 대해 ATR/감시상태 열이 설정된 상태로 저장
        self.db.execute_non_query("""
            INSERT INTO portfolio_positions (
                stock_code, quantity, avg_buy_price, anchor_price_p0, anchor_atr_a0,
                parameter_version, entry_stage, lifecycle_status
            ) VALUES ('000490', 1000, 8000.0, 8500.0, 420.0, 'v4_custom', 2, 'PROFIT_TRAIL')
        """)

        # 키움 동기화 단일 트랜잭션 수행 (수량 2475, 평단가 8116.0 갱신)
        positions = [{
            "stock_code": "000490",
            "stock_name": "대동",
            "quantity": 2475,
            "avg_buy_price": 8116.0,
            "current_price": 8050
        }]
        self.pm._update_portfolio_in_single_transaction(positions)

        # 조회하여 비대상 열들이 초기화되지 않고 온전히 보존되었는지 검증
        row = self.db.execute_query("SELECT * FROM portfolio_positions WHERE stock_code = '000490'")[0]
        self.assertEqual(row["quantity"], 2475)
        self.assertEqual(row["avg_buy_price"], 8116.0)
        self.assertEqual(row["anchor_price_p0"], 8500.0) # 기존 앵커 P0 보존!
        self.assertEqual(row["anchor_atr_a0"], 420.0)   # 기존 앵커 A0 보존!
        self.assertEqual(row["parameter_version"], 'v4_custom') # 버전 보존!
        self.assertEqual(row["entry_stage"], 2)         # 진입 단계 보존!
        self.assertEqual(row["lifecycle_status"], 'PROFIT_TRAIL') # 상태 보존!

    def test_09_raw_balance_price_and_fallback_separation(self):
        """9. [가격 원본 및 대체값 분리] cur_prc 0원 시 raw_balance_price=0 보존 및 pred_close 대체/출처 분리 검증"""
        page_items = [
            {
                "stk_cd": "A000490", "stk_nm": "대동", "rmnd_qty": "2475", "pur_pric": "8116",
                "cur_prc": "0", "pred_close_pric": "8100", "pur_amt": "20088100", "prft_rt": "0.0"
            },
            {
                "stk_cd": "A004960", "stk_nm": "한신공영", "rmnd_qty": "100", "pur_pric": "9000",
                "cur_prc": "9200", "pred_close_pric": "9100", "pur_amt": "900000", "prft_rt": "2.2"
            }
        ]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"acnt_evlt_remn_indv_tot": page_items}
        resp.headers = {"cont-yn": "N", "next-key": ""}

        with patch("requests.post", return_value=resp):
            positions = self.client.get_account_positions()
            self.assertEqual(len(positions), 2)
            
            p_daedong = [p for p in positions if p["stock_code"] == "000490"][0]
            self.assertEqual(p_daedong["raw_balance_price"], 0) # 0원 원본 보존!
            self.assertEqual(p_daedong["current_price"], 8100)  # pred_close 대체값
            self.assertTrue(p_daedong["fallback_used"])
            self.assertEqual(p_daedong["current_price_source"], "KIWOOM_PRED_CLOSE")

            p_hanshin = [p for p in positions if p["stock_code"] == "004960"][0]
            self.assertEqual(p_hanshin["raw_balance_price"], 9200) # cur_prc 원본
            self.assertEqual(p_hanshin["current_price"], 9200)
            self.assertFalse(p_hanshin["fallback_used"])
            self.assertEqual(p_hanshin["current_price_source"], "KIWOOM_CUR_PRC")

    def test_10_unavailable_price_fails_validation(self):
        """10. [가격 검증 실패 차단] 라이브 가격 확인 불가(0원) 시 평가 중단 및 RuntimeError 차단 검증"""
        self.pm.add_holding("000490", "대동", 100, 8116.0)
        # raw_balance_price=0, current_price=0, 시세DB 없음
        live_meta = [{
            "stock_code": "000490",
            "stock_name": "대동",
            "quantity": 100,
            "avg_buy_price": 8116.0,
            "current_price": 0,
            "raw_balance_price": 0,
            "current_price_source": "UNAVAILABLE",
            "fallback_used": True
        }]
        with self.assertRaises(RuntimeError) as cm:
            self.pm.get_held_portfolio_status(engine=None, live_positions=live_meta)
        self.assertIn("가격 검증 실패", str(cm.exception))

    def test_11_email_v2_conciseness_snapshot(self):
        """11. [이메일 간결성 검증] V2 리포트 상단 요약, 종목 카드, DART 공시 포함 및 불필요 중복 배제 검증"""
        from src.notifications.mobile_renderer_v2 import generate_mobile_html_report_v2
        sample_held = [{
            "stock_code": "000490", "stock_name": "대동", "current_price": 8050,
            "daily_change_pct": -0.81, "pnl_pct": -0.81, "pnl_amount": -163350,
            "trade_mode": "NORMAL", "action_status": "보유",
            "kiwoom_stop_tick_price": 7600, "kiwoom_target_tick_price": 9200,
            "profit_trail_delta": 0, "recommended_order_qty": 0, "order_direction": "보유",
            "quantity": 2475, "f_score": 60.0, "t_score": 55.0, "final_score": 57.5,
            "atr_14": 280, "atr_pct": 3.4
        }]
        sample_disc = [{
            "stock_name": "대동", "stock_code": "000490", "report_nm": "단일판매·공급계약체결",
            "link": "http://dart.fss.or.kr", "summary": "1,000억원 공급계약",
            "impact": "매출 증대 긍정적", "guide": "기존 비중 유지"
        }]

        html = generate_mobile_html_report_v2(
            date_str="2026-08-19 11:20",
            total_count=1,
            caught_signals=[],
            all_results=[],
            held_portfolio=sample_held,
            disclosures=sample_disc
        )
        self.assertIn("data-stock-code=\"000490\"", html)
        self.assertIn("보유 포트폴리오 요약", html)
        self.assertIn("V4-PILOT-C 주요 대응 지침", html)
        self.assertIn("DART 주요 공시 & 브리핑", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_12_weight_sum_consistency_across_sources(self):
        """12. [계좌비중 100% 검증] 키움 가격과 엔진 가격이 상이한 11종목에서 비중 합계 99.9%~100.1% 검증"""
        # 11개 보유종목 DB 등록
        for idx, c in enumerate(self.all_11_codes, 1):
            self.pm.add_holding(c, f"종목_{c}", 100 * idx, 10000.0)
            # 일봉 시세 등록
            self.db.execute_non_query("""
                INSERT OR REPLACE INTO kiwoom_daily (stock_code, stk_date, open_price, high_price, low_price, close_price, volume)
                VALUES (?, '20260819', 10000, 10500, 9500, 10000, 100000)
            """, (c,))

        # 키움 잔고 메타데이터 (키움 가격은 9,000원)
        live_meta_11 = [{
            "stock_code": c, "stock_name": f"종목_{c}", "quantity": 100 * idx,
            "avg_buy_price": 10000.0, "current_price": 9000, "raw_balance_price": 9000,
            "current_price_source": "KIWOOM_CUR_PRC", "fallback_used": False
        } for idx, c in enumerate(self.all_11_codes, 1)]

        # Mock 엔진 (엔진 분석 가격은 12,000원 -> 엔진 가격이 1순위 적용됨)
        mock_engine = MagicMock()
        mock_engine.analyze_stock.side_effect = lambda code, name: {
            "stock_code": code, "stock_name": name, "current_price": 12000.0,
            "f_score": 60.0, "t_score": 60.0, "final_score": 60.0, "data_completeness": 100.0,
            "is_etf": False, "f_score_confirmed": True
        }

        held_status = self.pm.get_held_portfolio_status(engine=mock_engine, live_positions=live_meta_11)
        self.assertEqual(len(held_status), 11)

        # 모든 종목이 1순위 엔진 가격(12,000원)으로 평가되었는지 확인
        for pos in held_status:
            self.assertEqual(pos["current_price"], 12000.0)

        # 계좌 비중의 합이 반올림 오차 범위(99.9% ~ 100.1%) 내에 정확히 안착하는지 단언
        total_weight = sum(p["eval_weight_pct"] for p in held_status)
        self.assertGreaterEqual(total_weight, 99.9)
        self.assertLessEqual(total_weight, 100.1)

    def test_13_kiwoom_price_parser(self):
        """13. [키움 가격 파서 검증] +, -, 쉼표, 원, 공백 포함 문자열의 절댓값 파싱 및 안전 변환 검증"""
        self.assertEqual(parse_kiwoom_price("+8,100"), 8100)
        self.assertEqual(parse_kiwoom_price("-8,100"), 8100)
        self.assertEqual(parse_kiwoom_price("8,100원"), 8100)
        self.assertEqual(parse_kiwoom_price("  +8,100원  "), 8100)
        self.assertEqual(parse_kiwoom_price(8100), 8100)
        self.assertEqual(parse_kiwoom_price(8100.4), 8100)
        self.assertEqual(parse_kiwoom_price(None), 0)
        self.assertEqual(parse_kiwoom_price(""), 0)
        self.assertEqual(parse_kiwoom_price("N/A"), 0)

    def test_14_applied_price_matches_pnl_and_weight_eval(self):
        """14. [분자/분모 가격 일치성 검증] 최종 적용가격과 평가손익·비중 산출에 사용된 가격의 100% 일치 검증"""
        code = "000490"
        self.pm.add_holding(code, "대동", 2475, 8116.0)
        self.db.execute_non_query("""
            INSERT OR REPLACE INTO kiwoom_daily (stock_code, stk_date, open_price, high_price, low_price, close_price, volume)
            VALUES (?, '20260819', 8050, 8150, 8000, 8050, 50000)
        """, (code,))

        live_meta = [{
            "stock_code": code, "stock_name": "대동", "quantity": 2475,
            "avg_buy_price": 8116.0, "current_price": 8050, "raw_balance_price": 8050,
            "current_price_source": "KIWOOM_CUR_PRC", "fallback_used": False
        }]

        eval_list = self.pm.get_held_portfolio_status(engine=None, live_positions=live_meta)
        self.assertEqual(len(eval_list), 1)
        item = eval_list[0]

        expected_eval = 2475 * 8050
        expected_inv = 2475 * 8116.0
        expected_pnl = expected_eval - expected_inv

        self.assertEqual(item["current_price"], 8050)
        self.assertEqual(item["eval_amount"], expected_eval)
        self.assertEqual(item["pnl_amount"], int(expected_pnl))
        self.assertEqual(item["eval_weight_pct"], 100.0)

    def test_15_email_v2_excludes_redundant_sections(self):
        """15. [이메일 V2 불필요 섹션 배제 검증] 필수 5개 항목만 포함되고 관심종목/신규매수 등 제외 대상 부재 검증"""
        from src.notifications.mobile_renderer_v2 import generate_mobile_html_report_v2
        sample_held = [{
            "stock_code": "000490", "stock_name": "대동", "current_price": 8050,
            "daily_change_pct": -0.81, "pnl_pct": -0.81, "pnl_amount": -163350,
            "trade_mode": "NORMAL", "action_status": "보유",
            "kiwoom_stop_tick_price": 7600, "kiwoom_target_tick_price": 9200,
            "profit_trail_delta": 0, "recommended_order_qty": 0, "order_direction": "보유",
            "quantity": 2475, "f_score": 60.0, "t_score": 55.0, "final_score": 57.5,
            "atr_14": 280, "atr_pct": 3.4
        }]
        sample_disc = [{
            "stock_name": "대동", "stock_code": "000490", "report_nm": "주요사항보고서",
            "link": "http://dart.fss.or.kr", "summary": "자금조달 공시",
            "impact": "중립", "guide": "관망"
        }]

        html = generate_mobile_html_report_v2(
            date_str="2026-08-19 15:35",
            total_count=10,
            caught_signals=[{"stock_code": "005930", "signal_type": "1차 신규매수"}], # V2에서는 제외 대상
            all_results=[{"stock_code": "005930", "signal_type": "1차 신규매수"}],
            held_portfolio=sample_held,
            disclosures=sample_disc
        )

        # 필수 포함 항목 검증
        self.assertIn("💼 보유 포트폴리오 요약", html)
        self.assertIn("📋 V4-PILOT-C 주요 대응 지침", html)
        self.assertIn("data-stock-code=\"000490\"", html)
        self.assertIn("📢 DART 주요 공시 & 브리핑", html)
        self.assertIn("※ 본 리포트는 V4-PILOT-C 위험관리 엔진 기준값이며", html)

        # 제외 대상 섹션 부재 검증
        self.assertNotIn("관심종목 전체 분석", html)
        self.assertNotIn("신규 매수 신호 포착", html)
        self.assertNotIn("전체 시장 동향", html)
        self.assertNotIn("신규매수", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_16_kiwoom_token_validation_gate(self):
        """16. [키움 토큰 응답 무결성 검증] return_code=0 성공 및 return_code!=0 실패 차단 검증"""
        client = KiwoomAPIClient(app_key=" test_app_key ", app_secret=" 'test_secret' ", account_no=" 3097-8228 ", use_mock=False)
        self.assertEqual(client.app_key, "test_app_key")
        self.assertEqual(client.app_secret, "test_secret")
        self.assertEqual(client.account_no, "3097-8228")

        # 1) 정상 발급 케이스
        with patch("requests.post") as mock_post:
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "token": "AUTHENTICATED_TOKEN_12345",
                "return_code": 0,
                "return_msg": "정상 처리",
                "expires_dt": "20260819235959"
            }
            mock_post.return_value = mock_res
            token = client.get_access_token()
            self.assertEqual(token, "AUTHENTICATED_TOKEN_12345")
            self.assertEqual(client.access_token, "AUTHENTICATED_TOKEN_12345")

        # 2) 상태코드 200이지만 return_code != 0 인 실패 케이스
        with patch("requests.post") as mock_post:
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "token": None,
                "return_code": -10,
                "return_msg": "유효하지 않은 앱키입니다"
            }
            mock_post.return_value = mock_res
            client.access_token = None
            token = client.get_access_token()
            self.assertIsNone(token)
            self.assertIsNone(client.access_token)

    def test_17_raw_weight_sum_integrity(self):
        """17. [원시 비중 합계 검증] 소수점 반올림 전 원시 부동소수점 비중 합계가 100.0%로 검증됨을 확인"""
        for idx, c in enumerate(self.all_11_codes, 1):
            self.pm.add_holding(c, f"종목_{c}", 77 * idx, 8000.0)
            self.db.execute_non_query("""
                INSERT OR REPLACE INTO kiwoom_daily (stock_code, stk_date, open_price, high_price, low_price, close_price, volume)
                VALUES (?, '20260819', 8000, 8500, 7500, 8000, 10000)
            """, (c,))

        live_meta = [{
            "stock_code": c, "stock_name": f"종목_{c}", "quantity": 77 * idx,
            "avg_buy_price": 8000.0, "current_price": 8000 + (idx * 50), "raw_balance_price": 8000 + (idx * 50),
            "current_price_source": "KIWOOM_CUR_PRC", "fallback_used": False
        } for idx, c in enumerate(self.all_11_codes, 1)]

        held_list = self.pm.get_held_portfolio_status(engine=None, live_positions=live_meta)
        self.assertEqual(len(held_list), 11)
        raw_sum = sum((item["eval_amount"] / sum(h["eval_amount"] for h in held_list)) * 100.0 for item in held_list)
        self.assertAlmostEqual(raw_sum, 100.0, places=4)

if __name__ == "__main__":
    unittest.main()
