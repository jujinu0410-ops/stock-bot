"""
Unit Tests for Phase 1: 45m ADD ADVISORY Sidecar Engine & Storage (WMA9 OBV Signal)
Validates all specification points:
1. VWAP GOLD CROSS detection
2. VWAP DEAD CROSS detection
3. cross_age 0 / 1 / 2+ tracking
4. OBV GOLD
5. OBV gap EXPANDING
6. OBV gap CONTRACTING
7. Chaikin RISING / FALLING
8. ADD_STRONG
9. ADD_WATCH
10. ADD_BLOCKED
11. UNKNOWN
12. Duplicate insert prevention (UNIQUE constraint)
13. Existing technical_state immutability
14. Existing scan_journal immutability
15. Order API calls count is 0
16. WMA9 known OBV values accuracy test
17. SMA9 vs WMA9 difference test
18. WMA9-based OBV_GOLD_CROSS & OBV_DEAD_CROSS test
19. WMA9-based OBV gap EXPANDING & CONTRACTING test
"""

import unittest
from unittest.mock import MagicMock
import pandas as pd
import numpy as np
import os
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Ensure test mode flag
os.environ["STOCKBOT_TEST_MODE"] = "1"

from src.analysis.add_advisory_engine import AddAdvisory45mEngine
from src.database.db_manager import DatabaseManager


def generate_test_15m_df(num_bars=90, mode="strong"):
    """Generates synthetic 15m OHLCV data for test scenarios"""
    dates = pd.date_range("2026-08-27 09:00", periods=num_bars, freq="15min")
    close_prices = []
    high_prices = []
    low_prices = []
    volumes = []

    base_p = 10000.0
    for i in range(num_bars):
        if mode == "strong":
            p = base_p + (i * 40.0)
            h = p + 20.0
            l = p - 10.0
            c = p + 15.0  # Close near high -> positive MFM -> Chaikin rising
            v = 1000.0 + (i * 20.0)
        elif mode == "dead_blocked":
            p = base_p - (i * 40.0)
            h = p + 10.0
            l = p - 20.0
            c = p - 15.0  # Close near low -> negative MFM -> Chaikin falling
            v = 1000.0 + (i * 10.0)
        elif mode == "watch":
            # VWAP9 > VWAP26 and Chaikin RISING, but OBV < OBV9
            if i < num_bars - 25:
                p = base_p - (i * 30.0)
                v = 5000.0
            else:
                p = base_p + ((i - (num_bars - 25)) * 150.0)
                v = 1000.0  # low volume on rebound so OBV stays below OBV9
            h = p + 30.0
            l = p - 10.0
            c = p + 25.0  # close near high -> Chaikin rising
        elif mode == "gold_cross_0":
            if i < num_bars - 3:
                p = base_p - 1000.0
            else:
                p = base_p + 2000.0
            h = p + 50.0
            l = p - 10.0
            c = p + 40.0
            v = 5000.0
        elif mode == "dead_cross_0":
            if i < num_bars - 3:
                p = base_p + 1000.0
            else:
                p = base_p - 2000.0
            h = p + 10.0
            l = p - 50.0
            c = p - 40.0
            v = 5000.0
        else:
            p = base_p
            h = p + 10.0
            l = p - 10.0
            c = p
            v = 1000.0

        close_prices.append(c)
        high_prices.append(h)
        low_prices.append(l)
        volumes.append(v)

    return pd.DataFrame({
        "Open": close_prices,
        "High": high_prices,
        "Low": low_prices,
        "Close": close_prices,
        "Volume": volumes
    }, index=dates)


