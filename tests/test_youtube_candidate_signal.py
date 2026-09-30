import unittest
from datetime import date

from src.analysis.youtube_candidate_signal import (
    BUY_REBOUND_ATR,
    FLOW_NEGATIVE,
    FLOW_NEUTRAL,
    FLOW_POSITIVE,
    FLOW_UNAVAILABLE,
    FINAL_BUY_ALERT,
    FINAL_BUY_ALERT_STRONG,
    FINAL_WATCH_ONLY,
    FINAL_WATCH_ONLY_DATA_GAP,
    REGIME_NEGATIVE,
    TECH_BUY_CANDIDATE,
    TECH_EARLY_READY,
    TECH_READY,
    FlowDay,
    KiwoomInvestorFlowReader,
    TechnicalInput,
    classify_market_regime,
    combine_final_signal,
    evaluate_flow,
    evaluate_technical,
)


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers, json, timeout))
        return self.responses.pop(0)


def base_tech(current):
    return TechnicalInput(
        close=105,
        current_price=current,
        reference_low=100,
        atr14=10,
        ma20=103,
        ma60=95,
        ma20_slope_5d=1,
        rsi14=55,
        pullback_ready=True,
    )


def make_flow(foreign_values, inst_values, turnover=1000):
    return [
        FlowDay(f"202609{i+1:02d}", f, n, turnover)
        for i, (f, n) in enumerate(zip(foreign_values, inst_values))
    ]


class SignalTests(unittest.TestCase):
    def test_04_is_early_ready_not_buy(self):
        a = evaluate_technical(base_tech(104.5))
        self.assertEqual(a.status, TECH_EARLY_READY)

    def test_05_is_buy_candidate(self):
        a = evaluate_technical(base_tech(105.0))
        self.assertEqual(a.status, TECH_BUY_CANDIDATE)
        self.assertAlmostEqual(a.buy_trigger_05, 105.0)
        self.assertAlmostEqual(BUY_REBOUND_ATR, 0.5)

    def test_below_04_stays_ready(self):
        a = evaluate_technical(base_tech(103.9))
        self.assertEqual(a.status, TECH_READY)

    def test_06_is_confirmation_not_separate_alert_state(self):
        a = evaluate_technical(base_tech(106.0))
        self.assertEqual(a.status, TECH_BUY_CANDIDATE)
        self.assertTrue(a.confirmed_06)

    def test_positive_flow(self):
        rows = make_flow([1]*20, [2]*20)
        a = evaluate_flow(rows, source_verified=True)
        self.assertEqual(a.status, FLOW_POSITIVE)
        self.assertGreater(a.flow_strength_5d, 0)

    def test_negative_flow(self):
        rows = make_flow([-1]*20, [-2]*20)
        a = evaluate_flow(rows, source_verified=True)
        self.assertEqual(a.status, FLOW_NEGATIVE)

    def test_neutral_when_directions_mixed(self):
        rows = make_flow([2]*20, [-2]*20)
        a = evaluate_flow(rows, source_verified=True)
        self.assertEqual(a.status, FLOW_NEUTRAL)

    def test_unverified_zero_legacy_flow_is_unavailable(self):
        rows = make_flow([0]*20, [0]*20)
        a = evaluate_flow(rows, source_verified=False)
        self.assertEqual(a.status, FLOW_UNAVAILABLE)

    def test_market_regime_negative(self):
        m = classify_market_regime(-100, -200, -50)
        self.assertEqual(m.status, REGIME_NEGATIVE)
        self.assertTrue(m.program_alignment)

    def test_final_mapping(self):
        tech = evaluate_technical(base_tech(105.0))
        pos = evaluate_flow(make_flow([1]*20, [1]*20), source_verified=True)
        neu = evaluate_flow(make_flow([1]*20, [-1]*20), source_verified=True)
        neg = evaluate_flow(make_flow([-1]*20, [-1]*20), source_verified=True)
        gap = evaluate_flow(make_flow([0]*20, [0]*20), source_verified=False)
        self.assertEqual(combine_final_signal(tech, pos).final_signal, FINAL_BUY_ALERT_STRONG)
        self.assertEqual(combine_final_signal(tech, neu).final_signal, FINAL_BUY_ALERT)
        self.assertEqual(combine_final_signal(tech, neg).final_signal, FINAL_WATCH_ONLY)
        self.assertEqual(combine_final_signal(tech, gap).final_signal, FINAL_WATCH_ONLY_DATA_GAP)

    def test_kiwoom_reader_requests_amount_net_buy_and_parses_signs(self):
        payload = {
            "stk_invsr_orgn": [
                {"dt": "20260930", "frgnr_invsr": "+1,200", "orgn": "--300", "acc_trde_prica": "10,000"}
            ],
            "return_code": 0,
            "return_msg": "OK",
        }
        session = FakeSession([FakeResponse(payload)])
        reader = KiwoomInvestorFlowReader("token", session=session)
        rows = reader.fetch_stock_flow("5930", date(2026, 9, 30))
        self.assertEqual(rows[0].foreign_amount, 1200)
        self.assertEqual(rows[0].institution_amount, -300)
        body = session.calls[0][2]
        self.assertEqual(body["amt_qty_tp"], "1")
        self.assertEqual(body["trde_tp"], "0")
        self.assertEqual(body["stk_cd"], "005930")


if __name__ == "__main__":
    unittest.main()
