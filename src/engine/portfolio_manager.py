import json
import pathlib
import math
from typing import Dict, Any, List, Optional
from datetime import datetime
import pandas as pd
import numpy as np

from config.settings import ATR_CONFIG, ATR_ENGINE_VERSION
from src.database.db_manager import DatabaseManager
from src.utils.logger import logger
from src.api.kiwoom_api import KiwoomAPIClient
from src.analysis.technical_analysis import TechnicalAnalysis, adjust_krx_tick_size

class PortfolioManager:
    """
    ATR Risk Engine V4-PILOT-C 기반 포트폴리오 위험관리 및 포지션 라이프사이클 관리자.
    - P0(감시개시 기준가) 및 A0(고정 기준 ATR) 영속 앵커링
    - 초기 2.0 ATR -> +1.0 ATR 진행 후 1.5 ATR 트레일링 2단계 손절 래칫 (절대 하향 금지)
    - 익절 +3.0 ATR 활성 후 0.8 ATR 래칫 트레일링
    - NORMAL / RECOVERY / EMERGENCY / HOLD 매매모드 분리
    - ATR 기반 계좌 위험예산(0.5%~0.75%) & 20% 비중 상한 포지션 사이징
    - DATA_HOLD 및 비정상 변동성(NATR >= 20%) 자동 주문 차단
    """
    def __init__(self, db_manager: DatabaseManager, kiwoom_client=None):
        self.db = db_manager
        self.kiwoom = kiwoom_client if kiwoom_client else KiwoomAPIClient()
        self.config = ATR_CONFIG

    def clear_all_holdings(self):
        """테스트 및 세션 갱신 시 기존 보유 종목 데이터 초기화"""
        logger.info("[PortfolioManager] 기존 보유 종목 데이터 초기화")
        self.db.execute_non_query("DELETE FROM portfolio_positions")

    def add_holding(self, stock_code: str, stock_name: str, quantity: int, avg_buy_price: float):
        """
        보유 종목을 portfolio_positions 및 stock_info DB 테이블에 수량 및 평단가와 함께 저장합니다.
        기존 앵커(P0/A0)가 존재하면 보존하고, 신규 편입 시에만 앵커를 생성합니다.
        """
        code = str(stock_code).zfill(6)
        
        self.db.execute_non_query("""
            INSERT OR REPLACE INTO stock_info (stock_code, stock_name, market_type, updated_at)
            VALUES (?, ?, 'KRX', CURRENT_TIMESTAMP)
        """, (code, stock_name))

        # 기존 앵커 정보 조회
        existing = self.db.execute_query("SELECT anchor_price_p0, anchor_atr_a0, position_cycle_id FROM portfolio_positions WHERE stock_code = ?", (code,))
        
        if existing and existing[0]["anchor_price_p0"] and float(existing[0]["anchor_price_p0"]) > 0:
            # 기존 P0/A0 유지
            self.db.execute_non_query("""
                UPDATE portfolio_positions
                SET quantity = ?, avg_buy_price = ?, updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ?
            """, (quantity, float(avg_buy_price), code))
        else:
            # 신규 앵커 등록
            cycle_id = f"{code}_{datetime.now().strftime('%Y%m%d%H%M')}"
            self.db.execute_non_query("""
                INSERT INTO portfolio_positions (
                    stock_code, quantity, avg_buy_price, position_cycle_id, parameter_version,
                    anchor_price_p0, anchor_created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(stock_code) DO UPDATE SET
                    quantity = excluded.quantity,
                    avg_buy_price = excluded.avg_buy_price,
                    updated_at = CURRENT_TIMESTAMP
            """, (code, quantity, float(avg_buy_price), cycle_id, ATR_ENGINE_VERSION, float(avg_buy_price)))

        logger.info(f"[PortfolioManager] 보유 종목 동기화: {stock_name}({code}) {quantity}주 @ {avg_buy_price:,}원")

    def sync_portfolio_from_kiwoom(self) -> List[Dict[str, Any]]:
        """
        키움 REST API (kt00018) 실시간 계좌평가 잔고 조회를 1순위로 실행하여
        실제 보유 종목만 DB에 최신화하고 매도된 종목은 안전하게 제거합니다.
        """
        logger.info("[PortfolioManager] 키움 API 실시간 계좌 보유 종목 동기화 시작...")
        positions = self.kiwoom.get_account_positions()
        
        if positions and len(positions) > 0 and self.kiwoom.is_valid_key():
            logger.info(f"[PortfolioManager] 🔥 키움 REST API 실계좌 연동 성공! (실제 보유: {len(positions)}개 종목)")
            active_codes = [pos["stock_code"] for pos in positions]
            for pos in positions:
                self.add_holding(pos["stock_code"], pos["stock_name"], pos["quantity"], pos["avg_buy_price"])
            
            if active_codes:
                placeholders = ",".join(["?"] * len(active_codes))
                self.db.execute_non_query(f"DELETE FROM portfolio_positions WHERE stock_code NOT IN ({placeholders})", tuple(active_codes))
            
            cfg_path = pathlib.Path("config/portfolio_holdings.json")
            try:
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(positions, f, ensure_ascii=False, indent=2)
            except Exception as e_cfg:
                logger.warning(f"[PortfolioManager] json 파일 저장 중 경고: {e_cfg}")

            return positions
        else:
            logger.warning("[PortfolioManager] 키움 API 연동 불가 또는 잔고 0개 - 기존 JSON 백업 파일 로드 시도")
            cfg_path = pathlib.Path("config/portfolio_holdings.json")
            if cfg_path.exists():
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        positions = json.load(f)
                    if positions:
                        active_codes = [pos["stock_code"] for pos in positions]
                        for pos in positions:
                            self.add_holding(pos["stock_code"], pos["stock_name"], pos["quantity"], pos["avg_buy_price"])
                        if active_codes:
                            placeholders = ",".join(["?"] * len(active_codes))
                            self.db.execute_non_query(f"DELETE FROM portfolio_positions WHERE stock_code NOT IN ({placeholders})", tuple(active_codes))
                        return positions
                except Exception as e:
                    logger.error(f"[PortfolioManager] JSON 백업 로드 실패: {e}")

        mock_positions = self.kiwoom._get_mock_account_positions()
        active_codes = [pos["stock_code"] for pos in mock_positions]
        for pos in mock_positions:
            self.add_holding(pos["stock_code"], pos["stock_name"], pos["quantity"], pos["avg_buy_price"])
        if active_codes:
            placeholders = ",".join(["?"] * len(active_codes))
            self.db.execute_non_query(f"DELETE FROM portfolio_positions WHERE stock_code NOT IN ({placeholders})", tuple(active_codes))
        return mock_positions

    def get_held_portfolio_status(self, engine=None) -> List[Dict[str, Any]]:
        """
        DB에 저장된 실제 보유 종목들에 대해 V4-PILOT-C 엔진을 가동하여
        P0/A0 기반 고정 감시가, 2단계 손절 래칫, 익절 트레일링, 포지션 사이징 및 정밀 평가를 수행합니다.
        """
        rows = self.db.execute_query("""
            SELECT p.*, s.stock_name
            FROM portfolio_positions p
            JOIN stock_info s ON p.stock_code = s.stock_code
            WHERE p.quantity > 0
        """)

        if not rows:
            logger.warning("[PortfolioManager] 계좌 내 보유 종목이 없습니다.")
            return []

        # 전체 포트폴리오 1차 평가금액 계산 (계좌 위험예산 및 비중 산출용)
        raw_holdings = []
        for r in rows:
            code = r["stock_code"]
            name = r["stock_name"]
            qty = int(r["quantity"])
            avg_p = float(r["avg_buy_price"])
            
            daily_df = self.db.get_daily_prices(code)
            cp_val = float(daily_df.iloc[-1]["close_price"]) if not daily_df.empty else avg_p
            raw_holdings.append({
                "code": code,
                "name": name,
                "qty": qty,
                "avg_p": avg_p,
                "cur_p": cp_val,
                "eval_amt": qty * cp_val,
                "row": dict(r),
                "daily_df": daily_df
            })

        total_account_equity = sum(h["eval_amt"] for h in raw_holdings) or 1.0

        eval_list = []
        for h in raw_holdings:
            code = h["code"]
            name = h["name"]
            qty = h["qty"]
            avg_p = h["avg_p"]
            total_inv = qty * avg_p
            daily_df = h["daily_df"]
            current_price = h["cur_p"]
            p_row = h["row"]

            # 1. 기술적 지표 및 기본 분석 데이터 추출
            tech_eval = {}
            f_sc = 50.0
            t_sc = 50.0
            final_sc = 50.0
            completeness = 100.0
            is_etf = False
            f_confirmed = True
            analysis = None

            if engine:
                try:
                    analysis = engine.analyze_stock(code, name)
                    if analysis:
                        current_price = float(analysis.get("current_price", current_price) or current_price)
                        f_sc = float(analysis.get("f_score", 50.0) or 50.0)
                        t_sc = float(analysis.get("t_score", 50.0) or 50.0)
                        final_sc = float(analysis.get("final_score", 50.0) or 50.0)
                        completeness = float(analysis.get("data_completeness", 100.0) or 100.0)
                        is_etf = bool(analysis.get("is_etf", False))
                        f_confirmed = bool(analysis.get("f_score_confirmed", True))
                except Exception as e_an:
                    logger.error(f"[{code}] 종목 분석 중 예외: {e_an}")

            if not daily_df.empty and len(daily_df) >= 5:
                ta = TechnicalAnalysis(daily_df)
                tech_eval = ta.evaluate_signals()
            elif analysis:
                tech_eval = analysis

            eval_amount = qty * current_price
            pnl_amount = eval_amount - total_inv
            pnl_pct = round((pnl_amount / total_inv) * 100.0, 2) if total_inv > 0 else 0.0

            # 2. 직전 완료봉 Wilder ATR14 (At) 및 NATR(%) 산출
            at = float(tech_eval.get("atr_14", current_price * 0.03) or (current_price * 0.03))
            if at <= 0:
                at = current_price * 0.03
            natr_pct = round((at / (current_price + 1e-9)) * 100.0, 2)

            # 3. 🔥 P0(감시개시 기준가격) & A0(기준 ATR) 동결 및 승계 로직
            p0 = float(p_row.get("anchor_price_p0") or 0.0)
            a0 = float(p_row.get("anchor_atr_a0") or 0.0)
            cycle_id = p_row.get("position_cycle_id")
            anchor_created_at = p_row.get("anchor_created_at")

            is_migrated_anchor = False
            if p0 <= 0 or a0 <= 0:
                p0 = avg_p if avg_p > 0 else current_price
                a0 = at
                anchor_created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cycle_id = f"{code}_{datetime.now().strftime('%Y%m%d')}_MIGRATED"
                is_migrated_anchor = True

            # 4. 🔥 데이터 이상 및 자동 주문설정 차단 검증 (DATA_HOLD)
            data_validity_flag = 1
            data_hold_reasons = []

            if not f_confirmed:
                data_validity_flag = 0
                data_hold_reasons.append("DART 재무 미확정")
            if natr_pct >= self.config["natr_order_block_threshold_pct"]:
                data_validity_flag = 0
                data_hold_reasons.append(f"NATR 이상 급등({natr_pct}% >= 20%)")
            if at <= 0:
                data_validity_flag = 0
                data_hold_reasons.append("ATR 비정상(0 이하)")
            if current_price <= 0:
                data_validity_flag = 0
                data_hold_reasons.append("현재가 0 이하")

            # 5. 🔥 매매 모드(Trade Mode) 판정
            mode_override = p_row.get("mode_override")
            if data_validity_flag == 0:
                trade_mode = "HOLD"
            elif mode_override:
                trade_mode = str(mode_override).upper()
            elif pnl_pct <= -25.0 and code == "348340":
                trade_mode = "EMERGENCY"  # 뉴로메카 등 고위험 비상축소
            elif pnl_pct <= -20.0:
                trade_mode = "RECOVERY"   # 손실 종목 반등 시 손실축소
            else:
                trade_mode = "NORMAL"

            # 6. 🔥 V4 고정 감시가격 (P0, A0 기반) 산출
            buy_watch_mul = self.config["buy_watch_multiple"]      # 1.5
            buy_rebound_mul = self.config["buy_rebound_multiple"]  # 0.5
            init_stop_mul = self.config["initial_stop_multiple"]    # 2.0
            profit_act_mul = self.config["profit_activation_multiple"] # 3.0

            raw_buy_watch = p0 - (buy_watch_mul * a0)
            rebound_delta = int(round(a0 * buy_rebound_mul))
            raw_initial_stop = p0 - (init_stop_mul * a0)
            raw_profit_activation = p0 + (profit_act_mul * a0)
            
            # 보유종목 상승률 캡 (필요 시 적용값과 원시값 구분)
            effective_profit_activation = raw_profit_activation
            profit_cap_applied = False

            # 7. 🔥 2단계 손절 래칫 (InitialStop -> +1.0ATR 후 1.5ATR 트레일링)
            prev_highest_close = float(p_row.get("highest_close") or p_row.get("highest_close_price") or 0.0)
            highest_close = max(prev_highest_close, float(current_price))
            
            prev_1atr_reached = bool(p_row.get("profit_progress_1atr_reached", 0))
            profit_progress_1atr_reached = 1 if (highest_close >= (p0 + (1.0 * a0)) or prev_1atr_reached) else 0

            # +1.0 ATR 달성 여부에 따른 후보 손절가 계산 (직전 완료봉 At 적용)
            if profit_progress_1atr_reached == 1:
                candidate_stop = highest_close - (self.config["trailing_stop_multiple"] * at)
            else:
                candidate_stop = highest_close - (self.config["initial_stop_multiple"] * at)

            prev_confirmed_stop = float(p_row.get("previous_confirmed_stop") or p_row.get("confirmed_stop_price") or 0.0)
            
            # 래칫 원칙: 직전 손절가 및 초기 손절가보다 낮아질 수 없음
            ratchet_stop = max(prev_confirmed_stop, raw_initial_stop, candidate_stop)

            # 호가단위 보정
            kiwoom_stop_tick = adjust_krx_tick_size(ratchet_stop, "down")
            
            # 🔥 호가보정 후에도 절대 하향 금지 재검증
            if prev_confirmed_stop > 0 and kiwoom_stop_tick < prev_confirmed_stop:
                kiwoom_stop_tick = int(prev_confirmed_stop)
                ratchet_stop = max(ratchet_stop, prev_confirmed_stop)

            if prev_confirmed_stop == 0:
                stop_update_status = "🆕 신규설정"
            elif kiwoom_stop_tick > prev_confirmed_stop:
                stop_update_status = "⬆️ 상향갱신"
            else:
                stop_update_status = "유지"

            # 8. 🔥 익절 트레일링 활성 및 실행선 산출
            prev_act_status = p_row.get("profit_activation_status", "INACTIVE")
            profit_act_status = "ACTIVE" if (current_price >= effective_profit_activation or prev_act_status == "ACTIVE") else "INACTIVE"
            
            prev_highest_act = float(p_row.get("highest_after_activation") or 0.0)
            highest_after_activation = max(prev_highest_act, current_price) if profit_act_status == "ACTIVE" else 0.0
            
            # 모드별 트레일링폭 결정
            if trade_mode == "RECOVERY":
                trail_mul = self.config["recovery_trail_min"] + self.config["recovery_trail_max"] / 2.0 # 0.3
            elif trade_mode == "EMERGENCY":
                trail_mul = self.config["emergency_trail_multiple"] # 0.15
            else:
                trail_mul = self.config["normal_profit_trail_multiple"] # 0.8

            profit_trail_delta = int(round(at * trail_mul))
            prev_profit_trail = float(p_row.get("profit_trail") or 0.0)

            if profit_act_status == "ACTIVE":
                candidate_profit_trail = highest_after_activation - (trail_mul * at)
                profit_trail = max(prev_profit_trail, candidate_profit_trail)
                kiwoom_target_tick = adjust_krx_tick_size(profit_trail, "down")
            else:
                profit_trail = 0.0
                kiwoom_target_tick = adjust_krx_tick_size(effective_profit_activation, "up")

            # 최종 유효 매도선
            effective_exit_line = max(ratchet_stop, profit_trail)
            kiwoom_exit_tick = adjust_krx_tick_size(effective_exit_line, "down")

            # 9. 🔥 ATR 기반 포지션 사이징 및 계좌 위험예산
            krx_unit = adjust_krx_tick_size(current_price, "down") - adjust_krx_tick_size(current_price - 1, "down") or 10
            slippage_buffer = max(0.1 * a0, 5.0 * krx_unit)
            risk_per_share = max(1.0, (p0 - raw_initial_stop) + slippage_buffer)
            
            is_top_confirmed = (f_confirmed and f_sc >= 70.0 and t_sc >= 70.0)
            account_risk_pct = self.config["max_account_risk_pct"] if is_top_confirmed else self.config["default_account_risk_pct"]
            risk_budget_amount = total_account_equity * account_risk_pct
            
            risk_based_qty = math.floor(risk_budget_amount / risk_per_share)
            weight_cap_qty = math.floor((total_account_equity * (self.config["max_position_weight_pct"] / 100.0)) / (current_price + 1e-9))
            recommended_qty = max(0, min(risk_based_qty, weight_cap_qty))

            # 10. 호가 보정값 정리
            kiwoom_buy_tick = adjust_krx_tick_size(raw_buy_watch, "down") if raw_buy_watch > 0 else 0

            if data_validity_flag == 0:
                display_stop_tick = "HOLD"
                display_target_tick = "HOLD"
                display_buy_tick = "HOLD"
                auto_order_enabled = False
            else:
                display_stop_tick = kiwoom_stop_tick
                display_target_tick = kiwoom_target_tick
                display_buy_tick = kiwoom_buy_tick
                auto_order_enabled = True

            # 11. 45분봉 수급 지표 수집
            try:
                from src.analysis.intraday_analysis import Intraday45mAnalyzer
                intra_analyzer = Intraday45mAnalyzer()
                intra_res = intra_analyzer.analyze_45m_indicators(code)
            except Exception as e_intra:
                logger.error(f"45분봉 지표 수집 실패 ({code}): {e_intra}")
                intra_res = {
                    "adx_14_45m": 0.0, "plus_di_45m": 0.0, "minus_di_45m": 0.0,
                    "obv_45m": 0, "obv_45m_trend": "미수집", "chaikin_osc_45m": 0,
                    "chaikin_flow_45m": "미수집", "signal_45m_text": "대기",
                    "intraday_cho_recent2": [0, 0], "intraday_cho_is_subzero_2bars": False,
                    "intraday_cho_note": "", "is_45m_breakdown": False,
                    "is_obv_dead": False, "is_cho_outflow": False, "is_45m_bearish_2plus": False
                }

            # 12. DB 업데이트 (V4 30개 필드 영속 저장)
            self.db.execute_non_query("""
                UPDATE portfolio_positions SET
                    position_cycle_id = ?, parameter_version = ?, trade_mode = ?, mode_override = ?,
                    anchor_price_p0 = ?, anchor_atr_a0 = ?, anchor_created_at = ?, atr_method = ?, atr_timeframe = ?,
                    current_completed_atr = ?, natr_pct = ?, initial_stop = ?, profit_progress_1atr_reached = ?,
                    highest_close = ?, highest_intraday = ?, previous_confirmed_stop = ?, ratchet_stop = ?,
                    profit_activation_raw = ?, profit_activation_effective = ?, profit_activation_status = ?,
                    highest_after_activation = ?, previous_profit_trail = ?, profit_trail = ?, effective_exit_line = ?,
                    account_risk_pct = ?, risk_budget_amount = ?, risk_per_share = ?, recommended_quantity = ?,
                    slippage_buffer = ?, data_validity_flag = ?, data_hold_reason = ?,
                    highest_close_price = ?, confirmed_stop_price = ?, updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ?
            """, (
                cycle_id, ATR_ENGINE_VERSION, trade_mode, mode_override,
                p0, a0, anchor_created_at, self.config["atr_method"], self.config["atr_timeframe"],
                at, natr_pct, raw_initial_stop, profit_progress_1atr_reached,
                highest_close, highest_close, kiwoom_stop_tick, ratchet_stop,
                raw_profit_activation, effective_profit_activation, profit_act_status,
                highest_after_activation, prev_profit_trail, profit_trail, effective_exit_line,
                account_risk_pct, risk_budget_amount, risk_per_share, recommended_qty,
                slippage_buffer, data_validity_flag, " / ".join(data_hold_reasons) if data_hold_reasons else "정상",
                highest_close, kiwoom_stop_tick, code
            ))

            eval_list.append({
                "stock_code": code,
                "stock_name": name,
                "quantity": qty,
                "avg_buy_price": int(avg_p),
                "current_price": current_price,
                "daily_change_pct": analysis.get("daily_change_pct", 0.0) if analysis else 0.0,
                "high_low_swing_pct": analysis.get("high_low_swing_pct", 0.0) if analysis else 0.0,
                "total_invested": int(total_inv),
                "eval_amount": int(eval_amount),
                "eval_weight_pct": 0.0,
                "pnl_amount": int(pnl_amount),
                "pnl_pct": pnl_pct,

                # V4 파라미터 및 앵커
                "parameter_version": ATR_ENGINE_VERSION,
                "trade_mode": trade_mode,
                "position_cycle_id": cycle_id,
                "anchor_price_p0": int(round(p0)),
                "anchor_atr_a0": round(a0, 1),
                "current_completed_atr": round(at, 1),
                "natr_pct": natr_pct,
                "atr_14": round(at, 1),
                "atr_pct": natr_pct,
                "is_migrated_anchor": is_migrated_anchor,

                # V4 매매가격선
                "buy_watch_price": int(round(raw_buy_watch)),
                "buy_rebound_delta": rebound_delta,
                "initial_stop_price": int(round(raw_initial_stop)),
                "highest_close_price": int(round(highest_close)),
                "profit_progress_1atr_reached": bool(profit_progress_1atr_reached),
                "prev_confirmed_stop": int(round(prev_confirmed_stop)),
                "ratchet_stop_price": int(round(ratchet_stop)),
                "confirmed_stop_price": kiwoom_stop_tick,
                "stop_update_status": stop_update_status,

                # 익절 및 트레일링선
                "profit_activation_raw": int(round(raw_profit_activation)),
                "profit_activation_effective": int(round(effective_profit_activation)),
                "profit_activation_status": profit_act_status,
                "profit_trail_delta": profit_trail_delta,
                "profit_trail_price": int(round(profit_trail)),
                "effective_exit_line": int(round(effective_exit_line)),
                "target_profit_price": kiwoom_target_tick,

                # 키움 호가 보정값
                "kiwoom_buy_tick_price": display_buy_tick,
                "kiwoom_stop_tick_price": display_stop_tick,
                "kiwoom_target_tick_price": display_target_tick,
                "kiwoom_exit_tick_price": kiwoom_exit_tick if auto_order_enabled else "HOLD",

                # 포지션 사이징
                "slippage_buffer": int(round(slippage_buffer)),
                "risk_per_share": int(round(risk_per_share)),
                "account_risk_pct": account_risk_pct * 100.0,
                "risk_budget_amount": int(round(risk_budget_amount)),
                "risk_based_qty": risk_based_qty,
                "weight_cap_qty": weight_cap_qty,
                "recommended_quantity": recommended_qty,
                "data_validity_flag": data_validity_flag,
                "data_hold_reason": " / ".join(data_hold_reasons) if data_hold_reasons else "정상",
                "auto_order_enabled": auto_order_enabled,

                # 점수 및 수급
                "f_score": f_sc,
                "t_score": t_sc,
                "final_score": final_sc,
                "growth_pts": analysis.get("growth_pts", 0.0) if analysis else 0.0,
                "cf_pts": analysis.get("cf_pts", 0.0) if analysis else 0.0,
                "cat_pts": analysis.get("cat_pts", 0.0) if analysis else 0.0,
                "stab_pts": analysis.get("stab_pts", 0.0) if analysis else 0.0,
                "val_pts": analysis.get("val_pts", 0.0) if analysis else 0.0,
                "data_completeness": completeness,
                "is_etf": is_etf,
                "f_score_confirmed": f_confirmed,

                # 45분봉 상세
                "obv_dead_date": tech_eval.get("obv_dead_date", "N/A"),
                "obv_dead_elapsed_days": tech_eval.get("obv_dead_elapsed_days", 0),
                "daily_cho_recent2": tech_eval.get("daily_cho_recent2", [0, 0]),
                "daily_cho_is_subzero_2bars": tech_eval.get("daily_cho_is_subzero_2bars", False),
                "adx_di_dominance": tech_eval.get("adx_di_dominance", "-"),
                "is_minus_di_dominant": tech_eval.get("is_minus_di_dominant", False),
                "is_tier3_sell_a": tech_eval.get("is_tier3_sell_a", False),
                "is_tier3_sell_b": tech_eval.get("is_tier3_sell_b", False),
                "is_tier3_sell": tech_eval.get("is_tier3_sell", False),
                "adx_14_45m": intra_res.get("adx_14_45m", 0.0),
                "plus_di_45m": intra_res.get("plus_di_45m", 0.0),
                "minus_di_45m": intra_res.get("minus_di_45m", 0.0),
                "obv_45m": intra_res.get("obv_45m", 0),
                "obv_45m_trend": intra_res.get("obv_45m_trend", "미수집"),
                "chaikin_osc_45m": intra_res.get("chaikin_osc_45m", 0),
                "chaikin_flow_45m": intra_res.get("chaikin_flow_45m", "미수집"),
                "intraday_cho_recent2": intra_res.get("intraday_cho_recent2", [0, 0]),
                "intraday_cho_is_subzero_2bars": intra_res.get("intraday_cho_is_subzero_2bars", False),
                "intraday_cho_note": intra_res.get("intraday_cho_note", ""),
                "is_45m_breakdown": intra_res.get("is_45m_breakdown", False),
                "is_obv_dead": intra_res.get("is_obv_dead", False),
                "is_cho_outflow": intra_res.get("is_cho_outflow", False),
                "is_45m_bearish_2plus": intra_res.get("is_45m_bearish_2plus", False),
                "signal_45m_text": intra_res.get("signal_45m_text", "대기"),
                "action_status": "안정 보유 (홀딩)",
                "reason": analysis.get("reason", "분석 데이터 정상") if analysis else "데이터 부족"
            })

        # 계좌 비중(%) 산출
        for item in eval_list:
            item["eval_weight_pct"] = round((item["eval_amount"] / total_account_equity) * 100.0, 1)

        # 순위 3원화 (확정 순위 / 잠정 순위 / ETF 순위)
        confirmed_stocks = [x for x in eval_list if not x["is_etf"] and x["f_score_confirmed"]]
        unconfirmed_stocks = [x for x in eval_list if not x["is_etf"] and not x["f_score_confirmed"]]
        etf_stocks = [x for x in eval_list if x["is_etf"]]

        confirmed_stocks.sort(key=lambda x: x["final_score"], reverse=True)
        unconfirmed_stocks.sort(key=lambda x: x["final_score"], reverse=True)
        etf_stocks.sort(key=lambda x: x["t_score"], reverse=True)

        for rank_idx, item in enumerate(confirmed_stocks, 1):
            item["rank"] = f"확정 {rank_idx}위"

        for rank_idx, item in enumerate(unconfirmed_stocks, 1):
            item["rank"] = f"잠정 {rank_idx}위"

        for rank_idx, item in enumerate(etf_stocks, 1):
            item["rank"] = f"ETF {rank_idx}위"

        # 매매 대응전략 5단계 복합 판정
        for item in eval_list:
            weight_pct = item["eval_weight_pct"]
            f_confirmed = item["f_score_confirmed"]
            completeness = item["data_completeness"]
            rank = item.get("rank", "순위")
            trade_mode = item.get("trade_mode", "NORMAL")

            is_tier3_sell = item.get("is_tier3_sell", False)
            is_minus_di_dominant = item.get("is_minus_di_dominant", False)
            adx_note = " (ADX -DI우세 확인)" if is_minus_di_dominant else " (ADX 방향 불일치, 참고)"
            intraday_cho_note = item.get("intraday_cho_note", "")
            is_45m_bearish_2plus = item.get("is_45m_bearish_2plus", False)
            is_45m_breakdown = item.get("is_45m_breakdown", False)
            is_cho_outflow = item.get("is_cho_outflow", False)

            if trade_mode == "HOLD" or not f_confirmed or completeness < 90.0:
                item["action_status"] = f"⚠️ {item['data_hold_reason']} (보류)"
            elif trade_mode == "EMERGENCY":
                item["action_status"] = "🚨 반등 시 긴급 비중축소"
            elif trade_mode == "RECOVERY":
                item["action_status"] = "🔄 반등 시 손실축소 분할매도"
            elif weight_pct > self.config["max_position_weight_pct"]:
                if is_tier3_sell:
                    item["action_status"] = f"⚠️ 비중과다({weight_pct}%) + 🚨 매도조건 충족 (추매금지/보유)"
                elif is_45m_breakdown:
                    item["action_status"] = f"⚠️ 비중과다({weight_pct}%) + 🚨 45m 이중수급이탈 (추매금지/보유)"
                elif is_cho_outflow:
                    item["action_status"] = f"⚠️ 비중과다({weight_pct}%) + ⚠️ 45m CHO유출 (추매금지/보유)"
                else:
                    item["action_status"] = f"⚠️ 비중과다({weight_pct}%) 집중위험 (추매금지/보유)"
            elif is_tier3_sell:
                item["action_status"] = f"🚨 매도 대응{adx_note}{intraday_cho_note}"
            elif is_45m_bearish_2plus and item["t_score"] >= 50.0:
                item["action_status"] = "🎯 45m 눌림목 분할매수"
            else:
                item["action_status"] = "🟢 계속 보유/홀딩"

        return eval_list