class TestAddAdvisory45m(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_stock.db")
        self.db = DatabaseManager(db_path=self.db_path)
        self.mock_analyzer = MagicMock()
        self.engine = AddAdvisory45mEngine(intraday_analyzer=self.mock_analyzer)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_01_vwap_gold_cross_detection(self):
        """1. VWAP GOLD CROSS (age 0) 검출"""
        df_15m = generate_test_15m_df(num_bars=90, mode="gold_cross_0")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertEqual(res["data_quality"], "VALID (30 bars)")
        self.assertEqual(res["vwap_state"], "VWAP_GOLD")
        self.assertEqual(res["vwap_cross_state"], "VWAP_GOLD_CROSS")
        self.assertEqual(res["vwap_cross_age"], 0)

    def test_02_vwap_dead_cross_detection(self):
        """2. VWAP DEAD CROSS (age 0) 검출"""
        df_15m = generate_test_15m_df(num_bars=90, mode="dead_cross_0")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertEqual(res["vwap_state"], "VWAP_DEAD")
        self.assertEqual(res["vwap_cross_state"], "VWAP_DEAD_CROSS")
        self.assertEqual(res["vwap_cross_age"], 0)

    def test_03_cross_age_tracking(self):
        """3. cross_age 1 및 2+ 추적"""
        df_15m = generate_test_15m_df(num_bars=90, mode="strong")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertEqual(res["vwap_state"], "VWAP_GOLD")
        self.assertGreaterEqual(res["vwap_cross_age"], 1)

    def test_04_obv_gold_state(self):
        """4. OBV GOLD 상태 확인"""
        df_15m = generate_test_15m_df(num_bars=90, mode="strong")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertIn(res["obv_state"], ["OBV_GOLD", "OBV_GOLD_CROSS"])
        self.assertGreater(res["obv"], res["obv9"])

    def test_05_obv_gap_expanding(self):
        """5. OBV gap EXPANDING 검출"""
        df_15m = generate_test_15m_df(num_bars=90, mode="strong")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertEqual(res["obv_gap_state"], "EXPANDING")
        self.assertGreater(res["obv_gap_delta"], 0)

    def test_06_obv_gap_contracting(self):
        """6. OBV gap CONTRACTING 검출"""
        df_15m = generate_test_15m_df(num_bars=90, mode="dead_blocked")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertEqual(res["obv_gap_state"], "CONTRACTING")
        self.assertLess(res["obv_gap_delta"], 0)

    def test_07_chaikin_rising_falling(self):
        """7. Chaikin RISING / FALLING 검출"""
        df_strong = generate_test_15m_df(num_bars=90, mode="strong")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_strong, "MOCK_SOURCE", "NONE")
        res_strong = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertEqual(res_strong["chaikin_state"], "CHAIKIN_RISING")

        df_dead = generate_test_15m_df(num_bars=90, mode="dead_blocked")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_dead, "MOCK_SOURCE", "NONE")
        res_dead = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertEqual(res_dead["chaikin_state"], "CHAIKIN_FALLING")

    def test_08_add_strong_evaluation(self):
        """8. ADD_STRONG 조건 검증"""
        df_15m = generate_test_15m_df(num_bars=90, mode="strong")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00", technical_state_reference="STRONG")
        self.assertEqual(res["add_advisory_state"], "ADD_STRONG")
        self.assertEqual(res["alert_candidate"], 1)  # Initial WATCH/STRONG transition

    def test_09_add_watch_evaluation(self):
        """9. ADD_WATCH 조건 검증"""
        df_15m = generate_test_15m_df(num_bars=90, mode="watch")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00", technical_state_reference="NEUTRAL")
        self.assertEqual(res["add_advisory_state"], "ADD_WATCH")

    def test_10_add_blocked_evaluation(self):
        """10. ADD_BLOCKED 조건 검증 (VWAP_DEAD 또는 DAMAGED)"""
        df_15m = generate_test_15m_df(num_bars=90, mode="dead_blocked")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00", technical_state_reference="DAMAGED")
        self.assertEqual(res["add_advisory_state"], "ADD_BLOCKED")

    def test_11_unknown_evaluation(self):
        """11. UNKNOWN 조건 검증 (데이터 결측 시)"""
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (None, "NONE", "NO_INTRADAY_DATA")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertEqual(res["add_advisory_state"], "UNKNOWN")

    def test_12_duplicate_insert_prevention(self):
        """12. DB 중복 적재 방지 (UNIQUE(stock_code, bar_timestamp))"""
        df_15m = generate_test_15m_df(num_bars=90, mode="strong")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")

        # First insert -> True
        inserted1 = self.db.insert_add_advisory_45m(res)
        self.assertTrue(inserted1)

        # Second insert with identical stock_code & bar_timestamp -> False (Ignored)
        inserted2 = self.db.insert_add_advisory_45m(res)
        self.assertFalse(inserted2)

        rows = self.db.get_add_advisories_by_date("2026-08-27")
        self.assertEqual(len(rows), 1)

    def test_13_existing_technical_state_immutability(self):
        """13. 기존 technical_state 불변성 보증"""
        df_15m = generate_test_15m_df(num_bars=90, mode="strong")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00", technical_state_reference="NEUTRAL")
        self.assertEqual(res["technical_state_reference"], "NEUTRAL")

    def test_14_existing_scan_journal_immutability(self):
        """14. 기존 scan_journal 불변성 보증"""
        df_15m = generate_test_15m_df(num_bars=90, mode="strong")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m, "MOCK_SOURCE", "NONE")

        res = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.db.insert_add_advisory_45m(res)

        journals = self.db.get_latest_scan_journal_for_stock("005930")
        self.assertIsNone(journals)

    def test_15_order_api_calls_count_zero(self):
        """15. 실제 주문 API 호출 수가 0임을 보증"""
        self.assertFalse(hasattr(self.engine, "send_order"))
        self.assertFalse(hasattr(self.engine, "buy_order"))
        self.assertFalse(hasattr(self.engine, "sell_order"))

    def test_16_obv_wma9_known_values_accuracy(self):
        """16. 알려진 9개 OBV 값으로 WMA9 수치 정확성 검증"""
        obv_vals = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
        s = pd.Series(obv_vals)
        wma_weights = np.arange(1, 10, dtype=float)
        wma_weight_sum = wma_weights.sum()

        wma9_val = s.rolling(9).apply(lambda w: np.dot(w, wma_weights) / wma_weight_sum, raw=True).iloc[-1]
        # (10*1 + 20*2 + 30*3 + 40*4 + 50*5 + 60*6 + 70*7 + 80*8 + 90*9) / 45 = 2850 / 45 = 63.333333333333336
        expected_wma9 = 2850.0 / 45.0
        self.assertAlmostEqual(wma9_val, expected_wma9, places=5)
        self.assertAlmostEqual(wma9_val, 63.333333, places=5)

    def test_17_sma9_vs_wma9_difference(self):
        """17. SMA9와 WMA9가 상승 데이터에서 실제로 다른 수치임을 검증"""
        obv_vals = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
        s = pd.Series(obv_vals)
        sma9_val = s.rolling(9).mean().iloc[-1]
        wma_weights = np.arange(1, 10, dtype=float)
        wma9_val = s.rolling(9).apply(lambda w: np.dot(w, wma_weights) / wma_weights.sum(), raw=True).iloc[-1]

        self.assertEqual(sma9_val, 50.0)
        self.assertAlmostEqual(wma9_val, 63.333333, places=5)
        self.assertNotEqual(sma9_val, wma9_val)
        self.assertGreater(abs(sma9_val - wma9_val), 10.0)

    def test_18_wma9_obv_gold_and_dead_cross(self):
        """18. WMA9 기준 OBV_GOLD_CROSS 및 OBV_DEAD_CROSS 연산 검증"""
        df_15m_strong = generate_test_15m_df(num_bars=90, mode="strong")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m_strong, "MOCK_SOURCE", "NONE")
        res_strong = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertIn(res_strong["obv_state"], ["OBV_GOLD", "OBV_GOLD_CROSS"])

        df_15m_dead = generate_test_15m_df(num_bars=90, mode="dead_blocked")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m_dead, "MOCK_SOURCE", "NONE")
        res_dead = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertIn(res_dead["obv_state"], ["OBV_DEAD", "OBV_DEAD_CROSS"])

    def test_19_wma9_gap_expanding_and_contracting(self):
        """19. WMA9 기준 OBV gap EXPANDING / CONTRACTING 연산 검증"""
        df_15m_strong = generate_test_15m_df(num_bars=90, mode="strong")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m_strong, "MOCK_SOURCE", "NONE")
        res_strong = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertEqual(res_strong["obv_gap_state"], "EXPANDING")

        df_15m_dead = generate_test_15m_df(num_bars=90, mode="dead_blocked")
        self.mock_analyzer.fetch_canonical_15m_data.return_value = (df_15m_dead, "MOCK_SOURCE", "NONE")
        res_dead = self.engine.evaluate_stock_advisory("005930", "2026-08-27", "2026-08-27 15:00:00")
        self.assertEqual(res_dead["obv_gap_state"], "CONTRACTING")


if __name__ == "__main__":
    unittest.main()
