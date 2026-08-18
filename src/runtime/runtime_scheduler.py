"""
Stock Analysis System V1.0 Shadow Production Runtime Scheduler
Phase 8 Runtime Layer

Features:
- Non-resident invocation via Windows Task Scheduler.
- KRX Calendar & Holiday Gate (fail-closed).
- Blackout window protection for existing 11:20 and 15:35 operational reports.
- Completed 45-minute bar execution only (no intraday backfill / no fake 45m bars).
- Scan Journal Append-Only Deduplication (DAILY_FIRST, SIGNAL_CHANGE, BLOCKER_CHANGE, TECHNICAL_STATE_CHANGE, MANUAL_RUNTIME_VERIFICATION).
- Zero Order API calls & Zero email spam.
- Audit logging to scheduler_runs.
"""

import sys
import os
import json
import argparse
from datetime import datetime, time
from typing import Dict, Any, List, Optional, Tuple

from src.database.db_manager import DatabaseManager
from src.utils.logger import logger
from src.runtime.krx_calendar import KRXCalendar
from src.runtime.scheduler_lock import SchedulerLockManager
from src.analysis.industry_radar_engine import IndustryRadarEngine
from src.analysis.outcome_evaluator import OutcomeEvaluator
from src.analysis.attribution_engine import AttributionEngine
from src.analysis.canonical_registry import CanonicalConsistencyChecker
from src.analysis.fundamental_evidence_scanner import FundamentalEvidenceScanner
from src.analysis.forward_visibility_engine import ForwardVisibilityEngine
from src.engine.trading_engine import TradingEngine
from src.api.market_quote_client import MarketQuoteClient

