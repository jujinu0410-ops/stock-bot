"""Display-only Policy Shadow regression tests (no operational DB or network)."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from src.notifications.mobile_renderer_v2 import generate_mobile_html_report_v2, format_t_3d
from src.policy.policy_shadow_store import PolicyShadowReader, PolicyShadowService, PolicyShadowStore
from src.utils.excel_exporter import create_analysis_excel_report
from tests.fixtures.sample_portfolio_fixture import SAMPLE_HELD_PORTFOLIO


class TestPolicyShadowDisplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "policy.db"
        self.store = PolicyShadowStore(self.path)
        self.service = PolicyShadowService(self.store)
        self.source = 0

    def tearDown(self):
        self.tmp.cleanup()

    def raw(self, code="000001", ts="2026-08-24 10:00:00", qty=10, avg=100.0, price=100.0, loss=0.0, f=70.0, t=50.0, is_etf=False):
        self.source += 1
        return {
            "asof_timestamp": ts, "source_identifier": f"display-{self.source}", "stock_code": code,
            "market_price": price, "quantity": qty, "weighted_avg_price": avg, "loss_pct": loss,
            "atr14": 10.0, "f_score": f, "t_score": t, "daily_state": "NEUTRAL",
            "is_45m_bearish_2plus": 0, "is_45m_breakdown": 0, "is_45m_bearish_gate": 0,
            "completed_45m_timestamp": "2026-08-24 09:45:00", "concentration_state": "CLEAR",
            "data_validity": 1, "suspension_state": "ACTIVE", "v4_effective_stop": 90.0,
            "is_etf": is_etf,
        }

    def observe(self, **kwargs):
        raw = self.raw(**kwargs)
        return self.service.observe(raw, [raw["stock_code"]])

    def payload(self, report="2026-08-24 10:30:00", qty=10, avg=100.0):
        return PolicyShadowReader(self.path).build_report_payload(report, [{"stock_code": "000001", "stock_name": "테스트", "quantity": qty, "avg_buy_price": avg}])

    def test_01_same_day_fresh_snapshot(self):
        self.observe(); self.assertEqual(self.payload()["rows"][0]["freshness_status"], "FRESH")
    def test_02_snapshot_at_60_minutes_is_fresh(self):
        self.observe(); self.assertTrue(self.payload("2026-08-24 11:00:00")["rows"][0]["display_allowed"])
    def test_03_snapshot_over_60_minutes_is_stale(self):
        self.observe(); self.assertEqual(self.payload("2026-08-24 11:01:00")["rows"][0]["freshness_status"], "STALE_POLICY_SNAPSHOT")
    def test_04_matching_quantity_is_match(self):
        self.observe(); self.assertEqual(self.payload()["rows"][0]["position_match_status"], "MATCH")
    def test_05_quantity_mismatch_is_stale_position(self):
        self.observe(); self.assertEqual(self.payload(qty=9)["rows"][0]["freshness_status"], "STALE_POSITION_STATE")
    def test_06_average_mismatch_is_stale_position(self):
        self.observe(); self.assertEqual(self.payload(avg=102)["rows"][0]["freshness_status"], "STALE_POSITION_STATE")
    def test_07_average_rounding_tolerance_is_match(self):
        self.observe(); self.assertEqual(self.payload(avg=100.05)["rows"][0]["position_match_status"], "MATCH")
    def test_08_zero_current_quantity_is_excluded_from_current_rows(self):
        self.observe(); p = PolicyShadowReader(self.path).build_report_payload("2026-08-24 10:30:00", [{"stock_code":"000001", "quantity":0, "avg_buy_price":100}]); self.assertEqual(p["rows"], [])
    def test_09_open_unheld_cycle_is_transition_pending(self):
        self.observe(); p = PolicyShadowReader(self.path).build_report_payload("2026-08-24 10:30:00", []); self.assertEqual(p["transition_rows"][0]["transition_status"], "POSITION_TRANSITION_PENDING_SHADOW")
    def test_10_missing_policy_db_is_unavailable(self):
        self.assertEqual(PolicyShadowReader(Path(self.tmp.name) / "absent.db").build_report_payload("2026-08-24 10:30:00", [])["status"], "UNAVAILABLE")
    def test_11_malformed_policy_db_is_unavailable(self):
        bad = Path(self.tmp.name) / "bad.db"; bad.write_text("not sqlite", encoding="utf-8"); self.assertEqual(PolicyShadowReader(bad).build_report_payload("2026-08-24 10:30:00", [])["status"], "UNAVAILABLE")
    def test_12_actual_order_impact_is_forced_zero(self):
        self.observe(); self.assertEqual(self.payload()["rows"][0]["actual_order_impact"], 0)
    def test_13_future_snapshot_is_not_selected(self):
        self.observe(ts="2026-08-24 11:00:00"); self.assertEqual(self.payload("2026-08-24 10:30:00")["rows"][0]["freshness_status"], "STALE_POLICY_SNAPSHOT")
    def test_14_current_holding_object_is_not_mutated(self):
        self.observe(); holding={"stock_code":"000001", "quantity":10, "avg_buy_price":100}; before=copy.deepcopy(holding); PolicyShadowReader(self.path).build_report_payload("2026-08-24 10:30:00", [holding]); self.assertEqual(holding, before)
    def test_15_renderer_shows_policy_compact_once_per_holding(self):
        h=copy.deepcopy(SAMPLE_HELD_PORTFOLIO); html=generate_mobile_html_report_v2("2026-08-24 15:35", 0, [], [], h, [], {"status":"OK","rows":[{"stock_code":"004960","stock_name":"한신공영","display_allowed":True,"position_kind":"LEGACY_POSITION","t_change_3d":None,"loss_defense_trigger":"OFF","defense_state":"OFF","actual_order_impact":0}]}); self.assertIn("Policy: LEGACY | T 3D: DATA_GAP | LD OFF", html)
    def test_16_renderer_marks_preexisting_hard_stop(self):
        h=copy.deepcopy(SAMPLE_HELD_PORTFOLIO); html=generate_mobile_html_report_v2("2026-08-24 15:35",0,[],[],h,[],{"status":"OK","rows":[{"stock_code":"004960","stock_name":"한신공영","display_allowed":True,"position_kind":"LEGACY_POSITION","hard_stop_already_breached_at_shadow_start":1,"loss_defense_trigger":"OFF","defense_state":"OFF"}]}); self.assertIn("기존 -22%선 기통과", html)
    def test_17_renderer_distinguishes_actual_hard_stop(self):
        h=copy.deepcopy(SAMPLE_HELD_PORTFOLIO); html=generate_mobile_html_report_v2("2026-08-24 15:35",0,[],[],h,[],{"status":"OK","rows":[{"stock_code":"004960","stock_name":"한신공영","display_allowed":True,"position_kind":"LEGACY_POSITION","shadow_action":"HARD_STOP_EXIT","loss_defense_trigger":"OFF","defense_state":"OFF"}]}); self.assertIn("HARD_STOP_EXIT SHADOW", html)
    def test_18_renderer_shows_loss_defense_compact(self):
        h=copy.deepcopy(SAMPLE_HELD_PORTFOLIO); html=generate_mobile_html_report_v2("2026-08-24 15:35",0,[],[],h,[],{"status":"OK","rows":[{"stock_code":"004960","stock_name":"한신공영","display_allowed":True,"loss_defense_trigger":"ON","defense_state":"WAIT_REBOUND","loss_pct":-20,"t_change_3d":None,"is_45m_bearish_gate":1}]}); self.assertIn("Policy: LD WAIT_REBOUND | T 3D: DATA_GAP", html)
    def test_19_renderer_keeps_suspension_non_orderable(self):
        h=copy.deepcopy(SAMPLE_HELD_PORTFOLIO); html=generate_mobile_html_report_v2("2026-08-24 15:35",0,[],[],h,[],{"status":"OK","rows":[{"stock_code":"234920","stock_name":"자이글","display_allowed":True,"is_suspended":True}]}); self.assertIn("거래정지 HOLD / 주문 금지", html)
    def test_20_excel_adds_policy_shadow_without_removing_existing_sheets(self):
        with tempfile.TemporaryDirectory() as out, patch("src.utils.excel_exporter.LOG_DIR", Path(out)):
            db = MagicMock(); db.execute_query.return_value = []
            path = create_analysis_excel_report("2026-08-24 15:35", [copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])], [], db, {"status":"OK","rows":[]})
            with pd.ExcelFile(path) as workbook:
                self.assertEqual(set(workbook.sheet_names), {"보유종목_정밀평가", "DART_실제재무분석", "전체종목_분석요약", "POLICY_SHADOW"})

    def test_21_renderer_uses_data_gap_for_missing_t_3d(self):
        h=copy.deepcopy(SAMPLE_HELD_PORTFOLIO)
        html=generate_mobile_html_report_v2("2026-08-24 15:35",0,[],[],h,[],{"status":"OK","rows":[{"stock_code":"004960","stock_name":"한신공영","display_allowed":True,"position_kind":"LEGACY_POSITION","t_change_3d":None,"loss_defense_trigger":"OFF","defense_state":"OFF"}]})
        self.assertIn("T 3D: DATA_GAP", html)

    def test_22_renderer_shows_new_433_compact_gate(self):
        h=copy.deepcopy(SAMPLE_HELD_PORTFOLIO)
        html=generate_mobile_html_report_v2("2026-08-24 15:35",0,[],[],h,[],{"status":"OK","rows":[{"stock_code":"004960","stock_name":"한신공영","display_allowed":True,"position_kind":"NEW_433_CYCLE","stage":1,"f0":75,"f_score":75,"t0":43,"t_score":43,"cycle_target_qty":25,"stage1_target_qty":10,"quantity_current":10,"stage_size_status":"STAGE1_SIZE_MATCH","stage2_gate":"BASELINE_DATA_GAP"}]})
        self.assertIn("Policy: S1 | F75/T43 | S2 BLOCKED (BASELINE_DATA_GAP)", html)

    def test_23_excel_policy_shadow_sheet_etf_f_null_and_not_applicable(self):
        """Excel POLICY_SHADOW 시트에서 ETF 종목의 F0/F current는 NULL이고 F_baseline_status는 NOT_APPLICABLE이어야 함"""
        with tempfile.TemporaryDirectory() as out, patch("src.utils.excel_exporter.LOG_DIR", Path(out)):
            db = MagicMock(); db.execute_query.return_value = []
            policy_display = {
                "status": "OK",
                "rows": [
                    {
                        "stock_code": "161510", "stock_name": "PLUS 고배당주", "is_etf": 1,
                        "f0": None, "f_score": None, "f_baseline_status": "NOT_APPLICABLE",
                        "t0": None, "t_score": 85.0, "t_change_3d": None,
                        "position_kind": "LEGACY_POSITION", "cycle_status": "OPEN",
                    },
                    {
                        "stock_code": "004960", "stock_name": "한신공영", "is_etf": 0,
                        "f0": 75.0, "f_score": 75.0, "f_baseline_status": "OK",
                        "t0": 43.0, "t_score": 50.0, "t_change_3d": 7.0,
                        "position_kind": "NEW_433_CYCLE", "cycle_status": "OPEN",
                    }
                ]
            }
            path = create_analysis_excel_report("2026-08-26 15:35", [copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])], [], db, policy_display)
            df_ps = pd.read_excel(path, sheet_name="POLICY_SHADOW", skiprows=1)
            etf_row = df_ps[df_ps["종목코드"].astype(str).str.zfill(6) == "161510"].iloc[0]
            regular_row = df_ps[df_ps["종목코드"].astype(str).str.zfill(6) == "004960"].iloc[0]
            self.assertTrue(pd.isna(etf_row["F current"]))
            self.assertTrue(pd.isna(etf_row["F0"]))
            self.assertEqual(etf_row["F_baseline_status"], "NOT_APPLICABLE")
            self.assertEqual(regular_row["F current"], 75.0)
            self.assertEqual(regular_row["F_baseline_status"], "OK")

    def test_24_payload_etf_f_score_is_none(self):
        """PolicyShadowReader에서 ETF 종목 payload 생성 시 f_score는 None이고 f_baseline_status는 NOT_APPLICABLE"""
        self.observe(code="161510", is_etf=True, f=None)
        p = PolicyShadowReader(self.path).build_report_payload("2026-08-24 10:30:00", [{"stock_code": "161510", "stock_name": "PLUS 고배당주", "quantity": 10, "avg_buy_price": 100}])
        row = p["rows"][0]
        self.assertIsNone(row["f_score"])
        self.assertIsNone(row["f0"])
        self.assertEqual(row["f_baseline_status"], "NOT_APPLICABLE")

    def test_25_today_action_summary_box_rendered(self):
        """Gmail 최상단에 '📌 오늘의 핵심 조치' 박스가 정상 렌더링됨"""
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], SAMPLE_HELD_PORTFOLIO, None, {"status": "OK", "rows": []})
        self.assertIn("📌 오늘의 핵심 조치", html)

    def test_26_today_action_summary_reductions(self):
        """권고 매도수량이 있을 때 오늘의 핵심 조치에 '비중축소: 종목명 수량주' 표시"""
        item = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])
        item["stock_name"] = "테스트종목"
        item["recommended_order_qty"] = 150
        item["order_direction"] = "매도"
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item], None, {"status": "OK", "rows": []})
        self.assertIn("• <b>비중축소</b>: 테스트종목 150주", html)

    def test_27_today_action_summary_raised_stop(self):
        """손절선이 상향된 종목이 있을 때 '손절 상향: 종목명 old → new (+diff)' 표시"""
        item = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])
        item["stock_name"] = "한신공영"
        item["previous_confirmed_stop"] = 10540.0
        item["confirmed_stop_price"] = 11010.0
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item], None, {"status": "OK", "rows": []})
        self.assertIn("• <b>손절 상향</b>: 한신공영 10,540 → 11,010 (+470)", html)

    def test_28_today_action_summary_loss_defense_active(self):
        """LOSS_DEFENSE 발동 상태인 종목이 있을 때 'LOSS_DEFENSE 발동: 종목명 (state)' 표시"""
        p_row = {
            "stock_code": "010140", "stock_name": "삼성중공업", "display_allowed": 1,
            "loss_defense_trigger": "ON", "defense_state": "WAIT_REBOUND", "loss_pct": -8.5,
            "freshness_status": "FRESH", "position_match_status": "MATCH",
        }
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], SAMPLE_HELD_PORTFOLIO, None, {"status": "OK", "rows": [p_row]})
        self.assertIn("• <b>LOSS_DEFENSE 발동</b>: 삼성중공업 (WAIT_REBOUND)", html)

    def test_29_today_action_summary_loss_defense_none(self):
        """LOSS_DEFENSE 발동 종목이 없을 때 'LOSS_DEFENSE 신규 발동: 없음' 표시"""
        p_row = {
            "stock_code": "000490", "stock_name": "대동", "display_allowed": 1,
            "loss_defense_trigger": "OFF", "defense_state": "OFF",
            "position_kind": "LEGACY", "freshness_status": "FRESH", "position_match_status": "MATCH",
        }
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], SAMPLE_HELD_PORTFOLIO, None, {"status": "OK", "rows": [p_row]})
        self.assertIn("• <b>LOSS_DEFENSE 신규 발동</b>: 없음", html)

    def test_30_today_action_summary_new_433_ready(self):
        """NEW_433 Stage2 준비 완료 시 'NEW_433: 종목명 Stage2 준비완료' 표시"""
        p_row = {
            "stock_code": "004960", "stock_name": "한신공영", "display_allowed": 1,
            "position_kind": "NEW_433_CYCLE", "stage": 1, "stage2_gate": "STAGE2_READY",
            "freshness_status": "FRESH", "position_match_status": "MATCH",
        }
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], SAMPLE_HELD_PORTFOLIO, None, {"status": "OK", "rows": [p_row]})
        self.assertIn("• <b>NEW_433</b>: 한신공영 Stage2 준비완료", html)

    def test_31_today_action_summary_new_433_blocker(self):
        """NEW_433 Stage2 차단 시 'NEW_433: 종목명 Stage2 차단 — blocker' 표시"""
        p_row = {
            "stock_code": "004960", "stock_name": "한신공영", "display_allowed": 1,
            "position_kind": "NEW_433_CYCLE", "stage": 1, "stage2_gate": "TARGET_QTY_DATA_GAP",
            "freshness_status": "FRESH", "position_match_status": "MATCH",
        }
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], SAMPLE_HELD_PORTFOLIO, None, {"status": "OK", "rows": [p_row]})
        self.assertIn("• <b>NEW_433</b>: 한신공영 Stage2 차단 — TARGET_QTY_DATA_GAP", html)

    def test_32_today_action_summary_empty_no_action(self):
        """특이 조치가 없을 때 '특이 조치 없음 (전 종목 정상 감시 유지)' 표시"""
        item = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])
        item["recommended_order_qty"] = 0
        item["previous_confirmed_stop"] = 10000.0
        item["confirmed_stop_price"] = 10000.0
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item], None, {"status": "OK", "rows": []})
        self.assertIn("• <b>특이 조치 없음</b> (전 종목 정상 감시 유지)", html)

    def test_33_all_held_stocks_present_once_with_compact_fields(self):
        """보유종목은 1회씩만 표시되고 메일용 핵심 감시 필드를 제공함"""
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], SAMPLE_HELD_PORTFOLIO, None, {"status": "OK", "rows": []})
        self.assertIn("📊 전체 보유종목 현황", html)
        for h in SAMPLE_HELD_PORTFOLIO:
            code = h["stock_code"]
            self.assertIn(f'data-overview-code="{code}"', html)
            self.assertEqual(html.count(f'data-overview-code="{code}"'), 1)
        for label in ("내 평단", "평가손익", "현재 행동", "손절가", "익절 목표가", "익절 trailing 하락폭", "추매 감시가", "반등확인", "Policy:"):
            self.assertIn(label, html)

    def test_34_zero_quantity_excluded_from_overview(self):
        """quantity=0인 미보유 종목은 전략 현황 목록에 포함되지 않음"""
        item = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])
        item["stock_code"] = "999999"
        item["quantity"] = 0
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item], None, {"status": "OK", "rows": []})
        self.assertNotIn('data-overview-code="999999"', html)

    def test_35_concentration_risk_in_overview(self):
        """비중과다 종목의 전략 배지 및 축소수량/손절선 감시선 표시"""
        item = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])
        item["stock_code"] = "055490"
        item["stock_name"] = "테이팩스"
        item["trade_mode"] = "CONCENTRATION_RISK"
        item["recommended_order_qty"] = 66
        item["confirmed_stop_price"] = 12340.0
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item], None, {"status": "OK", "rows": []})
        self.assertIn("🟡 20% 초과분 분할축소", html)
        self.assertIn("손절가 12,340원", html)

    def test_36_suspended_in_overview(self):
        """거래정지 종목의 전략 배지 및 HOLD 주문금지 표시"""
        item = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])
        item["stock_code"] = "234920"
        item["stock_name"] = "자이글"
        item["trade_mode"] = "SUSPENDED_HOLD"
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item], None, {"status": "OK", "rows": []})
        self.assertIn("⚫ 거래정지 HOLD / 주문 금지", html)
        self.assertIn("손절가 HOLD (거래정지) | 익절 목표가 HOLD (거래정지)", html)
        self.assertIn("Policy: 거래정지 / V4 HOLD", html)

    def test_37_new_433_in_overview(self):
        """NEW_433 종목의 전략 배지 및 Policy compact 표시"""
        item = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])
        item["stock_code"] = "004960"
        item["stock_name"] = "한신공영"
        p_row = {
            "stock_code": "004960", "stock_name": "한신공영", "display_allowed": 1,
            "position_kind": "NEW_433_CYCLE", "stage": 1, "stage2_gate": "TARGET_QTY_DATA_GAP",
            "f_score": 75.0, "t_score": 49.0, "freshness_status": "FRESH", "position_match_status": "MATCH",
        }
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item], None, {"status": "OK", "rows": [p_row]})
        self.assertIn("⚪ NEW_433 Stage1 (Stage2 BLOCKED)", html)
        self.assertIn("Policy: S1 | F75.0/T49.0 | S2 BLOCKED (TARGET_QTY_DATA_GAP)", html)

    def test_38_loss_defense_in_overview(self):
        """LOSS_DEFENSE 활성 종목의 전략 배지 및 Policy compact 표시"""
        item = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])
        item["stock_code"] = "010140"
        item["stock_name"] = "삼성중공업"
        p_row = {
            "stock_code": "010140", "stock_name": "삼성중공업", "display_allowed": 1,
            "loss_defense_trigger": "ON", "defense_state": "WAIT_REBOUND", "t_change_3d": 5.0,
            "freshness_status": "FRESH", "position_match_status": "MATCH",
        }
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item], None, {"status": "OK", "rows": [p_row]})
        self.assertIn("🔴 LOSS_DEFENSE 활성 (WAIT_REBOUND)", html)
        self.assertIn("Policy: LD WAIT_REBOUND | T 3D: +5", html)

    def test_39_policy_stale_in_overview(self):
        """Policy stale 시 'Policy: STALE_POLICY_SNAPSHOT — 행동판정 금지' 표시"""
        item = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])
        item["stock_code"] = "000490"
        p_row = {
            "stock_code": "000490", "stock_name": "대동", "display_allowed": 0,
            "freshness_status": "STALE_POLICY_SNAPSHOT", "position_match_status": "MATCH",
        }
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item], None, {"status": "OK", "rows": [p_row]})
        self.assertIn("Policy: STALE_POLICY_SNAPSHOT — 행동판정 금지", html)

    def test_40_no_none_or_null_rendered_in_html(self):
        """사용자 화면에 None / null / NULL 노출이 없고 DATA_GAP으로 치환됨"""
        p_row = {
            "stock_code": "004960", "stock_name": "한신공영", "display_allowed": 1,
            "position_kind": "NEW_433_CYCLE", "stage": 1,
            "f0": None, "f_score": None, "t0": None, "t_score": None,
            "cycle_target_qty": None, "stage1_target_qty": None, "quantity_current": None,
            "stage_size_status": None, "stage2_gate": None, "t_change_3d": None,
            "freshness_status": "FRESH", "position_match_status": "MATCH",
        }
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], SAMPLE_HELD_PORTFOLIO, None, {"status": "OK", "rows": [p_row]})
        self.assertNotIn("None", html)
        self.assertIn("Policy: S1 | FDATA_GAP/TDATA_GAP | S2 BLOCKED (DATA_GAP)", html)

    def test_41_etf_f_rendered_as_not_applicable(self):
        """ETF 종목의 F0/F 점수는 NEW_433에서도 NOT_APPLICABLE로 표시됨"""
        p_row = {
            "stock_code": "161510", "stock_name": "PLUS 고배당주", "display_allowed": 1,
            "is_etf": 1, "position_kind": "NEW_433_CYCLE", "stage": 1,
            "f0": None, "f_score": None, "t0": 80.0, "t_score": 85.0,
            "freshness_status": "FRESH", "position_match_status": "MATCH",
        }
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], SAMPLE_HELD_PORTFOLIO, None, {"status": "OK", "rows": [p_row]})
        self.assertIn("Policy: S1 | FN/A/T85.0 | S2 BLOCKED (DATA_GAP)", html)

    def test_42_t_3d_formatting(self):
        """T 3D 정수+부호 서식 (+22, +8, -6, 0, DATA_GAP) 검증"""
        self.assertEqual(format_t_3d(22.4), "+22")
        self.assertEqual(format_t_3d(8.0), "+8")
        self.assertEqual(format_t_3d(-6.2), "-6")
        self.assertEqual(format_t_3d(0.0), "0")
        self.assertEqual(format_t_3d(None), "DATA_GAP")
        self.assertEqual(format_t_3d(""), "DATA_GAP")

    def test_43_legacy_normal_policy_compact(self):
        """정상 LEGACY 종목의 compact policy 표시 검증"""
        item = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0])
        item["stock_code"] = "000490"
        item["stock_name"] = "대동"
        p_row = {
            "stock_code": "000490", "stock_name": "대동", "display_allowed": 1,
            "position_kind": "LEGACY", "t_change_3d": 6.0, "loss_defense_trigger": "OFF", "defense_state": "OFF",
            "freshness_status": "FRESH", "position_match_status": "MATCH",
        }
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item], None, {"status": "OK", "rows": [p_row]})
        self.assertIn("Policy: LEGACY | T 3D: +6 | LD OFF", html)

    def test_44_preexisting_and_suspended_text_preserved(self):
        """PREEXISTING 및 거래정지 특수 표현이 보존됨"""
        item1 = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0]); item1["stock_code"] = "010140"; item1["stock_name"] = "삼성중공업"
        item2 = copy.deepcopy(SAMPLE_HELD_PORTFOLIO[0]); item2["stock_code"] = "234920"; item2["stock_name"] = "자이글"
        p_rows = [
            {
                "stock_code": "010140", "stock_name": "삼성중공업", "display_allowed": 1,
                "hard_stop_already_breached_at_shadow_start": 1, "loss_defense_trigger": "OFF", "defense_state": "OFF",
                "freshness_status": "FRESH", "position_match_status": "MATCH",
            },
            {
                "stock_code": "234920", "stock_name": "자이글", "display_allowed": 1,
                "is_suspended": 1, "freshness_status": "FRESH", "position_match_status": "MATCH",
            }
        ]
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], [item1, item2], None, {"status": "OK", "rows": p_rows})
        self.assertIn("기존 -22%선 기통과", html)
        self.assertIn("Policy: 거래정지 / V4 HOLD", html)

    def test_45_section_headers_are_single_pass_layout(self):
        """한 줄 요약→보유종목 1회→45M→DART 순서만 유지되고 중복 섹션은 제거됨"""
        html = generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], SAMPLE_HELD_PORTFOLIO, None, {"status": "OK", "rows": []})
        self.assertIn("📌 오늘의 핵심 조치", html)
        self.assertIn("📊 전체 보유종목 현황", html)
        self.assertIn("📈 45M ADD ADVISORY", html)
        self.assertIn("📢 DART 주요 공시 & 브리핑", html)
        self.assertNotIn("⚠️ 오늘 변화·조치 종목 상세", html)
        self.assertNotIn("🧪 Policy Shadow 특이사항", html)

    def test_46_no_mutation_to_input_holdings_or_policy(self):
        """렌더러 호출이 입력된 보유종목 리스트 및 Policy 딕셔너리를 변경하지 않음 (불변성)"""
        holdings_copy = copy.deepcopy(SAMPLE_HELD_PORTFOLIO)
        policy_copy = {"status": "OK", "rows": [{"stock_code": "004960", "stock_name": "한신공영"}]}
        generate_mobile_html_report_v2("2026-08-26 15:35", 1, [], [], SAMPLE_HELD_PORTFOLIO, None, policy_copy)
        self.assertEqual(SAMPLE_HELD_PORTFOLIO, holdings_copy)
        self.assertEqual(policy_copy["rows"][0]["stock_code"], "004960")
