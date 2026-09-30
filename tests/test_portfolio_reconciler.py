# -*- coding: utf-8 -*-
"""
test_portfolio_reconciler.py

Unit tests for PortfolioReconciler and Strategy Validation:
1. avg 변경 + stop < current -> NORMAL
2. avg 변경 + stop >= current -> REVIEW_REQUIRED
3. stop > avg 이지만 current > stop -> PROFIT_PROTECTION_OK (NORMAL)
4. stop 없음 -> MISSING (UNCONFIGURED)
5. target <= current -> REACHED_OR_STALE (TARGET_REACHED)
6. target <= avg -> INVALID_OR_REVIEW (REVIEW_REQUIRED)
7. qty 변경만 있고 전략값 정상 -> revalidation_required=True, strategy_status=NORMAL
8. CLOSED 종목은 strategy validation 대상에서 제외 (status=CLOSED)
9. SUSPENDED 종목은 기존 상태 보존 (status=SUSPENDED)
10. 전략 검증 실패가 잔고 sync 자체를 막지 않음 (Fail-safe)
11. 기존 strategy config 값 불변
12. Safety validation (None, empty, zero valid positions)
13. Snapshot persistence
"""

import json
import shutil
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.engine.portfolio_reconciler import (
    PortfolioReconciler,
    validate_position_strategy,
    compute_atr_metrics,
    parse_price,
)