class RuntimeScheduler:
    """V1.0 Shadow Production Operations Orchestrator"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()
        self.lock_mgr = SchedulerLockManager(self.db)
        self.radar_engine = IndustryRadarEngine(self.db)
        self.outcome_evaluator = OutcomeEvaluator(self.db)
        self.attribution_engine = AttributionEngine(self.db)
        self.consistency_checker = CanonicalConsistencyChecker(self.db)
        self.trading_engine = TradingEngine(self.db)
        self.fund_scanner = FundamentalEvidenceScanner()
        self.fwd_engine = ForwardVisibilityEngine(self.db)
        self.quote_client = MarketQuoteClient()




    def run_task(self, task_type: str, is_manual: bool = False) -> Dict[str, Any]:
        """Entrypoint for scheduled / manual runtime tasks"""
        task_upper = task_type.upper().strip()
        logger.info(f"[RuntimeScheduler] Starting task '{task_upper}' (Manual={is_manual}) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        if task_upper == "PRE_MARKET_HEALTH_CHECK":
            return self._execute_pre_market_health_check(is_manual=is_manual)
        elif task_upper == "INTRADAY_SHADOW_SCAN":
            return self._execute_intraday_shadow_scan(is_manual=is_manual)
        elif task_upper == "OUTCOME_AND_JOURNAL_MAINTENANCE":
            return self._execute_outcome_maintenance(is_manual=is_manual)
        else:
            err_msg = f"Unknown task type: {task_type}"
            logger.error(f"[RuntimeScheduler] {err_msg}")
            return {"status": "FAILED", "error_code": "INVALID_TASK_TYPE", "error_message": err_msg}

    # =========================================================================
    # Task 1: 08:40 PRE_MARKET_HEALTH_CHECK
    # =========================================================================
    def _execute_pre_market_health_check(self, is_manual: bool = False) -> Dict[str, Any]:
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        trading_date = KRXCalendar.get_krx_trading_date_str(now)
        run_id = f"RUN_HC_{now.strftime('%Y%m%d_%H%M%S')}"

        # 1. Trading day check
        is_trading = KRXCalendar.is_krx_trading_day(now)
        if not is_trading and not is_manual:
            logger.info(f"[HealthCheck] Today {trading_date} is not a KRX trading day. Skipping health check.")
            self._record_run(run_id, "08:40", now_str, now_str, trading_date, "PRE_MARKET_HEALTH_CHECK", "SKIPPED_NON_TRADING_DAY")
            return {"status": "SKIPPED_NON_TRADING_DAY", "run_id": run_id}

        # 2. Acquire lock
        if not self.lock_mgr.acquire_lock("SHADOW_HEALTH_CHECK_LOCK", "PRE_MARKET_HEALTH_CHECK", ttl_seconds=300):
            return {"status": "SKIPPED_LOCK_BUSY", "run_id": run_id}

        try:
            self._record_run(run_id, "08:40", now_str, None, trading_date, "PRE_MARKET_HEALTH_CHECK", "STARTED")

            # Check DB health
            table_check = self.db.execute_query("SELECT count(*) FROM sqlite_master WHERE type='table'")
            table_count = table_check[0][0] if table_check else 0

            # Check Industry Production Run
            ind_scores = self.db.get_all_latest_industry_scores(run_type="PRODUCTION")
            has_ind_scores = len(ind_scores) >= 5

            # Check Lock Table
            self.lock_mgr.db.cleanup_stale_scheduler_locks()

            status = "SUCCESS" if (table_count >= 10 and has_ind_scores) else "DEGRADED"
            end_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.db.update_scheduler_run(run_id, {
                "actual_end_time": end_str,
                "status": status,
                "error_code": None if status == "SUCCESS" else "HEALTH_CHECK_DEGRADED",
                "error_message": f"Tables: {table_count}, Industry Scores: {len(ind_scores)}"
            })
            logger.info(f"[HealthCheck] Completed. Status: {status} (Tables: {table_count}, Industry Scores: {len(ind_scores)})")
            return {"status": status, "run_id": run_id, "tables": table_count, "industry_scores": len(ind_scores)}

        except Exception as e:
            logger.error(f"[HealthCheck] Failed with exception: {e}", exc_info=True)
            self.db.update_scheduler_run(run_id, {
                "actual_end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "FAILED",
                "error_code": "EXCEPTION",
                "error_message": str(e)
            })
            return {"status": "FAILED", "error": str(e)}
        finally:
            self.lock_mgr.release_lock("SHADOW_HEALTH_CHECK_LOCK")

    # =========================================================================
    # Task 2: INTRADAY_SHADOW_SCAN (09:50, 10:35, 11:35, 12:05, 12:50, 13:35, 14:20, 15:05)
    # =========================================================================
    def _execute_intraday_shadow_scan(self, is_manual: bool = False) -> Dict[str, Any]:
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        time_str = now.strftime("%H:%M")
        trading_date = KRXCalendar.get_krx_trading_date_str(now)
        run_id = f"RUN_SC_{now.strftime('%Y%m%d_%H%M%S')}"

        # 1. KRX Trading Day Check
        is_trading = KRXCalendar.is_krx_trading_day(now)
        if not is_trading and not is_manual:
            logger.info(f"[IntradayScan] Today {trading_date} is not a KRX trading day. Skipping scan.")
            self._record_run(run_id, time_str, now_str, now_str, trading_date, "INTRADAY_SHADOW_SCAN", "SKIPPED_NON_TRADING_DAY")
            return {"status": "SKIPPED_NON_TRADING_DAY", "run_id": run_id}

        # 2. Blackout / Reserved Window Check (11:10~11:30, 15:25~15:50)
        is_reserved, window_label = self.lock_mgr.check_reserved_window(now)
        if is_reserved and not is_manual:
            logger.warning(f"[IntradayScan] Current time is inside reserved window '{window_label}'. Skipping scan.")
            self._record_run(run_id, time_str, now_str, now_str, trading_date, "INTRADAY_SHADOW_SCAN", "SKIPPED_RESERVED_WINDOW", error_code="RESERVED_WINDOW", error_message=window_label)
            return {"status": "SKIPPED_RESERVED_WINDOW", "window": window_label, "run_id": run_id}

        # 3. Existing Operational Job Running Check (e.g. 11:20 / 15:35 job)
        if self.lock_mgr.is_existing_job_active() and not is_manual:
            logger.warning(f"[IntradayScan] Existing operational portfolio/closing job is actively running. Skipping to prevent collision.")
            self._record_run(run_id, time_str, now_str, now_str, trading_date, "INTRADAY_SHADOW_SCAN", "SKIPPED_EXISTING_JOB_ACTIVE", error_code="EXISTING_JOB_ACTIVE")
            return {"status": "SKIPPED_EXISTING_JOB_ACTIVE", "run_id": run_id}

        # 4. 45-Minute Completed Bar Resolution
        bar_info = KRXCalendar.get_completed_45m_bar(now)
        if bar_info is None and not is_manual:
            logger.warning(f"[IntradayScan] No completed 45-minute bar available yet (Market starts 09:00, 1st bar completes 09:45).")
            self._record_run(run_id, time_str, now_str, now_str, trading_date, "INTRADAY_SHADOW_SCAN", "SKIPPED_NO_NEW_45M_BAR", error_code="BEFORE_FIRST_45M_BAR")
            return {"status": "SKIPPED_NO_NEW_45M_BAR", "run_id": run_id}

        slot_name = bar_info[0] if bar_info else "MANUAL"
        bar_end = bar_info[2] if bar_info else now.strftime("%H:%M")
        completed_bar_ts = f"{trading_date} {bar_end}:00"

        # 5. Check if this exact 45m bar was already scanned
        if not is_manual:
            latest_run = self.db.get_latest_scheduler_run("INTRADAY_SHADOW_SCAN")
            if latest_run and latest_run.get("last_completed_45m_bar") == completed_bar_ts and latest_run.get("status") == "SUCCESS":
                logger.info(f"[IntradayScan] 45m bar '{completed_bar_ts}' already processed in previous run '{latest_run['run_id']}'. Skipping duplicate scan.")
                self._record_run(run_id, time_str, now_str, now_str, trading_date, "INTRADAY_SHADOW_SCAN", "SKIPPED_NO_NEW_45M_BAR", last_completed_45m_bar=completed_bar_ts)
                return {"status": "SKIPPED_NO_NEW_45M_BAR", "last_completed_45m_bar": completed_bar_ts, "run_id": run_id}

        # 6. Acquire Lock
        if not self.lock_mgr.acquire_lock("SHADOW_INTRADAY_SCAN_LOCK", "INTRADAY_SHADOW_SCAN", ttl_seconds=600):
            return {"status": "SKIPPED_LOCK_BUSY", "run_id": run_id}

        try:
            self._record_run(run_id, time_str, now_str, None, trading_date, "INTRADAY_SHADOW_SCAN", "STARTED", last_completed_45m_bar=completed_bar_ts)

            # 7. Collect Active Stock Universe
            universe_stocks = self._get_active_universe()
            logger.info(f"[IntradayScan] Scanning {len(universe_stocks)} universe stocks for slot '{slot_name}' (Bar: {completed_bar_ts})...")

            stocks_scanned = 0
            journals_created = 0
            signal_changes = 0

            for stock in universe_stocks:
                code = stock["stock_code"]
                name = stock.get("stock_name", code)

                # Multi-Layer Scan Evaluation
                scan_res = self._evaluate_stock_shadow(code, name, trading_date, now_str)
                if not scan_res:
                    continue

                stocks_scanned += 1

                # Deduplication & Snapshot Reason Decision
                snapshot_reason = self._determine_snapshot_reason(code, scan_res, trading_date, is_manual)

                if snapshot_reason:
                    # Append Snapshot to scan_journal
                    j_id = f"JRN_{now.strftime('%Y%m%d_%H%M%S')}__{code}"
                    journal_entry = {
                        "journal_id": j_id,
                        "scan_timestamp": f"{now_str} KST",
                        "trading_date": trading_date,
                        "snapshot_reason": snapshot_reason,
                        "stock_code": code,
                        "stock_name": name,
                        "market_price": scan_res["market_price"],
                        "industry_run_id": scan_res.get("industry_run_id"),
                        "industry_score": scan_res.get("industry_score"),
                        "industry_gate": scan_res.get("industry_gate"),
                        "industry_confidence": scan_res.get("industry_confidence"),
                        "exposure_type": scan_res.get("exposure_type"),
                        "mapping_version": scan_res.get("mapping_version", "V1.0"),
                        "fundamental_state": scan_res.get("fundamental_state"),
                        "turnaround_type": scan_res.get("turnaround_type"),
                        "turnaround_label": scan_res.get("turnaround_label"),
                        "forward_opportunity": scan_res.get("forward_opportunity"),
                        "forward_confidence": scan_res.get("forward_confidence"),
                        "forward_risk": scan_res.get("forward_risk"),
                        "forward_risk_override_tag": scan_res.get("forward_risk_override_tag", "NONE"),
                        "book_to_bill_summary": scan_res.get("book_to_bill_summary"),
                        "t_score": scan_res.get("t_score"),
                        "technical_state": scan_res.get("technical_state"),
                        "technical_action": scan_res.get("technical_action"),
                        "intraday_data_quality": scan_res.get("intraday_data_quality", "VALID"),
                        "atr14": scan_res.get("atr14"),
                        "natr": scan_res.get("natr"),
                        "candidate_ref_price": scan_res.get("candidate_ref_price"),
                        "candidate_stop_price": scan_res.get("candidate_stop_price"),
                        "candidate_target_price": scan_res.get("candidate_target_price"),
                        "atr_mode": scan_res.get("atr_mode", "NORMAL"),
                        "shadow_integrated_state": scan_res.get("shadow_integrated_state"),
                        "primary_blocker": scan_res.get("primary_blocker", "NONE"),
                        "all_blockers": scan_res.get("all_blockers", []),
                        "existing_f_score": scan_res.get("existing_f_score"),
                        "existing_final_score": scan_res.get("existing_final_score"),
                        "buy_approval": scan_res.get("buy_approval"),
                        "p0_status": scan_res.get("p0_status", "NONE"),
                        "position_cycle_id": scan_res.get("position_cycle_id", "NONE"),
                        "financial_data_asof": scan_res.get("financial_data_asof"),
                        "forward_data_asof": scan_res.get("forward_data_asof"),
                        "intraday_last_timestamp": scan_res.get("intraday_last_timestamp", now_str),
                        "scoring_versions": {"industry": "V1.0", "scoring": "V1.0"}
                    }
                    self.db.insert_scan_journal(journal_entry)
                    journals_created += 1
                    if snapshot_reason == "SIGNAL_CHANGE":
                        signal_changes += 1

            # Update scheduler_run completion
            end_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.db.update_scheduler_run(run_id, {
                "actual_end_time": end_str,
                "status": "SUCCESS",
                "stocks_scanned": stocks_scanned,
                "journals_created": journals_created,
                "signal_changes": signal_changes,
                "last_completed_45m_bar": completed_bar_ts
            })

            logger.info(f"[IntradayScan] Run '{run_id}' finished successfully. Scanned: {stocks_scanned}, Journals Appended: {journals_created}, Signal Changes: {signal_changes}")
            return {
                "status": "SUCCESS",
                "run_id": run_id,
                "stocks_scanned": stocks_scanned,
                "journals_created": journals_created,
                "signal_changes": signal_changes,
                "last_completed_45m_bar": completed_bar_ts
            }

        except Exception as e:
            logger.error(f"[IntradayScan] Exception occurred: {e}", exc_info=True)
            self.db.update_scheduler_run(run_id, {
                "actual_end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "FAILED",
                "error_code": "EXCEPTION",
                "error_message": str(e)
            })
            return {"status": "FAILED", "error": str(e), "run_id": run_id}
        finally:
            self.lock_mgr.release_lock("SHADOW_INTRADAY_SCAN_LOCK")

    # =========================================================================
    # Task 3: 16:10 OUTCOME_AND_JOURNAL_MAINTENANCE
    # =========================================================================
    def _execute_outcome_maintenance(self, is_manual: bool = False) -> Dict[str, Any]:
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        trading_date = KRXCalendar.get_krx_trading_date_str(now)
        run_id = f"RUN_MT_{now.strftime('%Y%m%d_%H%M%S')}"

        is_trading = KRXCalendar.is_krx_trading_day(now)
        if not is_trading and not is_manual:
            logger.info(f"[OutcomeMaintenance] Today {trading_date} is not a KRX trading day. Skipping maintenance.")
            self._record_run(run_id, "16:10", now_str, now_str, trading_date, "OUTCOME_AND_JOURNAL_MAINTENANCE", "SKIPPED_NON_TRADING_DAY")
            return {"status": "SKIPPED_NON_TRADING_DAY", "run_id": run_id}

        if not self.lock_mgr.acquire_lock("SHADOW_MAINTENANCE_LOCK", "OUTCOME_AND_JOURNAL_MAINTENANCE", ttl_seconds=600):
            return {"status": "SKIPPED_LOCK_BUSY", "run_id": run_id}

        try:
            self._record_run(run_id, "16:10", now_str, None, trading_date, "OUTCOME_AND_JOURNAL_MAINTENANCE", "STARTED")

            # 1. Fetch pending or un-evaluated scan journals
            journals = self.db.get_all_scan_journals()
            evaluated_count = 0

            for j in journals:
                j_id = j["journal_id"]
                outcome = self.outcome_evaluator.evaluate_journal_outcome(j_id)
                if outcome:
                    evaluated_count += 1

            # 2. Cleanup stale locks
            self.lock_mgr.db.cleanup_stale_scheduler_locks()

            end_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.db.update_scheduler_run(run_id, {
                "actual_end_time": end_str,
                "status": "SUCCESS",
                "stocks_scanned": len(journals),
                "journals_created": evaluated_count,
                "error_message": f"Matured/Evaluated outcomes: {evaluated_count}"
            })
            logger.info(f"[OutcomeMaintenance] Completed. Evaluated {evaluated_count} / {len(journals)} journals.")
            return {"status": "SUCCESS", "run_id": run_id, "total_journals": len(journals), "outcomes_evaluated": evaluated_count}

        except Exception as e:
            logger.error(f"[OutcomeMaintenance] Failed with exception: {e}", exc_info=True)
            self.db.update_scheduler_run(run_id, {
                "actual_end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "FAILED",
                "error_code": "EXCEPTION",
                "error_message": str(e)
            })
            return {"status": "FAILED", "error": str(e)}
        finally:
            self.lock_mgr.release_lock("SHADOW_MAINTENANCE_LOCK")

    # =========================================================================
    # Helpers
    # =========================================================================
    def _record_run(
        self,
        run_id: str,
        scheduled_time: str,
        start_time: str,
        end_time: Optional[str],
        trading_date: str,
        task_type: str,
        status: str,
        stocks_scanned: int = 0,
        journals_created: int = 0,
        signal_changes: int = 0,
        last_completed_45m_bar: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> None:
        self.db.insert_scheduler_run({
            "run_id": run_id,
            "scheduled_time": scheduled_time,
            "actual_start_time": start_time,
            "actual_end_time": end_time,
            "trading_date": trading_date,
            "task_type": task_type,
            "status": status,
            "stocks_scanned": stocks_scanned,
            "journals_created": journals_created,
            "signal_changes": signal_changes,
            "last_completed_45m_bar": last_completed_45m_bar,
            "error_code": error_code,
            "error_message": error_message
        })

    def _get_active_universe(self) -> List[Dict[str, Any]]:
        """Get portfolio holdings, watchlist, and core industry stocks"""
        stocks = {}
        # 1. stock_info
        rows = self.db.execute_query("SELECT stock_code, stock_name FROM stock_info")
        for r in (rows or []):
            d = dict(r)
            code = str(d["stock_code"]).zfill(6)
            stocks[code] = {"stock_code": code, "stock_name": d.get("stock_name", code)}

        # 2. industry_company_map
        map_rows = self.db.execute_query("SELECT stock_code, stock_name FROM industry_company_map WHERE is_eligible_candidate = 1")
        for r in (map_rows or []):
            d = dict(r)
            code = str(d["stock_code"]).zfill(6)
            if code not in stocks:
                stocks[code] = {"stock_code": code, "stock_name": d.get("stock_name", code)}

        return list(stocks.values())


    def _evaluate_stock_shadow(
        self,
        stock_code: str,
        stock_name: str,
        trading_date: str,
        now_str: str
    ) -> Optional[Dict[str, Any]]:
        """Multi-layer evaluation wrapper using existing frozen engines with verified live market price"""
        try:
            # 1. Fetch Verified Realtime Market Quote with strict provenance
            quote_res = self.quote_client.fetch_live_quote(stock_code)
            if not quote_res.get("success") or quote_res.get("price") is None:
                logger.warning(f"[ShadowScan] {stock_code} Live quote unavailable ({quote_res.get('status')}). Skipping snapshot.")
                return None

            market_price = float(quote_res["price"])
            price_source = quote_res.get("quote_source", "UNKNOWN")
            quote_timestamp = quote_res.get("quote_timestamp", now_str)
            quote_age_sec = quote_res.get("quote_age_seconds", 0)

            # ATR14 calculation from daily candles
            daily_rows = self.db.get_recent_daily_candles(stock_code, limit=20)
            if daily_rows and len(daily_rows) >= 2:
                highs = [float(b.get("high_price", market_price)) for b in daily_rows[:15]]
                lows = [float(b.get("low_price", market_price)) for b in daily_rows[:15]]
                closes = [float(b.get("close_price", market_price)) for b in daily_rows[:15]]
                tr_list = []
                for i in range(len(highs) - 1):
                    tr = max(highs[i] - lows[i], abs(highs[i] - closes[i+1]), abs(lows[i] - closes[i+1]))
                    tr_list.append(tr)
                atr14 = sum(tr_list) / len(tr_list) if tr_list else (market_price * 0.03)
            else:
                atr14 = market_price * 0.03

            natr = round((atr14 / market_price) * 100.0, 2)


            # 2. Industry Radar Profile
            ind_prof = self.radar_engine.get_industry_profile_for_stock(stock_code, stock_name, as_of_date=trading_date)

            # 3. Fundamental Evidence & State
            q_rows = self.db.get_recent_quarterly_financials(stock_code, limit=8)
            quarterly_records = [dict(r) for r in q_rows] if q_rows else []
            fund_res = self.fund_scanner.evaluate_evidence(quarterly_records, stock_code)
            fund_state = fund_res.get("fundamental_state", "STABLE")
            f_score = fund_res.get("f_score", 70.0)
            turnaround_type = fund_res.get("turnaround_type", "NONE")
            turnaround_label = fund_res.get("turnaround_label", "안정 성장")

            # 4. Forward Visibility & Risk State
            fwd_res = self.fwd_engine.evaluate_forward_visibility(stock_code, stock_name)
            fwd_opp = fwd_res.get("opportunity_state", "MODERATE")
            fwd_conf = fwd_res.get("opportunity_confidence", "MEDIUM")
            fwd_risk = fwd_res.get("risk_state", "NONE")
            fwd_risk_tag = fwd_res.get("risk_override_tag", "NONE")
            b2b_summary = fwd_res.get("book_to_bill_summary", "N/A")



            # 5. Technical Gate & Existing Score from TradingEngine
            eng_res = self.trading_engine.analyze_stock(stock_code)
            if eng_res:
                t_score = eng_res.get("t_score", 65.0)
                tech_state = eng_res.get("technical_state", "NEUTRAL")
                tech_action = eng_res.get("technical_action", "BUY_WAIT")
                intraday_dq = eng_res.get("intraday_data_quality", "VALID")
                existing_f = eng_res.get("f_score", f_score)
                existing_fin = eng_res.get("final_score", round(0.4 * f_score + 0.6 * t_score, 1))
            else:
                t_score = 65.0
                tech_state = "NEUTRAL"
                tech_action = "BUY_WAIT"
                intraday_dq = "VALID"
                existing_f = f_score
                existing_fin = round(0.4 * f_score + 0.6 * t_score, 1)

            # 6. Synthesize Shadow State & Blockers
            shadow_synth = self.radar_engine.synthesize_shadow_state(
                industry_gate=ind_prof["industry_gate"],
                industry_score=ind_prof["total_score"],
                exposure_type=ind_prof["exposure_type"],
                fundamental_state=fund_state,
                f_score=existing_f,
                forward_opp_state=fwd_opp,
                forward_risk_state=fwd_risk,
                technical_action=tech_action,
                atr_mode="NORMAL"
            )


            # 7. Candidate Protection & Targets
            candidate_stop = market_price - (1.5 * atr14)
            candidate_target = market_price + (3.0 * atr14)

            # 8. Buy Approval
            if shadow_synth["primary_blocker"] == "NONE" and tech_action in ["BUY_ALLOWED", "BUY_ALLOWED_CONDITIONAL"]:
                buy_approval = "🔵 ON (트레일링/눌림목 분할매수 승인)"
            else:
                buy_approval = "🔴 OFF (진입 불가/조건 미충족)"

            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "market_price": market_price,
                "industry_run_id": ind_prof.get("run_id"),
                "industry_score": ind_prof["total_score"],
                "industry_gate": ind_prof["industry_gate"],
                "industry_confidence": ind_prof["industry_confidence"],
                "exposure_type": ind_prof["exposure_type"],
                "mapping_version": ind_prof.get("mapping_version", "V1.0"),
                "fundamental_state": fund_state,
                "turnaround_type": turnaround_type,
                "turnaround_label": turnaround_label,
                "forward_opportunity": fwd_opp,
                "forward_confidence": fwd_conf,
                "forward_risk": fwd_risk,
                "forward_risk_override_tag": fwd_risk_tag,
                "book_to_bill_summary": b2b_summary,
                "t_score": t_score,
                "technical_state": tech_state,
                "technical_action": tech_action,
                "intraday_data_quality": "VALID",
                "atr14": atr14,
                "natr": natr,
                "candidate_ref_price": market_price,
                "candidate_stop_price": candidate_stop,
                "candidate_target_price": candidate_target,
                "atr_mode": "NORMAL",
                "shadow_integrated_state": shadow_synth["shadow_integrated_state"],
                "primary_blocker": shadow_synth["primary_blocker"],
                "all_blockers": shadow_synth["all_blockers"],
                "existing_f_score": f_score,
                "existing_final_score": round(0.4 * f_score + 0.6 * t_score, 1),
                "buy_approval": buy_approval,
                "p0_status": "NONE",
                "position_cycle_id": "NONE",
                "financial_data_asof": "2026-06-30",
                "forward_data_asof": trading_date,
                "intraday_last_timestamp": now_str
            }
        except Exception as e:
            logger.error(f"[ShadowScan] Error evaluating stock {stock_code}: {e}", exc_info=True)
            return None

    def _determine_snapshot_reason(
        self,
        stock_code: str,
        current_res: Dict[str, Any],
        trading_date: str,
        is_manual: bool
    ) -> Optional[str]:
        """
        Determine if a new snapshot should be appended to scan_journal.
        Deduplication rules:
        - MANUAL_RUNTIME_VERIFICATION: in manual mode.
        - DAILY_FIRST: first successful scan of the trading day.
        - SIGNAL_CHANGE: buy approval or integrated state transition.
        - BLOCKER_CHANGE: change in primary blocker.
        - TECHNICAL_STATE_CHANGE: change in technical action.
        - None: state is identical to prior snapshot (suppress duplicate).
        """
        if is_manual:
            return "MANUAL_RUNTIME_VERIFICATION_V2"

        latest = self.db.get_latest_scan_journal_for_stock(stock_code)

        if not latest:
            return "DAILY_FIRST"

        if latest.get("trading_date") != trading_date:
            return "DAILY_FIRST"

        # Check for state changes
        if latest.get("buy_approval") != current_res.get("buy_approval"):
            return "SIGNAL_CHANGE"

        if latest.get("shadow_integrated_state") != current_res.get("shadow_integrated_state"):
            return "SIGNAL_CHANGE"

        if latest.get("primary_blocker") != current_res.get("primary_blocker"):
            return "BLOCKER_CHANGE"

        if latest.get("technical_action") != current_res.get("technical_action"):
            return "TECHNICAL_STATE_CHANGE"

        # Identical state -> Suppress duplicate
        return None

# =============================================================================
# CLI Main
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Stock Analysis System V1.0 Runtime Scheduler")
    parser.add_argument(
        "--task",
        type=str,
        required=True,
        choices=["PRE_MARKET_HEALTH_CHECK", "INTRADAY_SHADOW_SCAN", "OUTCOME_AND_JOURNAL_MAINTENANCE"],
        help="Target runtime task to execute"
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Run in manual verification mode (bypasses trading day / 45m bar timing gates and tags snapshot as MANUAL_RUNTIME_VERIFICATION)"
    )

    args = parser.parse_args()
    scheduler = RuntimeScheduler()
    result = scheduler.run_task(args.task, is_manual=args.manual)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("status") in ["SUCCESS", "SKIPPED_NON_TRADING_DAY", "SKIPPED_RESERVED_WINDOW", "SKIPPED_EXISTING_JOB_ACTIVE", "SKIPPED_NO_NEW_45M_BAR"]:
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
