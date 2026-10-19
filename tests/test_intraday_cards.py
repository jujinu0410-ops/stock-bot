import copy
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from src.notifications.intraday_data import prepare_intraday_holdings, select_latest_alerts, valuation, load_recent_alerts
from src.notifications.intraday_renderer import generate_intraday_html_report, money
from src.notifications.gmail_notifier import GmailNotifier
from src.notifications.mobile_renderer_v2 import build_today_action_summary_html


def holding(**changes):
    return dict(stock_code="005930", stock_name="긴ETF이름"*12, current_price=12000,
                avg_buy_price=10000, quantity=10, pnl_amount=20000, pnl_pct=20,
                buy_trailing_dist=61, sell_trailing_dist=41, action_status="홀딩",
                trade_mode="NORMAL", **changes)


class IntradayCardsTest(unittest.TestCase):
    def setUp(self):
        self.h = holding()
        self.alerts = select_latest_alerts([
            ["TIMESTAMP", "TICKER", "EVENT_KEY", "MESSAGE"],
            ["2026-10-18 15:00:00", 5930, "STOP_BREACH", "전일"],
            ["2026-10-19 09:05:00", 5930, "DOWN_EARLY", "하락 조짐"],
            ["2026-10-19 12:35:00", 5930, "DOWN_CONFIRMED", "하락 확인"],
        ], "2026-10-19 11:20:00")

    def render(self, **kwargs):
        return generate_intraday_html_report("2026-10-19 11:20:00", [self.h], recent_alerts=self.alerts, **kwargs)

    def test_01_actual_event_cutoff_and_code(self):
        output = self.render()
        self.assertIn("09:05", output)
        self.assertIn("DOWN_EARLY", output)
        self.assertNotIn("DOWN_CONFIRMED", output)
        self.assertIn('data-holding-code="005930"', output)

    def test_02_serial_timestamp_preserves_time(self):
        result = select_latest_alerts([["TIMESTAMP", "TICKER", "EVENT_KEY", "MESSAGE"],
                                     [46314.5, "KRX:005930", "DOWN_EARLY", "하락"]], "2026-10-19 13:35:00")
        self.assertIn("12:00:00", result["by_code"]["005930"]["occurred_at"])

    def test_03_no_event_is_distinct_from_unavailable(self):
        self.alerts["by_code"] = {}
        self.assertIn("오늘 신규 신호 없음", self.render())
        self.alerts["status"] = "UNAVAILABLE"
        self.assertIn("최근 알림 조회 불가", self.render())
        self.assertNotIn("오늘 신규 신호 없음", self.render())

    def test_04_failed_lookup(self):
        with patch("src.engine.portfolio_reconciler.PortfolioReconciler._get_token", side_effect=RuntimeError):
            self.assertEqual(load_recent_alerts(datetime(2026,10,19,11,20))["status"], "UNAVAILABLE")

    def test_05_missing_valuation_never_zero(self):
        for field in ("quantity", "avg_buy_price", "current_price", "pnl_amount", "pnl_pct"):
            with self.subTest(field=field):
                original = self.h.pop(field)
                self.assertIn("자료 없음", self.render())
                self.h[field] = original

    def test_06_inconsistent_amount_or_sign_rejected(self):
        for field, value in (("pnl_amount",-20000),("pnl_pct",-20),("pnl_amount",21000),("pnl_pct",21)):
            item = dict(self.h, **{field:value})
            with self.assertRaises(ValueError):
                valuation(item)

    def test_07_uses_existing_atr_distances_without_recalculation(self):
        self.h["atr_14"] = 99999
        output = self.render()
        self.assertIn("61원",output)
        self.assertIn("41원",output)
        self.h.pop("buy_trailing_dist")
        self.assertIn("자료 없음", self.render())

    def test_08_suspended(self):
        self.h["trade_mode"] = "SUSPENDED_HOLD"
        output = self.render()
        self.assertIn("거래정지 전 가격",output)
        self.assertNotIn("61원",output)
        self.assertIn("거래정지",output)

    def test_09_no_mutation_and_summary_at_bottom(self):
        before = copy.deepcopy(self.h)
        output = self.render()
        self.assertEqual(before,self.h)
        self.assertLess(output.index('data-section="holdings"'),output.index('data-section="disclosures"'))
        self.assertLess(output.index('data-section="disclosures"'),output.index('data-section="actions"'))
        self.assertIn("특이 조치 없음",output)
        self.assertNotIn("BB–ATR",output)
        self.assertNotIn("<script",output)
        self.assertNotIn("<style",output)

    def test_10_escape_and_correction_group(self):
        self.h["stock_name"] = '<img src=x onerror="bad">'
        output = self.render(disclosures=[dict(rcept_no="1",stock_code="005930",stock_name="가",report_nm="사업보고서",summary="사실",link="javascript:bad"),
                                        dict(rcept_no="2",stock_code="005930",stock_name="가",report_nm="[기재정정]사업보고서",summary="정정 사실",impact="추론")])
        self.assertNotIn('<img',output)
        self.assertNotIn('href="javascript:',output)
        self.assertIn("원공시",output)
        self.assertIn("정정공시",output)
        self.assertIn("투자 영향 해석",output)

    def test_11_account_precision_quantity_and_no_mutation(self):
        account = dict(self.h)
        before = copy.deepcopy(self.h)
        self.assertEqual(prepare_intraday_holdings([self.h],[account]),[self.h])
        self.assertEqual(before,self.h)
        account["quantity"] = 11
        with self.assertRaises(ValueError):
            prepare_intraday_holdings([self.h],[account])

    def test_12_duplicates_and_fake_alerts_rejected(self):
        with self.assertRaises(ValueError):
            generate_intraday_html_report("2026-10-19 11:20:00",[self.h,self.h])
        self.alerts["by_code"]["005930"]["source"] = "STAGE_STATUS"
        with self.assertRaises(ValueError):
            self.render()

    def test_13_explicit_session_routes_only_intraday(self):
        notifier = GmailNotifier(sender_email="",app_password="")
        with patch("src.notifications.gmail_notifier.generate_mobile_html_report_v2",return_value="LEGACY") as legacy:
            for session in ("1120","1335"):
                output = notifier.generate_html_report("2026-10-19 11:20:00",1,[],[],[self.h],report_session=session,recent_alerts=self.alerts)
                self.assertIn('data-intraday-layout="cards"',output)
            for session in (None,"1535","POSTMARKET"):
                self.assertEqual(notifier.generate_html_report("2026-10-19 15:35:00",1,[],[],[self.h],report_session=session),"LEGACY")
            self.assertEqual(legacy.call_count,3)

    def test_14_source_precision_and_nan(self):
        self.assertEqual(money(10000.25),"10,000.25원")
        self.assertEqual(money(float('nan')),"자료 없음")

    def test_15_reductions_stops_and_policy_content_preserved(self):
        self.h.update(recommended_order_qty=3,order_direction="매도",prev_confirmed_stop=8000,confirmed_stop_price=9000)
        policy = {"rows":[dict(stock_name="가",loss_defense_trigger="ON",defense_state="WAIT_REBOUND",position_kind="NEW_433_CYCLE",stage=1,stage2_gate="STAGE2_BLOCKED")]}
        output = self.render(policy_display=policy)
        for term in ("비중축소", "3주", "손절 상향", "8,000", "9,000", "LOSS_DEFENSE", "STAGE2_BLOCKED"):
            self.assertIn(term, output[output.index('data-section="actions"'):])
            self.assertNotIn(term, output[output.index('data-section="holdings"'):output.index('data-section="disclosures"')])


if __name__ == "__main__":
    unittest.main()
