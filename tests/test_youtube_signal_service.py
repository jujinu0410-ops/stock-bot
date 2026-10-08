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

    def test_confirm_requires_shared_token(self):
        res = self.client.post("/confirm", json={"ticker": "007660", "tech_status": "BUY_CANDIDATE"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"], "UNAUTHORIZED")

    @patch.object(svc, "_confirm")
    def test_confirm_passes_only_authenticated_payload(self, mocked):
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
    @patch.object(svc, "_fetch_daily_obv_gate")
    def test_entry_timing_veto_when_daily_obv_and_45m_vwap_both_not_gold(self, daily, vwap):
        daily.return_value = {"available": True, "gold": False, "trend_down": True}
        vwap.return_value = {"available": True, "gold": False, "state": "VWAP_DEAD"}
        gate = svc._evaluate_entry_timing_veto("316140")
        self.assertEqual(gate["decision"], "VETO")
        self.assertEqual(gate["reason"], "DAILY_OBV_NOT_GOLD_AND_45M_VWAP_NOT_GOLD")

    @patch.object(svc, "_fetch_45m_vwap_gate")
    @patch.object(svc, "_fetch_daily_obv_gate")
    def test_entry_timing_gate_passes_if_either_signal_is_gold(self, daily, vwap):
        daily.return_value = {"available": True, "gold": False}
        vwap.return_value = {"available": True, "gold": True, "state": "VWAP_GOLD"}
        gate = svc._evaluate_entry_timing_veto("316140")
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
            {"fresh_price": 214000, "quote": {"open": 231000}},
            {"vwap_45m": {"available": True, "gold": False, "state": "VWAP_DEAD"}},
        )
        self.assertEqual(final.final_signal, svc.FINAL_BUY_ALERT)
        self.assertEqual(gate["decision"], "DOWNGRADE_TO_BUY_ALERT")
        self.assertIn("45M_VWAP_NOT_GOLD", gate["reason"])
        self.assertIn("INTRADAY_DROP_GE_5PCT", gate["reason"])

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
            {"fresh_price": 105000, "quote": {"open": 104000}},
            {"vwap_45m": {"available": True, "gold": True, "state": "VWAP_GOLD"}},
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