class TestPortfolioReconciler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.state_dir = self.temp_dir / "stockbot_state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.reconciler = PortfolioReconciler(
            spreadsheet_id="test_sheet_id",
            state_dir=self.state_dir,
            token="mock_token",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Safety Gates
    # -------------------------------------------------------------------------
    def test_safety_gate_none_positions(self):
        """None positions must be rejected with fail-closed status."""
        res = self.reconciler.reconcile(None, sync_to_sheet=False)
        self.assertEqual(res["sync_status"], "FAILED")
        self.assertEqual(res["fail_reason"], "LIVE_POSITIONS_IS_NONE")
        self.assertEqual(res["holdings_count"], 0)

    def test_safety_gate_empty_positions(self):
        """Empty list must be rejected to prevent 0-stock wipeout."""
        res = self.reconciler.reconcile([], sync_to_sheet=False)
        self.assertEqual(res["sync_status"], "FAILED")
        self.assertEqual(res["fail_reason"], "EMPTY_LIVE_POSITIONS_REJECTED")

    def test_safety_gate_zero_valid_positions(self):
        """Positions with 0 quantity or 0 price must be rejected."""
        invalid = [
            {"stock_code": "010140", "quantity": 0, "avg_buy_price": 20000},
            {"stock_code": "", "quantity": 100, "avg_buy_price": 20000},
        ]
        res = self.reconciler.reconcile(invalid, sync_to_sheet=False)
        self.assertEqual(res["sync_status"], "FAILED")
        self.assertEqual(res["fail_reason"], "ZERO_VALID_POSITIONS_FOUND")

    # -------------------------------------------------------------------------
    # Strategy Validation Rule Unit Tests (Section 10)
    # -------------------------------------------------------------------------
    def test_rule_1_avg_changed_stop_below_current(self):
        """1. avg 변경 + stop < current -> NORMAL"""
        strat = {"ticker": "010140", "stop_price": "18000", "target1": "25000", "status": "ACTIVE"}
        live = {"stock_code": "010140", "quantity": 1000, "avg_buy_price": 20000, "current_price": 21000}
        val = validate_position_strategy(strat, live, prev_qty=500, prev_avg=19000)

        self.assertTrue(val["strategy_revalidation_required"])
        self.assertIn("AVG_PRICE_CHANGED", val["revalidation_reasons"])
        self.assertEqual(val["stop_status"], "ACTIVE")
        self.assertEqual(val["strategy_status"], "NORMAL")

    def test_rule_2_avg_changed_stop_above_or_equal_current(self):
        """2. avg 변경 + stop >= current -> REVIEW_REQUIRED (삼성중공업 실제 사례)"""
        strat = {"ticker": "010140", "stop_price": "20950", "target1": "23306", "status": "ACTIVE"}
        live = {"stock_code": "010140", "quantity": 1013, "avg_buy_price": 20119, "current_price": 19850}
        val = validate_position_strategy(strat, live, prev_qty=406, prev_avg=20686)

        self.assertTrue(val["strategy_revalidation_required"])
        self.assertIn("QTY_CHANGED", val["revalidation_reasons"])
        self.assertIn("AVG_PRICE_CHANGED", val["revalidation_reasons"])
        self.assertEqual(val["stop_status"], "BREACHED_OR_STALE")
        self.assertEqual(val["strategy_status"], "REVIEW_REQUIRED")
        self.assertIn("추가매수로 평단 변경 + 현재가가 기존 손절선 하회", val["strategy_warning"])

    def test_rule_3_stop_above_avg_but_current_above_stop(self):
        """3. stop > avg 이지만 current > stop -> PROFIT_PROTECTION_OK (NORMAL)"""
        strat = {"ticker": "005930", "stop_price": "75000", "target1": "85000", "status": "ACTIVE"}
        live = {"stock_code": "005930", "quantity": 100, "avg_buy_price": 70000, "current_price": 80000}
        val = validate_position_strategy(strat, live, prev_qty=100, prev_avg=70000)

        self.assertEqual(val["stop_status"], "PROFIT_PROTECTION_OK")
        self.assertEqual(val["strategy_status"], "NORMAL")

    def test_rule_4_missing_stop_price(self):
        """4. stop 없음 -> MISSING (UNCONFIGURED)"""
        strat = {"ticker": "035720", "stop_price": "", "target1": "50000", "status": "ACTIVE"}
        live = {"stock_code": "035720", "quantity": 50, "avg_buy_price": 45000, "current_price": 46000}
        val = validate_position_strategy(strat, live, prev_qty=50, prev_avg=45000)

        self.assertEqual(val["stop_status"], "MISSING")
        self.assertEqual(val["strategy_status"], "UNCONFIGURED")

    def test_rule_5_target_reached_or_stale(self):
        """5. target <= current -> REACHED_OR_STALE (TARGET_REACHED)"""
        strat = {"ticker": "005930", "stop_price": "65000", "target1": "70000", "status": "ACTIVE"}
        live = {"stock_code": "005930", "quantity": 10, "avg_buy_price": 60000, "current_price": 72000}
        val = validate_position_strategy(strat, live, prev_qty=10, prev_avg=60000)

        self.assertEqual(val["target1_status"], "REACHED_OR_STALE")
        self.assertEqual(val["strategy_status"], "TARGET_REACHED")

    def test_rule_6_target_below_avg(self):
        """6. target <= avg -> INVALID_OR_REVIEW (REVIEW_REQUIRED)"""
        strat = {"ticker": "005930", "stop_price": "65000", "target1": "68000", "status": "ACTIVE"}
        live = {"stock_code": "005930", "quantity": 10, "avg_buy_price": 70000, "current_price": 69000}
        val = validate_position_strategy(strat, live, prev_qty=10, prev_avg=70000)

        self.assertEqual(val["target1_status"], "INVALID_OR_REVIEW")
        self.assertEqual(val["strategy_status"], "REVIEW_REQUIRED")

    def test_rule_7_qty_changed_only_strategy_valid(self):
        """7. qty 변경만 있고 전략값 정상 -> revalidation_required=True, strategy_status=NORMAL"""
        strat = {"ticker": "005930", "stop_price": "65000", "target1": "80000", "status": "ACTIVE"}
        live = {"stock_code": "005930", "quantity": 20, "avg_buy_price": 70000, "current_price": 72000}
        val = validate_position_strategy(strat, live, prev_qty=10, prev_avg=70000)

        self.assertTrue(val["strategy_revalidation_required"])
        self.assertEqual(val["revalidation_reasons"], ["QTY_CHANGED"])
        self.assertEqual(val["strategy_status"], "NORMAL")

    def test_rule_9_suspended_preserves_status(self):
        """9. SUSPENDED 종목은 기존 상태 보존 (strategy_status = SUSPENDED)"""
        strat = {"ticker": "234920", "stop_price": "HOLD", "target1": "", "status": "SUSPENDED_HOLD"}
        live = {"stock_code": "234920", "quantity": 9314, "avg_buy_price": 7517, "current_price": 5310}
        val = validate_position_strategy(strat, live, prev_qty=9314, prev_avg=7517)

        self.assertEqual(val["stop_status"], "SUSPENDED_HOLD")
        self.assertEqual(val["strategy_status"], "SUSPENDED")

    # -------------------------------------------------------------------------
    # End-to-End Reconciliation & Strategy Config Invariance
    # -------------------------------------------------------------------------
    @patch.object(PortfolioReconciler, "read_remote_config")
    @patch.object(PortfolioReconciler, "write_remote_config")
    def test_reconciliation_end_to_end_and_strategy_invariance(self, mock_write, mock_read):
        """
        Verify that:
        - 8. CLOSED stock (086450) is excluded from live positions and marked CLOSED
        - 11. Existing strategy config targets and stop prices are NEVER altered
        - Columns O & P (STRATEGY_STATUS, STRATEGY_WARNING) are added
        - Leading zero in ticker is preserved with '
        """
        mock_read.return_value = [
            ["TICKER", "EXCHANGE", "NAME", "QTY", "AVG_PRICE", "REF_CLOSE", "STOP_PRICE", "STATUS", "BASE_DATE", "ENABLED", "TARGET1", "TARGET2", "TARGET_SOURCE", "TARGET_BASE_DATE"],
            ["086450", "KOSDAQ", "동국제약", "1476", "8,044", "8120", "7500", "ACTIVE", "2026-09-29", "TRUE", "9500", "10500", "USER_SPECIFIED", "2026-09-29"],
            ["010140", "KRX", "삼성중공업", "406", "20,686", "19330", "20950", "ACTIVE", "2026-09-29", "TRUE", "23306", "", "StockBot V4", "2026-09-23"],
            ["108490", "KOSDAQ", "로보티즈", "88", "313,357", "285000", "281000", "ACTIVE", "2026-09-29", "TRUE", "366000", "388000", "Spark/V8", "2026-09-23"],
        ]
        mock_write.return_value = 48

        live_positions = [
            {"stock_code": "010140", "stock_name": "삼성중공업", "quantity": 1013, "avg_buy_price": 20119.0, "current_price": 19850},
            {"stock_code": "108490", "stock_name": "로보티즈", "quantity": 88, "avg_buy_price": 313357.0, "current_price": 286500},
        ]

        result = self.reconciler.reconcile(live_positions, sync_to_sheet=True)

        self.assertEqual(result["sync_status"], "SUCCESS")
        self.assertEqual(result["holdings_count"], 2)

        # Check Samsung Heavy
        samsung_heavy = next(p for p in result["positions"] if p["ticker"] == "010140")
        self.assertEqual(samsung_heavy["qty"], 1013)
        self.assertEqual(samsung_heavy["avg_price"], 20119.0)
        self.assertEqual(samsung_heavy["stop_price"], "20950")  # INVARIANT!
        self.assertEqual(samsung_heavy["target1"], "23306")      # INVARIANT!
        self.assertEqual(samsung_heavy["strategy_status"], "REVIEW_REQUIRED")
        self.assertEqual(samsung_heavy["stop_status"], "BREACHED_OR_STALE")

        # Check Robotis
        robotis = next(p for p in result["positions"] if p["ticker"] == "108490")
        self.assertEqual(robotis["strategy_status"], "NORMAL")
        self.assertEqual(robotis["stop_status"], "ACTIVE")

        # Check written rows to sheet
        written_rows = mock_write.call_args[0][0]
        self.assertEqual(len(written_rows), 3)

        # 086450 is CLOSED (Rule 8)
        row_dk = written_rows[0]
        self.assertEqual(row_dk[0], "'086450")  # leading zero preserved with '
        self.assertEqual(row_dk[3], 0)
        self.assertEqual(row_dk[6], "7500")      # strategy preserved
        self.assertEqual(row_dk[7], "CLOSED")
        self.assertEqual(row_dk[9], "FALSE")
        self.assertEqual(row_dk[14], "CLOSED")

        # 010140 is REVIEW_REQUIRED
        row_sh = written_rows[1]
        self.assertEqual(row_sh[0], "'010140")
        self.assertEqual(row_sh[3], 1013)
        self.assertEqual(row_sh[4], 20119)
        self.assertEqual(row_sh[6], "20950")     # strategy preserved
        self.assertEqual(row_sh[10], "23306")    # strategy preserved
        self.assertEqual(row_sh[14], "REVIEW_REQUIRED")
        self.assertIn("손절선", row_sh[15])

    def test_snapshot_persistence(self):
        """13. Snapshots must be persisted to disk."""
        live_positions = [
            {"stock_code": "010140", "stock_name": "삼성중공업", "quantity": 1013, "avg_buy_price": 20119.0, "current_price": 19850}
        ]
        result = self.reconciler.reconcile(live_positions, sync_to_sheet=False)

        live_file = self.state_dir / "data" / "PORTFOLIO_LIVE.json"
        history_file = self.state_dir / "data" / "PORTFOLIO_LIVE_HISTORY.json"

        self.assertTrue(live_file.exists())
        self.assertTrue(history_file.exists())

        with open(live_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data["sync_status"], "SUCCESS")
            p = data["positions"][0]
            self.assertEqual(p["ticker"], "010140")
            self.assertEqual(p["strategy_status"], "UNCONFIGURED")

    # -------------------------------------------------------------------------
    # ATR & Trailing Distance Metric Tests
    # -------------------------------------------------------------------------
    def test_compute_atr_metrics_normal(self):
        """Standard Wilder ATR14 with +0.6 buy tracking and -0.4 high trailing."""
        metrics = compute_atr_metrics(880.6, base_date="2026-09-30", is_suspended=False)
        self.assertEqual(metrics["atr_round"], 881)
        self.assertEqual(metrics["buy_trailing_dist"], 528)
        self.assertEqual(metrics["sell_trailing_dist"], 352)
        self.assertEqual(metrics["atr_text"], "ATR14 881원")
        self.assertEqual(metrics["atr_detail_text"], "0.6ATR 528원 · 0.4ATR 352원")

    def test_compute_atr_metrics_robotis(self):
        """Robotis high ATR14 precision."""
        metrics = compute_atr_metrics(20324.9, base_date="2026-09-30", is_suspended=False)
        self.assertEqual(metrics["atr_round"], 20325)
        self.assertEqual(metrics["buy_trailing_dist"], 12195)
        self.assertEqual(metrics["sell_trailing_dist"], 8130)
        self.assertEqual(metrics["atr_text"], "ATR14 20,325원")
        self.assertEqual(metrics["atr_detail_text"], "0.6ATR 12,195원 · 0.4ATR 8,130원")

    def test_compute_atr_metrics_suspended_with_date(self):
        """Suspended stock with last valid ATR and base date."""
        metrics = compute_atr_metrics(69.1, base_date="2026-09-29", is_suspended=True)
        self.assertEqual(metrics["atr_round"], 69)
        self.assertIsNone(metrics["buy_trailing_dist"])
        self.assertIsNone(metrics["sell_trailing_dist"])
        self.assertEqual(metrics["atr_text"], "ATR14 69원 · 기준일 09/29")
        self.assertEqual(metrics["atr_detail_text"], "-")

    def test_compute_atr_metrics_suspended_without_atr(self):
        """Suspended stock without valid ATR."""
        metrics = compute_atr_metrics(0.0, base_date="2026-09-30", is_suspended=True)
        self.assertIsNone(metrics["atr_round"])
        self.assertEqual(metrics["atr_text"], "ATR - (거래정지)")
        self.assertEqual(metrics["atr_detail_text"], "-")

    def test_compute_atr_metrics_missing(self):
        """Missing or None ATR."""
        metrics = compute_atr_metrics(None, base_date="2026-09-30", is_suspended=False)
        self.assertIsNone(metrics["atr_round"])
        self.assertEqual(metrics["atr_text"], "ATR -")
        self.assertEqual(metrics["atr_detail_text"], "-")

    @patch.object(PortfolioReconciler, "read_remote_config")
    @patch.object(PortfolioReconciler, "write_remote_config")
    def test_reconcile_with_atr_columns_sync(self, mock_write, mock_read):
        """Verify that columns Q, R, S, T are populated in written rows and final positions."""
        mock_read.return_value = [
            ["TICKER", "EXCHANGE", "NAME", "QTY", "AVG_PRICE", "REF_CLOSE", "STOP_PRICE", "STATUS", "BASE_DATE", "ENABLED", "TARGET1", "TARGET2", "TARGET_SOURCE", "TARGET_BASE_DATE", "STRATEGY_STATUS", "STRATEGY_WARNING", "ATR14", "BUY_TRAILING_DIST", "SELL_TRAILING_DIST", "ATR_BASE_DATE"],
            ["010140", "KRX", "삼성중공업", "1013", "20,119", "19330", "20950", "ACTIVE", "2026-09-30", "TRUE", "23306", "", "StockBot V4", "2026-09-23", "REVIEW_REQUIRED", "전략 재검토"],
        ]
        mock_write.return_value = 20

        live_positions = [
            {"stock_code": "010140", "stock_name": "삼성중공업", "quantity": 1013, "avg_buy_price": 20119.0, "current_price": 19850}
        ]
        atr_map = {
            "010140": {"atr_14": 880.6, "base_date": "2026-09-30"}
        }

        result = self.reconciler.reconcile(live_positions, atr_map=atr_map, sync_to_sheet=True)
        self.assertEqual(result["sync_status"], "SUCCESS")

        p = result["positions"][0]
        self.assertEqual(p["atr_round"], 881)
        self.assertEqual(p["buy_trailing_dist"], 528)
        self.assertEqual(p["sell_trailing_dist"], 352)
        self.assertEqual(p["atr_text"], "ATR14 881원")
        self.assertEqual(p["atr_detail_text"], "0.6ATR 528원 · 0.4ATR 352원")

        written_rows = mock_write.call_args[0][0]
        self.assertEqual(len(written_rows), 1)
        row = written_rows[0]
        self.assertEqual(len(row), 20)
        self.assertEqual(row[16], 881)   # ATR14
        self.assertEqual(row[17], 528)   # BUY_TRAILING_DIST
        self.assertEqual(row[18], 352)   # SELL_TRAILING_DIST
        self.assertEqual(row[19], "2026-09-30")  # ATR_BASE_DATE


if __name__ == "__main__":
    unittest.main()

