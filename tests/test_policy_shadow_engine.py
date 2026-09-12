"""Unit coverage for the pure, offline Policy Shadow calculations."""

import unittest

from src.policy.policy_shadow_engine import (
    DATA_GAP,
    LOSS_DEFENSE_NOT_DETERMINABLE,
    LOSS_DEFENSE_ON,
    PREEXISTING_HARD_STOP_BREACH,
    DailyRange,
    IntradayPoint,
    calculate_daily_rebound_threshold,
    calculate_hard_stop,
    current_only_hard_stop_status,
    evaluate_intraday_defense_path,
    evaluate_loss_defense,
    t_gate_for_loss_band,
)


class TestPolicyShadowEngine(unittest.TestCase):
    def test_01_mid_loss_t_minus_8_triggers_when_45m_bearish(self):
        self.assertEqual(
            evaluate_loss_defense(loss_pct=-20.0, t_change_3d=-8.0, is_45m_bearish=True),
            LOSS_DEFENSE_ON,
        )

    def test_02_deep_loss_t_minus_5_triggers_when_45m_bearish(self):
        self.assertEqual(
            evaluate_loss_defense(loss_pct=-30.0, t_change_3d=-5.0, is_45m_bearish=True),
            LOSS_DEFENSE_ON,
        )

    def test_03_boundary_point_one_short_of_mid_threshold_fails(self):
        self.assertEqual(t_gate_for_loss_band(-20.0, -7.9), "FAIL")

    def test_04_daily_low_calculates_rebound_threshold_only(self):
        result = calculate_daily_rebound_threshold([DailyRange("2026-08-20", 110.0, 90.0)], 10.0)
        self.assertEqual(result.rebound_threshold, 96.0)
        self.assertEqual(result.grade, "APPROXIMATE_DAILY_RANGE")

    def test_05_daily_range_never_claims_rebound_occurrence(self):
        result = calculate_daily_rebound_threshold([DailyRange("2026-08-20", 110.0, 90.0)], 10.0)
        self.assertEqual(result.rebound_occurrence, DATA_GAP)

    def test_06_daily_range_never_claims_trail_occurrence(self):
        result = calculate_daily_rebound_threshold([DailyRange("2026-08-20", 110.0, 90.0)], 10.0)
        self.assertEqual(result.trail_occurrence, DATA_GAP)

    def test_07_daily_high_does_not_prove_a_rebound_or_peak(self):
        result = calculate_daily_rebound_threshold([DailyRange("2026-08-20", 130.0, 90.0)], 10.0)
        self.assertIsNone(result.defense_peak)
        self.assertIsNone(result.trail_threshold)

    def test_08_ordered_intraday_path_allows_low_rebound_peak_and_trail(self):
        result = evaluate_intraday_defense_path(
            [
                IntradayPoint("09:00", 100.0),
                IntradayPoint("09:15", 90.0),
                IntradayPoint("09:30", 96.0),
                IntradayPoint("09:45", 110.0),
                IntradayPoint("10:00", 106.0),
            ],
            10.0,
        )
        self.assertEqual(result.rebound_timestamp, "09:30")
        self.assertEqual(result.defense_peak, 110.0)
        self.assertEqual(result.trail_threshold, 106.0)
        self.assertEqual(result.trail_trigger_timestamp, "10:00")

    def test_09_intraday_trail_is_peak_minus_point_4_atr(self):
        result = evaluate_intraday_defense_path(
            [IntradayPoint("09:00", 90.0), IntradayPoint("09:15", 96.0), IntradayPoint("09:30", 120.0)],
            10.0,
        )
        self.assertEqual(result.trail_threshold, 116.0)

    def test_10_intraday_trail_never_moves_down(self):
        result = evaluate_intraday_defense_path(
            [IntradayPoint("09:00", 90.0), IntradayPoint("09:15", 96.0), IntradayPoint("09:30", 120.0)],
            10.0,
            previous_trail=118.0,
        )
        self.assertEqual(result.trail_threshold, 118.0)

    def test_11_hard_stop_is_78_percent_of_average(self):
        result = calculate_hard_stop(10000.0)
        self.assertEqual(result.candidate, 7800.0)
        self.assertEqual(result.hard_stop, 7800.0)

    def test_12_hard_stop_never_moves_down(self):
        result = calculate_hard_stop(10000.0, previous_hard_stop=8000.0)
        self.assertEqual(result.hard_stop, 8000.0)

    def test_13_preexisting_hard_stop_breach_is_not_an_exit(self):
        _, status = current_only_hard_stop_status(current_price=7700.0, current_weighted_avg_price=10000.0)
        self.assertEqual(status, PREEXISTING_HARD_STOP_BREACH)

    def test_14_missing_historical_input_does_not_get_inferred(self):
        self.assertEqual(
            evaluate_loss_defense(loss_pct=None, t_change_3d=-10.0, is_45m_bearish=None),
            LOSS_DEFENSE_NOT_DETERMINABLE,
        )
        self.assertEqual(t_gate_for_loss_band(None, -10.0), DATA_GAP)


if __name__ == "__main__":
    unittest.main()
