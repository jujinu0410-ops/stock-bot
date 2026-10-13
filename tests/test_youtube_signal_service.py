import os
import unittest
import pandas as pd
from unittest.mock import patch

from services import youtube_signal_service as svc


class YouTubeSignalServiceTest(unittest.TestCase):
    def setUp(self):
        self.old_token = os.environ.get("YOUTUBE_SIGNAL_API_TOKEN")
        os.environ["YOUTUBE_SIGNAL_API_TOKEN"] = "test-token"
        self.client = svc.app.test_client()

    def tearDown(self):
        if self.old_token is None:
            os.environ.pop("YOUTUBE_SIGNAL_API_TOKEN", None)
        else:
            os.environ["YOUTUBE_SIGNAL_API_TOKEN"] = self.old_token

    def test_health(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["ok"])
        self.assertIn("/jev-gate", res.get_json()["endpoints"])
        self.assertIn("/alert-outcome", res.get_json()["endpoints"])

    def test_held_obv15_requires_shared_token(self):
        res = self.client.post("/held-obv15", json={"ticker": "010140", "atr14": 900})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"], "UNAUTHORIZED")

    @patch.object(svc, "_held_obv15")
    def test_held_obv15_response_is_rule_only_no_orders(self, mocked):
        mocked.return_value = {
            "ok": True, "ticker": "010140", "decision": "WARN",
            "consecutive_no_gold_bars": 15,
            "reason": "15_COMPLETED_45M_BARS_NO_GOLD_OBV_OBV9_FALLING_NO_LIVE_RECOVERY",
        }
        res = self.client.post(
            "/held-obv15",
            headers={"X-StockBot-Token": "test-token"},
            json={"ticker": "010140", "atr14": 900},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["decision"], "WARN")
        mocked.assert_called_once()
        self.assertNotIn("order", res.get_json())

    def test_confirm_requires_shared_token(self):
        res = self.client.post("/confirm", json={"ticker": "007660", "tech_status": "BUY_CANDIDATE"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"], "UNAUTHORIZED")

    @patch.object(svc, "_krx_session_status")
    @patch.object(svc, "_confirm")
    def test_confirm_passes_only_authenticated_payload(self, mocked, krx_status):
        krx_status.return_value = {
            "date": "2026-10-08",
            "is_trading_day": True,
            "reason": "TRADING_DAY",
            "timezone": "Asia/Seoul",
        }
        mocked.return_value = {
            "ok": True,
            "ticker": "007660",
            "final_signal": "WATCH_ONLY",
            "mail_sent": True,
        }
        res = self.client.post(
            "/confirm",
            headers={"X-StockBot-Token": "test-token"},
            json={"ticker": "007660", "name": "이수페타시스", "tech_status": "BUY_CANDIDATE"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["ok"])
        mocked.assert_called_once()

    def test_krx_session_status_closes_hangul_day(self):
        status = svc._krx_session_status("2026-10-09")
        self.assertFalse(status["is_trading_day"])
        self.assertEqual(status["reason"], "KRX_CLOSED")

    def test_krx_session_status_opens_next_trading_monday(self):
        status = svc._krx_session_status("2026-10-12")
        self.assertTrue(status["is_trading_day"])
        self.assertEqual(status["reason"], "TRADING_DAY")

    @patch.object(svc, "_krx_session_status")
    def test_confirm_holiday_fail_closed_before_signal_engine(self, krx_status):
        krx_status.return_value = {
            "date": "2026-10-09",
            "is_trading_day": False,
            "reason": "KRX_CLOSED",
            "timezone": "Asia/Seoul",
        }
        res = self.client.post(
            "/confirm",
            headers={"X-StockBot-Token": "test-token"},
            json={"ticker": "010140", "tech_status": "BUY_CANDIDATE"},
        )
        body = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["suppressed_by_krx_closed"])
        self.assertFalse(body["mail_sent"])
        self.assertEqual(body["reason"], "KRX_MARKET_CLOSED")

    def test_alert_outcome_requires_shared_token(self):
        res = self.client.post(
            "/alert-outcome",
            json={
                "ticker": "488900",
                "alert_time": "2026-10-07T11:20:00+09:00",
                "alert_price": 11910,
                "atr14": 900,
            },
        )
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"], "UNAUTHORIZED")

    def test_minute_bar_datetime_supports_intraday_hhmmss(self):
        alert = svc._parse_alert_time("2026-10-07T11:20:00+09:00")
        parsed = svc._minute_bar_datetime("122100", alert)
        self.assertEqual(parsed.isoformat(), "2026-10-07T12:21:00+09:00")

    def test_build_alert_outcome_bars_uses_minutes_after_alert(self):
        alert = svc._parse_alert_time("2026-10-07T11:20:00+09:00")
        bars = svc._build_alert_outcome_bars(
            [
                {"time": "111900", "high": 12000, "low": 11800, "close": 11900},
                {"time": "112500", "high": 12100, "low": 11900, "close": 12050},
                {"time": "115000", "high": 12300, "low": 12000, "close": 12200},
                {"time": "122000", "high": 12600, "low": 12100, "close": 12400},
            ],
            alert,
        )
        self.assertEqual([b["minutes_after_alert"] for b in bars], [5, 30, 60])

    def test_45m_exit_risk_warns_on_two_session_obv_and_falling_macd(self):
        idx = list(pd.date_range("2026-10-07 09:45", periods=20, freq="45min"))
        idx += list(pd.date_range("2026-10-08 09:45", periods=20, freq="45min"))
        closes = [23000 - i * 80 for i in range(40)]
        df = pd.DataFrame(
            {
                "Open": closes,
                "High": [v + 50 for v in closes],
                "Low": [v - 50 for v in closes],
                "Close": closes,
                "Volume": [1000 + i * 10 for i in range(40)],
            },
            index=pd.DatetimeIndex(idx),
        )
        out = svc._analyze_45m_exit_risk_df(df, "TEST")
        self.assertTrue(out["available"])
        self.assertEqual(out["decision"], "WARN")
        self.assertTrue(out["no_gold_two_sessions"])
        self.assertTrue(out["obv_session_falling"])
        self.assertTrue(out["macd_falling_3"])
        self.assertTrue(out["macd_below_signal"])

    def test_ticker_validation(self):
        self.assertEqual(svc._clean_ticker("007660"), "007660")
        with self.assertRaises(svc.SignalServiceError):
            svc._clean_ticker("ABC")

    @patch.object(svc, "_fetch_45m_vwap_gate")
    def test_lg_1019_rising_one_minute_does_not_override_45m_downtrend(self, vwap):
        vwap.return_value = {
            "available": True, "gold": False, "vwap9": 396625.0,
            "state": "VWAP_DEAD", "completed_bar": "09:45",
        }
        gate = svc._evaluate_entry_timing_veto("373220", fresh_price=387000.0)
        self.assertEqual(gate["decision"], "VETO")
        self.assertEqual(gate["reason"], "45M_WEAK_AND_PRICE_BELOW_VWAP9")
        self.assertEqual(gate["fresh_price"], 387000.0)

    @patch.object(svc, "_fetch_45m_vwap_gate")
    def test_45m_missing_is_verification_pending_not_pass(self, vwap):
        vwap.return_value = {"available": False, "reason": "CUTOFF_MISMATCH"}
        gate = svc._evaluate_entry_timing_veto("373220", fresh_price=387000.0)
        self.assertEqual(gate["decision"], "WAIT")
        self.assertEqual(gate["reason"], "45M_VWAP_UNAVAILABLE")

    @patch.object(svc, "_fetch_45m_vwap_gate")
    def test_kiwoom_quote_missing_is_verification_pending(self, vwap):
        vwap.return_value = {"available": True, "gold": True, "vwap9": 380000.0}
        gate = svc._evaluate_entry_timing_veto("373220", fresh_price=None)
        self.assertEqual(gate["decision"], "WAIT")

    @patch.object(svc, "_fetch_45m_vwap_gate")
    def test_45m_downtrend_blocks_buy_even_after_intraday_price_recovery(self, vwap):
        vwap.return_value = {"available": True, "gold": False, "vwap9": 380000.0}
        gate = svc._evaluate_entry_timing_veto("373220", fresh_price=387000.0)
        self.assertEqual(gate["decision"], "VETO")
        self.assertEqual(gate["reason"], "45M_TREND_NOT_GOLD")

    @patch.object(svc, "_fetch_45m_vwap_gate")
    def test_45m_gold_but_price_below_vwap_still_blocks_buy(self, vwap):
        vwap.return_value = {"available": True, "gold": True, "vwap9": 389000.0}
        gate = svc._evaluate_entry_timing_veto("373220", fresh_price=387000.0)
        self.assertEqual(gate["decision"], "VETO")
        self.assertEqual(gate["reason"], "PRICE_BELOW_45M_VWAP9")

    @patch.object(svc, "_fetch_45m_vwap_gate")
    def test_upward_45m_and_kiwoom_price_above_vwap_allow_buy(self, vwap):
        vwap.return_value = {"available": True, "gold": True, "vwap9": 384000.0}
        gate = svc._evaluate_entry_timing_veto("373220", fresh_price=387000.0)
        self.assertEqual(gate["decision"], "PASS")


    def test_strong_confirmation_gate_requires_45m_uptrend(self):
        strong = svc.FinalSignalAssessment(
            svc.FINAL_BUY_ALERT_STRONG,
            svc.TECH_BUY_CANDIDATE,
            "POSITIVE",
            "UNAVAILABLE",
            "confirmed",
        )
        final, gate = svc._apply_strong_confirmation_gate(
            strong,
            {"fresh_price": 214000, "quote": {"open": 231000, "change_pct": -0.075}},
            {"vwap_45m": {"available": True, "gold": False, "vwap9": 220000, "state": "VWAP_DEAD"}},
        )
        self.assertEqual(final.final_signal, svc.FINAL_BUY_ALERT)
        self.assertEqual(gate["decision"], "DOWNGRADE_TO_BUY_ALERT")
        self.assertIn("45M_VWAP_NOT_GOLD", gate["reason"])
        self.assertIn("INTRADAY_DROP_GE_5PCT", gate["reason"])
        self.assertIn("PREVCLOSE_DROP_GE_5PCT", gate["reason"])

    def test_strong_confirmation_gate_downgrades_gap_drop_even_if_open_flat(self):
        strong = svc.FinalSignalAssessment(
            svc.FINAL_BUY_ALERT_STRONG, svc.TECH_BUY_CANDIDATE,
            "POSITIVE", "UNAVAILABLE", "confirmed"
        )
        final, gate = svc._apply_strong_confirmation_gate(
            strong,
            {"fresh_price": 159800.0, "quote": {"open": 159500.0, "change_pct": -0.0533}},
            {"vwap_45m": {"available": True, "gold": True, "vwap9": 158000.0}},
        )
        self.assertEqual(final.final_signal, svc.FINAL_BUY_ALERT)
        self.assertIn("PREVCLOSE_DROP_GE_5PCT", gate["reason"])

    def test_strong_confirmation_gate_downgrades_when_price_below_45m_vwap9(self):
        strong = svc.FinalSignalAssessment(
            svc.FINAL_BUY_ALERT_STRONG, svc.TECH_BUY_CANDIDATE,
            "POSITIVE", "UNAVAILABLE", "confirmed"
        )
        final, gate = svc._apply_strong_confirmation_gate(
            strong,
            {"fresh_price": 159800.0, "quote": {"open": 159500.0, "change_pct": 0.003}},
            {"vwap_45m": {"available": True, "gold": True, "vwap9": 164000.0}},
        )
        self.assertEqual(final.final_signal, svc.FINAL_BUY_ALERT)
        self.assertIn("PRICE_BELOW_45M_VWAP9", gate["reason"])

    def test_strong_confirmation_gate_passes_when_45m_uptrend_and_no_crash(self):
        strong = svc.FinalSignalAssessment(
            svc.FINAL_BUY_ALERT_STRONG,
            svc.TECH_BUY_CANDIDATE,
            "POSITIVE",
            "UNAVAILABLE",
            "confirmed",
        )
        final, gate = svc._apply_strong_confirmation_gate(
            strong,
            {"fresh_price": 105000, "quote": {"open": 104000, "change_pct": 0.015}},
            {"vwap_45m": {"available": True, "gold": True, "vwap9": 104500, "state": "VWAP_GOLD"}},
        )
        self.assertEqual(final.final_signal, svc.FINAL_BUY_ALERT_STRONG)
        self.assertEqual(gate["decision"], "PASS_STRONG")

    def test_watch_only_is_never_mail_worthy(self):
        self.assertNotIn(svc.FINAL_WATCH_ONLY, svc.ALLOWED_FINAL_SIGNALS)
        self.assertEqual(
            svc.ALLOWED_FINAL_SIGNALS,
            {svc.FINAL_BUY_ALERT, svc.FINAL_BUY_ALERT_STRONG},
        )

    @patch.object(svc, "_fetch_kiwoom_minute_bars")
    @patch.object(svc, "_fetch_kiwoom_quote")
    def test_freshness_veto_when_price_drops_below_buy_trigger(self, quote, minute):
        quote.return_value = {"available": True, "current_price": 950.0, "source": "KIWOOM_KA10001"}
        minute.return_value = {
            "available": True,
            "source": "KIWOOM_KA10080",
            "bars": [
                {"time": "100000", "close": 1010.0, "high": 1012.0, "low": 1008.0, "volume": 100},
                {"time": "100100", "close": 1005.0, "high": 1007.0, "low": 1002.0, "volume": 100},
                {"time": "100200", "close": 990.0, "high": 1000.0, "low": 988.0, "volume": 100},
                {"time": "100300", "close": 970.0, "high": 990.0, "low": 968.0, "volume": 100},
                {"time": "100400", "close": 955.0, "high": 972.0, "low": 950.0, "volume": 100},
            ],
        }
        gate = svc._evaluate_kiwoom_freshness_gate(
            "009150",
            signal_price=1000.0,
            atr14=100.0,
            buy_trigger_05=980.0,
            session=object(),
            token="token",
        )
        self.assertEqual(gate["decision"], "VETO")
        self.assertEqual(gate["reason"], "FRESH_PRICE_BELOW_BUY_TRIGGER")

    @patch.object(svc, "_fetch_kiwoom_minute_bars")
    @patch.object(svc, "_fetch_kiwoom_quote")
    def test_freshness_veto_s_oil_minus_5pct_despite_rising_one_minute_bars(self, quote, minute):
        quote.return_value = {
            "available": True, "current_price": 159800.0,
            "open": 159500.0, "change_pct": -0.0533, "source": "KIWOOM_KA10001"
        }
        minute.return_value = {
            "available": True, "source": "KIWOOM_KA10080",
            "bars": [
                {"time": "095400", "close": 159000, "high": 159100, "low": 158900, "volume": 100},
                {"time": "095500", "close": 159200, "high": 159300, "low": 159100, "volume": 150},
                {"time": "095600", "close": 159400, "high": 159500, "low": 159300, "volume": 150},
                {"time": "095700", "close": 159600, "high": 159700, "low": 159500, "volume": 150},
                {"time": "095800", "close": 159800, "high": 159900, "low": 159700, "volume": 150},
            ]
        }
        gate = svc._evaluate_kiwoom_freshness_gate(
            "010950", signal_price=159700, atr14=9800,
            buy_trigger_05=152093, session=object(), token="token",
        )
        self.assertEqual(gate["decision"], "VETO")
        self.assertEqual(gate["reason"], "PREVCLOSE_DROP_GE_5PCT")
        self.assertEqual(gate["quote"]["change_pct"], -0.0533)

    @patch.object(svc, "_fetch_kiwoom_minute_bars")
    @patch.object(svc, "_fetch_kiwoom_quote")
    def test_freshness_passes_when_signal_is_still_alive(self, quote, minute):
        quote.return_value = {"available": True, "current_price": 1020.0, "source": "KIWOOM_KA10001"}
        minute.return_value = {
            "available": True,
            "source": "KIWOOM_KA10080",
            "bars": [
                {"time": "100000", "close": 995.0, "high": 998.0, "low": 992.0, "volume": 100},
                {"time": "100100", "close": 1000.0, "high": 1003.0, "low": 998.0, "volume": 120},
                {"time": "100200", "close": 1005.0, "high": 1008.0, "low": 1002.0, "volume": 130},
                {"time": "100300", "close": 1010.0, "high": 1013.0, "low": 1008.0, "volume": 150},
                {"time": "100400", "close": 1018.0, "high": 1020.0, "low": 1015.0, "volume": 170},
            ],
        }
        gate = svc._evaluate_kiwoom_freshness_gate(
            "009150",
            signal_price=1000.0,
            atr14=100.0,
            buy_trigger_05=980.0,
            session=object(),
            token="token",
        )
        self.assertEqual(gate["decision"], "PASS")
        self.assertEqual(gate["reason"], "FRESH_SIGNAL_ALIVE")
        self.assertEqual(gate["direction"], "RISING")

    @patch.object(svc, "_fetch_kiwoom_minute_bars")
    @patch.object(svc, "_fetch_kiwoom_quote")
    def test_freshness_veto_when_price_reaches_overheat_upper(self, quote, minute):
        quote.return_value = {
            "available": True,
            "current_price": 228000.0,
            "change_pct": -0.015,
            "source": "KIWOOM_KA10001",
        }
        minute.return_value = {
            "available": True,
            "source": "KIWOOM_KA10080",
            "bars": [
                {"time": "093000", "close": 225000.0, "high": 225500.0, "low": 224500.0, "volume": 100},
                {"time": "093100", "close": 226000.0, "high": 226500.0, "low": 225500.0, "volume": 120},
                {"time": "093200", "close": 227000.0, "high": 227500.0, "low": 226500.0, "volume": 130},
                {"time": "093300", "close": 227500.0, "high": 228000.0, "low": 227000.0, "volume": 140},
                {"time": "093400", "close": 228000.0, "high": 228500.0, "low": 227500.0, "volume": 150},
            ],
        }
        gate = svc._evaluate_kiwoom_freshness_gate(
            "062040",
            signal_price=228000.0,
            atr14=12146.66361,
            buy_trigger_05=209073.3318,
            overheat_upper=225354.9954,
            session=object(),
            token="token",
        )
        self.assertEqual(gate["decision"], "VETO")
        self.assertEqual(gate["reason"], "FRESH_PRICE_AT_OR_ABOVE_OVERHEAT")

    def test_early_alert_veto_when_45m_unavailable_and_day_below_minus_1pct(self):
        early = svc.FinalSignalAssessment(
            svc.FINAL_BUY_ALERT,
            svc.TECH_BUY_CANDIDATE,
            "POSITIVE",
            "UNAVAILABLE",
            "early alert",
        )
        gate = svc._evaluate_early_alert_safety_gate(
            early,
            {"change_pct": -0.015},
            {"vwap_45m": {"available": False, "reason": "UNAVAILABLE"}},
        )
        self.assertEqual(gate["decision"], "VETO")
        self.assertEqual(gate["reason"], "45M_UNAVAILABLE_AND_DAY_BELOW_MINUS_1PCT")

    def test_daily_bear_pattern_downgrades_strong_to_early(self):
        final = svc.FinalSignalAssessment(
            svc.FINAL_BUY_ALERT_STRONG,
            svc.TECH_BUY_CANDIDATE,
            "POSITIVE",
            "UNAVAILABLE",
            "strong",
        )
        tech = svc.TechnicalAssessment(
            svc.TECH_BUY_CANDIDATE, None, None, 100.0, 110.0, True, False, "ok"
        )
        changed, gate = svc._apply_daily_pattern_signal_modifier(
            final,
            {"available": True, "bias": "BEAR", "labels": ["석별형"]},
            tech,
            {"decision": "PASS", "fresh_price": 120.0, "quote": {"open": 118.0}},
            {"vwap_45m": {"available": True, "gold": True}},
        )
        self.assertEqual(changed.final_signal, svc.FINAL_BUY_ALERT)
        self.assertEqual(gate["decision"], "DOWNGRADE")

    def test_daily_bear_pattern_downgrades_early_to_watch(self):
        final = svc.FinalSignalAssessment(
            svc.FINAL_BUY_ALERT,
            svc.TECH_BUY_CANDIDATE,
            "POSITIVE",
            "UNAVAILABLE",
            "early",
        )
        tech = svc.TechnicalAssessment(
            svc.TECH_BUY_CANDIDATE, None, None, 100.0, 110.0, False, False, "ok"
        )
        changed, gate = svc._apply_daily_pattern_signal_modifier(
            final,
            {"available": True, "bias": "BEAR", "labels": ["흑삼병"]},
            tech,
            {"decision": "PASS", "fresh_price": 105.0, "quote": {"open": 106.0}},
            {"vwap_45m": {"available": True, "gold": True}},
        )
        self.assertEqual(changed.final_signal, svc.FINAL_WATCH_ONLY)
        self.assertEqual(gate["decision"], "DOWNGRADE")

    def test_daily_bull_pattern_upgrades_neutral_flow_only_with_06_and_45m(self):
        final = svc.FinalSignalAssessment(
            svc.FINAL_BUY_ALERT,
            svc.TECH_BUY_CANDIDATE,
            "NEUTRAL",
            "UNAVAILABLE",
            "early",
        )
        tech = svc.TechnicalAssessment(
            svc.TECH_BUY_CANDIDATE, None, None, 100.0, 110.0, True, False, "ok"
        )
        changed, gate = svc._apply_daily_pattern_signal_modifier(
            final,
            {"available": True, "bias": "BULL", "labels": ["샛별형"]},
            tech,
            {"decision": "PASS", "fresh_price": 120.0, "quote": {"open": 118.0}},
            {"vwap_45m": {"available": True, "gold": True}},
        )
        self.assertEqual(changed.final_signal, svc.FINAL_BUY_ALERT_STRONG)
        self.assertEqual(gate["decision"], "UPGRADE")

        blocked, blocked_gate = svc._apply_daily_pattern_signal_modifier(
            final,
            {"available": True, "bias": "BULL", "labels": ["샛별형"]},
            tech,
            {"decision": "PASS", "fresh_price": 100.0, "quote": {"open": 110.0}},
            {"vwap_45m": {"available": True, "gold": True}},
        )
        self.assertEqual(blocked.final_signal, svc.FINAL_BUY_ALERT)
        self.assertEqual(blocked_gate["decision"], "NO_EFFECT")

    @patch.object(svc.KiwoomInvestorFlowReader, "fetch_stock_flow")
    @patch.object(svc, "_evaluate_kiwoom_freshness_gate")
    @patch.object(svc, "_get_kiwoom_token")
    def test_confirm_watch_only_never_sends_mail(self, token, freshness, fetch_flow):
        token.return_value = "token"
        freshness.return_value = {
            "decision": "PASS",
            "reason": "FRESH_SIGNAL_ALIVE",
            "fresh_price": 1020.0,
        }
        fetch_flow.return_value = [
            {
                "dt": f"202609{day:02d}",
                "foreign_amount": -100.0,
                "institution_amount": -100.0,
                "turnover_amount": 1000.0,
            }
            for day in range(1, 21)
        ]
        result = svc._confirm(
            {
                "ticker": "009150",
                "name": "삼성전기",
                "tech_status": "BUY_CANDIDATE",
                "current_price": 1000.0,
                "atr14": 100.0,
                "buy_trigger_05": 980.0,
                "confirm_trigger_06": 990.0,
                "as_of_date": "2026-10-07",
            }
        )
        self.assertEqual(result["final_signal"], svc.FINAL_WATCH_ONLY)
        self.assertFalse(result["mail_sent"])
        self.assertTrue(result["suppressed_non_mail_signal"])

    @patch.object(svc.KiwoomInvestorFlowReader, "fetch_stock_flow")
    @patch.object(svc, "_evaluate_kiwoom_freshness_gate")
    @patch.object(svc, "_get_kiwoom_token")
    def test_confirm_freshness_veto_short_circuits_flow(self, token, freshness, fetch_flow):
        token.return_value = "token"
        freshness.return_value = {
            "decision": "VETO",
            "reason": "RECENT_1M_SHARP_FALL",
            "fresh_price": 950.0,
        }
        result = svc._confirm(
            {
                "ticker": "009150",
                "name": "삼성전기",
                "tech_status": "BUY_CANDIDATE",
                "current_price": 1000.0,
                "atr14": 100.0,
                "buy_trigger_05": 980.0,
                "as_of_date": "2026-10-07",
            }
        )
        self.assertFalse(result["mail_sent"])
        self.assertTrue(result["suppressed_by_freshness_gate"])
        fetch_flow.assert_not_called()

    @patch.object(svc, "_jev_gate")
    @patch.object(svc, "_evaluate_entry_timing_veto")
    @patch.object(svc, "_evaluate_early_obv_recovery_gate")
    @patch.object(svc.KiwoomInvestorFlowReader, "fetch_stock_flow")
    @patch.object(svc, "_evaluate_kiwoom_freshness_gate")
    @patch.object(svc, "_get_kiwoom_token")
    def test_persistent_early_obv_down_blocks_jev_pre_gate_proceed(
        self, token, freshness, flow, obv, timing, jev,
    ):
        token.return_value = "test-token"
        freshness.return_value = {
            "decision": "PASS",
            "fresh_price": 222000.0,
            "_minute_bars": [],
            "quote": {"change_pct": -0.018},
        }
        flow.return_value = [
            {
                "dt": f"202609{day:02d}",
                "foreign_amount": 100.0,
                "institution_amount": 200.0,
                "turnover_amount": 1000.0,
            }
            for day in range(1, 21)
        ]
        obv.return_value = {
            "decision": "VETO",
            "reason": "PERSISTENT_45M_OBV_DOWN_WITHOUT_MEANINGFUL_RECOVERY",
        }
        result = svc._confirm({
            "ticker": "062040", "name": "산일전기",
            "tech_status": "BUY_CANDIDATE",
            "current_price": 222000.0, "atr14": 12240.0,
            "buy_trigger_05": 209122.0,
            "confirm_trigger_06": 210346.0,
            "as_of_date": "2026-10-12",
            "jev_pre_gate": {
                "status": "OK", "decision": "PROCEED",
                "needs_final_review": False,
            },
        })
        self.assertFalse(result["mail_sent"])
        self.assertTrue(result["suppressed_by_early_obv_gate"])
        self.assertEqual(result["final_signal"], svc.FINAL_WATCH_ONLY)
        self.assertEqual(result["early_obv_gate"]["decision"], "VETO")
        timing.assert_not_called()
        jev.assert_not_called()

    @patch.object(svc.GmailNotifier, "send_email")
    @patch.object(svc, "_evaluate_entry_timing_veto")
    @patch.object(svc, "_evaluate_early_obv_recovery_gate")
    @patch.object(svc.KiwoomInvestorFlowReader, "fetch_stock_flow")
    @patch.object(svc, "_evaluate_kiwoom_freshness_gate")
    @patch.object(svc, "_get_kiwoom_token")
    def test_lg_1019_live_bounce_and_positive_flow_still_cannot_send_buy(
        self, token, freshness, fetch_flow, obv, timing, send_email,
    ):
        token.return_value = "test-token"
        freshness.return_value = {
            "decision": "PASS", "reason": "FRESH_SIGNAL_ALIVE",
            "direction": "RISING", "fresh_price": 387000.0,
            "quote": {"change_pct": -0.033}, "_minute_bars": [],
        }
        fetch_flow.return_value = [
            {"dt": f"202609{day:02d}", "foreign_amount": 10000.0,
             "institution_amount": 15000.0, "turnover_amount": 50000.0}
            for day in range(1, 21)
        ]
        obv.return_value = {
            "decision": "PASS", "reason": "LIVE_PARTIAL_OBV_GOLD_WITH_VOLUME",
        }
        timing.return_value = {
            "decision": "VETO",
            "reason": "45M_WEAK_AND_PRICE_BELOW_VWAP9",
            "fresh_price": 387000.0, "vwap9": 396625.0,
        }
        result = svc._confirm({
            "ticker": "373220", "name": "LG에너지솔루션",
            "tech_status": "BUY_CANDIDATE", "current_price": 393500.0,
            "atr14": 17362.5, "buy_trigger_05": 366181.0,
            "confirm_trigger_06": 367918.0, "as_of_date": "2026-10-13",
            "jev_pre_gate": {
                "status": "OK", "decision": "PROCEED",
                "needs_final_review": False,
            },
        })
        self.assertFalse(result["mail_sent"])
        self.assertTrue(result["suppressed_by_timing_gate"])
        self.assertEqual(result["final_signal"], svc.FINAL_WATCH_ONLY)
        self.assertIn("45M_WEAK_AND_PRICE_BELOW_VWAP9", result["flow_summary"])
        timing.assert_called_once_with("373220", fresh_price=387000.0)
        send_email.assert_not_called()

    @patch.object(svc, "_fetch_kiwoom_minute_bars")
    @patch.object(svc, "_fetch_kiwoom_quote")
    @patch.object(svc, "_get_kiwoom_token")
    def test_etf_down_kiwoom_live_confirms_not_sheet_price(self, token, quote, minute):
        token.return_value = "fake"
        quote.return_value = {"available": True, "current_price": 94535, "change_pct": -0.0696,
                              "source": "KIWOOM_KA10001"}
        result = svc._evaluate_etf_freshness({"ticker": "069500", "stage": "▼", "sheet_change_pct": 0.01})
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["fresh_price"], 94535)
        self.assertAlmostEqual(result["change_pct"], -0.0696)
        minute.assert_not_called()

    @patch.object(svc, "_fetch_kiwoom_quote")
    @patch.object(svc, "_get_kiwoom_token")
    def test_market_risk_live_requires_minus_three_percent(self, token, quote):
        token.return_value = "fake"
        quote.return_value = {"available": True, "current_price": 94535, "change_pct": -0.0696}
        result = svc._evaluate_etf_freshness({"ticker": "069500", "stage": "RISK"})
        self.assertEqual(result["decision"], "PASS")
        quote.return_value = {"available": True, "current_price": 99000, "change_pct": -0.02}
        self.assertEqual(svc._evaluate_etf_freshness({"ticker": "069500", "stage": "RISK"})["decision"], "VETO")
        with self.assertRaises(svc.SignalServiceError):
            svc._evaluate_etf_freshness({"ticker": "114800", "stage": "RISK"})

    @patch.object(svc, "_fetch_kiwoom_quote")
    @patch.object(svc, "_get_kiwoom_token")
    def test_etf_down_does_not_send_if_kiwoom_is_green_or_missing(self, token, quote):
        token.return_value = "fake"
        quote.return_value = {"available": True, "current_price": 107000, "change_pct": 0.01}
        self.assertEqual(svc._evaluate_etf_freshness({"ticker": "069500", "stage": "▼"})["decision"], "VETO")
        quote.return_value = {"available": False, "reason": "KA10001_HTTP_429"}
        self.assertEqual(svc._evaluate_etf_freshness({"ticker": "069500", "stage": "▽"})["decision"], "VETO")

    @patch.object(svc, "_jev_gate")
    def test_jev_gate_uses_existing_shared_token(self, mocked):
        mocked.return_value = {"ok": True, "status": "NOT_CONFIGURED", "decision": "SEND"}
        res = self.client.post(
            "/jev-gate",
            headers={"X-StockBot-Token": "test-token"},
            json={"source": "ETF", "ticker": "069500", "name": "KODEX 200"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["decision"], "SEND")
        mocked.assert_called_once()

    def test_user_facing_signal_language_matches_held_monitor(self):
        self.assertEqual(svc.SIGNAL_LABELS[svc.FINAL_WATCH_ONLY], "· 관찰")
        self.assertEqual(svc.SIGNAL_LABELS[svc.FINAL_BUY_ALERT], "△ 매수조짐")
        self.assertEqual(svc.SIGNAL_LABELS[svc.FINAL_BUY_ALERT_STRONG], "▲ 매수확인")
        self.assertEqual(svc.SIGNAL_COLORS[svc.FINAL_WATCH_ONLY], "#64748B")
        self.assertEqual(svc.SIGNAL_COLORS[svc.FINAL_BUY_ALERT], "#DC2626")
        self.assertEqual(svc.SIGNAL_COLORS[svc.FINAL_BUY_ALERT_STRONG], "#DC2626")


if __name__ == "__main__":
    unittest.main()
