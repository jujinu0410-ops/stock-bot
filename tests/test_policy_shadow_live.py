import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.policy.policy_shadow_store import PolicyShadowService, PolicyShadowStore
from src.runtime.runtime_scheduler import RuntimeScheduler


class TestPolicyShadowLive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PolicyShadowStore(Path(self.tmp.name) / "policy.db")
        self.service = PolicyShadowService(self.store)
        self.seq = 0

    def tearDown(self): self.tmp.cleanup()

    def raw(self, code="000001", day="2026-08-20", price=100.0, qty=10, avg=100.0, f=70, t=50, loss=0, atr=10, bear=False, breakdown=False, valid=1, suspension="ACTIVE", concentration="CLEAR", daily="NEUTRAL", risk_target_qty=25, is_etf=False):
        self.seq += 1
        return {"asof_timestamp": f"{day} 10:{self.seq:02d}:00", "source_identifier": f"S{self.seq}", "stock_code": code, "market_price": price, "quantity": qty, "weighted_avg_price": avg, "loss_pct": loss, "atr14": atr, "f_score": f, "t_score": t, "daily_state": daily, "is_45m_bearish_2plus": int(bear), "is_45m_breakdown": int(breakdown), "is_45m_bearish_gate": int(bear or breakdown), "completed_45m_timestamp": f"{day} 09:45:00", "concentration_state": concentration, "data_validity": valid, "suspension_state": suspension, "v4_effective_stop": 90.0, "risk_target_qty": risk_target_qty, "is_etf": is_etf}

    def obs(self, raw, boot=()): return self.service.observe(raw, list(boot))
    def legacy(self, **kw): return self.obs(self.raw(**kw), (kw.get("code", "000001"),))
    def new_three_days(self):
        self.obs(self.raw(day="2026-08-20", t=50), ())
        self.obs(self.raw(day="2026-08-21", t=52), ())
        return self.raw(day="2026-08-24", t=55, price=106)

    def test_01_test_mode_path_is_temp(self):
        with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "1"}): self.assertIn(str(os.getpid()), str(PolicyShadowStore().db_path))
    def test_02_schema_has_two_core_tables(self):
        import sqlite3
        c = sqlite3.connect(self.store.db_path)
        try: self.assertTrue({"policy_shadow_cycles", "policy_shadow_snapshots"}.issubset({r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}))
        finally: c.close()
    def test_03_snapshot_append(self): self.legacy(); self.assertEqual(self.store.counts(), (1, 1))
    def test_04_duplicate_source_is_idempotent(self):
        r=self.raw(); self.obs(r,("000001",)); self.service.observe(r,["000001"]); self.assertEqual(self.store.counts()[1],1)
    def test_05_cycle_is_updated_only_in_policy_db(self): self.legacy(price=80, loss=-20); self.assertEqual(self.store.latest_cycle("000001")["defense_state"], "OFF")
    def test_06_bootstrap_is_legacy(self): self.assertEqual(self.legacy()["position_kind"], "LEGACY_POSITION")
    def test_07_legacy_has_no_stage(self): self.legacy(); self.assertIsNone(self.store.latest_cycle("000001")["stage"])
    def test_08_bootstrap_preexisting_breach(self): self.legacy(price=70); self.assertEqual(self.store.latest_cycle("000001")["hard_stop_already_breached_at_shadow_start"],1)
    def test_09_post_bootstrap_hard_stop_is_new_candidate(self):
        self.legacy(price=100); r=self.obs(self.raw(price=70), ("000001",)); self.assertEqual(r["shadow_action"],"HARD_STOP_EXIT")
    def test_10_zero_to_held_is_new_cycle(self): self.assertEqual(self.obs(self.raw(), ())["position_kind"], "NEW_433_CYCLE")
    def test_11_new_cycle_f0_t0_fixed(self):
        self.obs(self.raw(f=70,t=50),()); self.obs(self.raw(f=99,t=99),()); c=self.store.latest_cycle("000001"); self.assertEqual((c["f0"],c["t0"]),(70,50))
    def test_12_stage2_ready(self): self.assertEqual(self.obs(self.new_three_days())["stage2_gate"],"STAGE2_READY")
    def test_13_ready_does_not_raise_stage(self): self.new_three_days(); self.assertEqual(self.store.latest_cycle("000001")["stage"],1)
    def test_14_qty_increase_promotes_stage2(self):
        self.new_three_days(); self.obs(self.raw(day="2026-08-24",t=55,price=106,qty=11)); self.assertEqual(self.store.latest_cycle("000001")["stage"],2)
    def test_15_stage2_stores_t2(self):
        self.new_three_days(); self.obs(self.raw(day="2026-08-24",t=55,price=106,qty=11)); self.assertEqual(self.store.latest_cycle("000001")["t2"],55)
    def test_16_out_of_policy_add_not_promoted(self):
        self.obs(self.raw(),()); self.obs(self.raw(qty=11,t=51)); self.assertEqual(self.store.latest_cycle("000001")["last_reason"],"OUT_OF_POLICY_ADD_OBSERVED")
    def test_17_stage3_same_day_blocked(self):
        self.new_three_days(); self.obs(self.raw(day="2026-08-24",t=55,price=106,qty=11)); self.assertEqual(self.obs(self.raw(day="2026-08-24",t=60,price=106,qty=12))["stage3_gate"],"NOT_READY")
    def test_18_stage3_below_average_blocked(self):
        self.new_three_days(); self.obs(self.raw(day="2026-08-24",t=55,price=106,qty=11)); self.assertEqual(self.obs(self.raw(day="2026-08-25",t=60,qty=11,price=90))["stage3_gate"],"NOT_READY")
    def test_19_mid_loss_t_minus_8_starts_defense(self):
        self.obs(self.raw(day="2026-08-20",loss=-20,t=50,bear=True),("000001",)); self.obs(self.raw(day="2026-08-21",loss=-20,t=48,bear=True),("000001",)); self.obs(self.raw(day="2026-08-24",loss=-20,t=42,bear=True),("000001",)); self.assertEqual(self.store.latest_cycle("000001")["defense_state"],"WAIT_REBOUND")
    def test_20_deep_loss_t_minus_5_starts_defense(self):
        self.obs(self.raw(day="2026-08-20",loss=-30,t=50,bear=True),("000001",)); self.obs(self.raw(day="2026-08-21",loss=-30,t=48,bear=True),("000001",)); self.obs(self.raw(day="2026-08-24",loss=-30,t=45,bear=True),("000001",)); self.assertEqual(self.store.latest_cycle("000001")["defense_state"],"WAIT_REBOUND")
    def test_21_no_bearish_no_defense(self):
        self.obs(self.raw(day="2026-08-20",loss=-20,t=50),("000001",)); self.obs(self.raw(day="2026-08-21",loss=-20,t=45),("000001",)); self.obs(self.raw(day="2026-08-24",loss=-20,t=40),("000001",)); self.assertEqual(self.store.latest_cycle("000001")["defense_state"],"OFF")
    def test_22_missing_history_no_defense(self): self.legacy(loss=-20,t=40,bear=True); self.assertEqual(self.store.latest_cycle("000001")["defense_state"],"OFF")
    def test_23_loss_defense_locks_adds(self):
        self.test_19_mid_loss_t_minus_8_starts_defense(); self.assertEqual(self.store.latest_cycle("000001")["add_locked"],1)
    def test_24_defense_atr_is_fixed(self):
        self.test_19_mid_loss_t_minus_8_starts_defense(); self.obs(self.raw(day="2026-08-25",loss=-20,t=42,bear=True,atr=99),("000001",)); self.assertEqual(self.store.latest_cycle("000001")["defense_atr"],10)
    def test_25_low_updates_while_waiting(self): self.test_19_mid_loss_t_minus_8_starts_defense(); self.obs(self.raw(day="2026-08-25",price=80,loss=-20,t=42,bear=True),("000001",)); self.assertEqual(self.store.latest_cycle("000001")["defense_low"],80)
    def test_26_below_rebound_stays_waiting(self): self.test_25_low_updates_while_waiting(); self.obs(self.raw(day="2026-08-25",price=85,loss=-20,t=42,bear=True),("000001",)); self.assertEqual(self.store.latest_cycle("000001")["defense_state"],"WAIT_REBOUND")
    def test_27_rebound_activates_trail(self): self.test_25_low_updates_while_waiting(); self.obs(self.raw(day="2026-08-25",price=86,loss=-20,t=42,bear=True),("000001",)); self.assertEqual(self.store.latest_cycle("000001")["defense_state"],"TRAIL_ACTIVE")
    def test_28_pre_rebound_high_not_used(self): self.test_19_mid_loss_t_minus_8_starts_defense(); self.assertIsNone(self.store.latest_cycle("000001")["defense_peak"])
    def test_29_peak_then_trail(self): self.test_27_rebound_activates_trail(); self.obs(self.raw(day="2026-08-25",price=100,loss=-20,t=42,bear=True),("000001",)); self.assertEqual(self.store.latest_cycle("000001")["defense_trail"],96)
    def test_30_trail_never_decreases(self): self.test_29_peak_then_trail(); old=self.store.latest_cycle("000001")["defense_trail"]; self.obs(self.raw(day="2026-08-25",price=90,loss=-20,t=42,bear=True),("000001",)); self.assertGreaterEqual(self.store.latest_cycle("000001")["defense_trail"],old)
    def test_31_hard_stop_point_78(self): self.legacy(avg=100,price=100); self.assertEqual(self.store.latest_cycle("000001")["hard_stop"],78)
    def test_32_averaging_down_does_not_lower_hard_stop(self): self.legacy(avg=100); self.obs(self.raw(avg=80),("000001",)); self.assertEqual(self.store.latest_cycle("000001")["hard_stop"],78)
    def test_33_averaging_up_raises_hard_stop(self): self.legacy(avg=100); self.obs(self.raw(avg=120,price=121),("000001",)); self.assertAlmostEqual(self.store.latest_cycle("000001")["hard_stop"],93.6)
    def test_34_shadow_exit_never_below_v4(self): self.assertGreaterEqual(self.legacy(price=100,avg=100)["shadow_effective_exit"],90)
    def test_35_data_hold_blocks_stage_ready(self): self.new_three_days(); self.assertEqual(self.obs(self.raw(day="2026-08-25",t=60,valid=0))["stage2_gate"],"NOT_READY")
    def test_36_suspension_blocks_stage_ready(self): self.new_three_days(); self.assertEqual(self.obs(self.raw(day="2026-08-25",t=60,suspension="SUSPENDED"))["stage2_gate"],"NOT_READY")
    def test_37_concentration_blocks_stage_ready(self): self.new_three_days(); self.assertEqual(self.obs(self.raw(day="2026-08-25",t=60,concentration="BLOCKED"))["stage2_gate"],"NOT_READY")
    def test_38_close_locks_cycle(self): self.legacy(); self.obs(self.raw(qty=0),("000001",)); self.assertEqual(self.store.latest_cycle("000001")["cycle_status"],"CLOSED")
    def test_39_order_impact_always_zero(self): self.assertEqual(self.legacy()["actual_order_impact"],0)
    def test_40_runtime_policy_store_failure_is_isolated(self):
        scheduler = RuntimeScheduler.__new__(RuntimeScheduler); scheduler.db = MagicMock()
        with patch("src.policy.policy_shadow_store.PolicyShadowStore", side_effect=RuntimeError("store down")):
            scheduler._capture_policy_shadow({"stock_code":"000001"}, "2026-08-24", "2026-08-24 10:00:00", "2026-08-24 09:45:00", "RUN")
    def test_41_runtime_policy_calculation_failure_is_isolated(self):
        scheduler = RuntimeScheduler.__new__(RuntimeScheduler); scheduler.db = MagicMock()
        with patch("src.policy.policy_shadow_store.PolicyShadowService.observe", side_effect=RuntimeError("calc down")):
            scheduler._capture_policy_shadow({"stock_code":"000001", "market_price":100, "existing_f_score":70, "t_score":50, "atr14":10}, "2026-08-24", "2026-08-24 10:00:00", "2026-08-24 09:45:00", "RUN")

    def test_42_authoritative_f_is_preserved_not_defaulted(self):
        self.obs(self.raw(f=56), ())
        self.assertEqual(self.store.latest_cycle("000001")["f0"], 56)

    def test_43_missing_f_is_data_gap_not_70(self):
        self.obs(self.raw(f=None), ())
        cycle = self.store.latest_cycle("000001")
        self.assertIsNone(cycle["f0"])
        self.assertEqual(cycle["f_baseline_status"], "DATA_GAP")
        self.assertEqual(self.obs(self.raw(day="2026-08-21", f=None, t=99))["stage2_gate"], "BASELINE_DATA_GAP")

    def test_44_etf_f_is_not_applicable(self):
        self.obs(self.raw(f=91, is_etf=True), ())
        cycle = self.store.latest_cycle("000001")
        self.assertIsNone(cycle["f0"])
        self.assertEqual(cycle["f_baseline_status"], "NOT_APPLICABLE")

    def test_45_cycle_target_quantities_are_frozen(self):
        self.obs(self.raw(qty=10, risk_target_qty=25), ())
        self.obs(self.raw(day="2026-08-21", qty=10, risk_target_qty=100), ())
        cycle = self.store.latest_cycle("000001")
        self.assertEqual((cycle["cycle_target_qty"], cycle["stage1_target_qty"], cycle["stage2_target_qty"], cycle["stage3_target_qty"]), (25, 10, 17, 25))

    def test_46_stage1_size_statuses(self):
        self.obs(self.raw(qty=10, risk_target_qty=25), ())
        self.assertEqual(self.store.latest_cycle("000001")["stage_size_status"], "STAGE1_SIZE_MATCH")
        self.obs(self.raw(day="2026-08-21", qty=9), ())
        self.assertEqual(self.store.latest_cycle("000001")["stage_size_status"], "STAGE1_SIZE_UNDERALLOCATED")
        self.obs(self.raw(day="2026-08-22", qty=11), ())
        self.assertEqual(self.store.latest_cycle("000001")["stage_size_status"], "STAGE1_SIZE_OVERALLOCATED")

    def test_47_target_qty_gap_blocks_stage2(self):
        self.obs(self.raw(risk_target_qty=None), ())
        self.assertEqual(self.obs(self.raw(day="2026-08-21", t=99, risk_target_qty=None))["stage2_gate"], "TARGET_QTY_DATA_GAP")

    def test_48_explicit_baseline_repair_requires_source(self):
        self.obs(self.raw(f=None, t=None), ())
        self.assertIsNone(self.store.repair_new_cycle_baseline("000001", f0=75, t0=43, source="", repaired_at="2026-08-20 11:00:00"))
        repaired = self.store.repair_new_cycle_baseline("000001", f0=75, t0=43, source="trading_signals:20260820", repaired_at="2026-08-20 11:00:00")
        self.assertEqual((repaired["f0"], repaired["t0"], repaired["f_baseline_status"]), (75, 43, "BASELINE_REPAIRED_FROM_ORIGINAL_FIRST_OBSERVATION"))

    def test_49_runtime_bridge_passes_authoritative_f_or_none(self):
        scheduler = RuntimeScheduler.__new__(RuntimeScheduler)
        position = {"quantity": 10, "avg_buy_price": 100, "data_validity_flag": 1, "data_hold_reason": "", "effective_exit_line": 90, "trade_mode": "NORMAL"}
        for value in (56, 91, None):
            scheduler.db = MagicMock()
            scheduler.db.execute_query.side_effect = [[position], [{"stock_code": "000001"}]]
            with patch("src.policy.policy_shadow_store.PolicyShadowStore"), patch("src.policy.policy_shadow_store.PolicyShadowService.observe") as observe:
                scheduler._capture_policy_shadow({"stock_code":"000001", "market_price":100, "existing_f_score":value, "t_score":50, "atr14":10}, "2026-08-24", "2026-08-24 10:00:00", "2026-08-24 09:45:00", "RUN")
                self.assertEqual(observe.call_args.args[0]["f_score"], value)
        scheduler.db = MagicMock()
        scheduler.db.execute_query.side_effect = [[position], [{"stock_code": "000001"}]]
        with patch("src.policy.policy_shadow_store.PolicyShadowStore"), patch("src.policy.policy_shadow_store.PolicyShadowService.observe") as observe:
            scheduler._capture_policy_shadow({"stock_code":"000001", "market_price":100, "existing_f_score":50, "is_etf":True, "t_score":50, "atr14":10}, "2026-08-24", "2026-08-24 10:00:00", "2026-08-24 09:45:00", "RUN")
            self.assertIsNone(observe.call_args.args[0]["f_score"])

    def test_50_regular_stock_f_score_preserved_in_snapshot_and_cycle(self):
        """일반주 F 점수는 정상 유지되고 f_baseline_status는 OK여야 함"""
        res = self.obs(self.raw(code="004960", f=75.0, is_etf=False), ())
        self.assertEqual(res["f_score"], 75.0)
        self.assertEqual(res["f_baseline_status"], "OK")
        cycle = self.store.latest_cycle("004960")
        self.assertEqual(cycle["f0"], 75.0)
        self.assertEqual(cycle["f_baseline_status"], "OK")

    def test_51_etf_stock_f_score_is_none_and_not_applicable_in_snapshot(self):
        """ETF (161510, 490590)는 F 점수가 NULL이고 상태는 NOT_APPLICABLE이어야 함"""
        for etf_code in ("161510", "490590"):
            res = self.obs(self.raw(code=etf_code, f=None, is_etf=True), (etf_code,))
            self.assertIsNone(res["f_score"])
            self.assertEqual(res["f_baseline_status"], "NOT_APPLICABLE")
            cycle = self.store.latest_cycle(etf_code)
            self.assertIsNone(cycle["f0"])
            self.assertEqual(cycle["f_baseline_status"], "NOT_APPLICABLE")

    def test_52_etf_fallback_35_or_70_prohibited(self):
        """ETF에 임의의 35.0 또는 70.0 점수가 전달되더라도 Policy Shadow에서 F는 NULL/NOT_APPLICABLE 처리"""
        for bad_f in (35.0, 70.0, 50.0):
            res = self.obs(self.raw(code="161510", f=bad_f, is_etf=True), ("161510",))
            self.assertIsNone(res["f_score"])
            self.assertEqual(res["f_baseline_status"], "NOT_APPLICABLE")

    def test_53_trading_engine_detects_etf_without_dart_financials(self):
        """TradingEngine이 DART 재무데이터 없는 ETF에 대해 올바르게 is_etf=True로 평가"""
        from src.engine.trading_engine import TradingEngine
        db_mock = MagicMock()
        db_mock.execute_query.side_effect = [
            [{"stock_code": "161510", "stock_name": "PLUS 고배당주"}],  # stock_info
            [{"stk_date": f"202608{i:02d}", "close_price": 25000, "open_price": 25000, "high_price": 25100, "low_price": 24900, "volume": 1000} for i in range(1, 25)],  # daily
            [],  # dart_financials (empty for ETF)
            [],  # portfolio_positions
        ]
        te = TradingEngine(db_mock)
        with patch("src.engine.trading_engine.TechnicalAnalysis") as mock_ta, \
             patch.object(te.intraday_analyzer, "analyze_45m_indicators", return_value={"intraday_quality": "VALID", "is_45m_bearish_2plus": False, "is_45m_breakdown": False}):
            mock_ta_inst = MagicMock()
            mock_ta_inst.evaluate_signals.return_value = {
                "tech_completeness": 100.0, "t_raw": 80.0, "t_score": 80.0,
                "reason": "OK", "kiwoom_stop_tick_price": 24000, "kiwoom_target_tick_price": 28000
            }
            mock_ta.return_value = mock_ta_inst
            res = te.analyze_stock("161510")
            self.assertIsNotNone(res)
            self.assertTrue(res["is_etf"])
            self.assertTrue(mock_ta.call_args.kwargs.get("is_etf", False))

