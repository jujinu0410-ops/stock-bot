import math
from typing import Dict, Any, Optional
from config.settings import ATR_CONFIG, ATR_ENGINE_VERSION
from src.analysis.technical_analysis import adjust_krx_tick_size
from src.utils.logger import logger

class ATRRiskEngine:
    """
    ATR Risk Engine V4 Single Source of Truth.
    - 매수 전 Watchlist: candidate_reference_price, candidate_reference_atr 및 후보 가격선 (PREVIEW_ONLY)
    - 포지션 진입 후: P0(최초 진입가), A0(진입 시점 ATR14) 영속 앵커링
    - 운영 초기손절: S0 = P0 - 1.5 * A0 (단일 표준 공식)
    - 장마감 후 래칫 손절: S_new = H_close - 1.5 * At ➔ S_final = max(S_prev, S0, S_new) (절대 하향 금지)
    - 트레일링 익절: TrailActivation = P0 + 3.0 * A0 도달 시 ACTIVE ➔ TrailLine = max(T_prev, H_high - 0.8 * At)
    - 포지션 사이징: 계좌 위험예산(0.5%~0.75%) 및 단일종목 20% 상한 적용
    """

    @staticmethod
    def calculate_candidate_preview(current_price: float, atr_14: float, is_etf: bool = False) -> Dict[str, Any]:
        """
        [매수 전 Watchlist 단계]
        실제 포지션 P0/A0와 명확히 분리된 감시 후보 가격선 산출 (상태: PREVIEW_ONLY).
        """
        cp = float(current_price)
        atr = float(atr_14) if atr_14 and atr_14 > 0 else cp * 0.03

        buy_watch_mul = ATR_CONFIG.get("buy_watch_multiple", 1.5)
        stop_mul = ATR_CONFIG.get("initial_stop_multiple", 1.5)
        target_mul = ATR_CONFIG.get("profit_activation_multiple", 3.0)

        raw_buy = cp - (buy_watch_mul * atr)
        raw_stop = cp - (stop_mul * atr)
        raw_target = cp + (target_mul * atr)

        rebound_delta = int(round(atr * ATR_CONFIG.get("buy_rebound_multiple", 0.5)))
        sell_drop_delta = int(round(atr * ATR_CONFIG.get("normal_profit_trail_multiple", 0.8)))

        return {
            "lifecycle_status": "PREVIEW_ONLY",
            "candidate_reference_price": int(round(cp)),
            "candidate_reference_atr": round(atr, 1),
            "raw_buy_price": raw_buy,
            "raw_stop_price": raw_stop,
            "raw_target_price": raw_target,
            "candidate_buy_price": adjust_krx_tick_size(raw_buy, "down", is_etf=is_etf),
            "candidate_stop_price": adjust_krx_tick_size(raw_stop, "down", is_etf=is_etf),
            "candidate_target_price": adjust_krx_tick_size(raw_target, "up", is_etf=is_etf),
            "buy_rebound_delta": rebound_delta,
            "sell_drop_delta": sell_drop_delta
        }

    @staticmethod
    def calculate_position_risk(
        p0: float,
        a0: float,
        at: float,
        current_price: float,
        highest_close: float,
        highest_high: float,
        prev_confirmed_stop: float = 0.0,
        prev_profit_trail: float = 0.0,
        prev_activation_status: str = "INACTIVE",
        trade_mode: str = "NORMAL",
        entry_stage: int = 1,
        lifecycle_status: str = "POSITION_OPEN",
        total_equity: float = 1.0,
        current_qty: int = 0,
        is_etf: bool = False,
        is_top_confirmed: bool = False,
        is_suspended: bool = False,
        data_validity_flag: int = 1
    ) -> Dict[str, Any]:
        """
        [포지션 보유 단계]
        P0/A0 기반의 손절 래칫, 익절 트레일링, 위험예산 포지션 사이징을 산출하는 Single Source of Truth.
        """
        cp = float(current_price)
        p0 = float(p0) if p0 > 0 else cp
        a0 = float(a0) if a0 > 0 else (cp * 0.03)
        at = float(at) if at > 0 else a0

        h_close = max(float(highest_close), cp)
        h_high = max(float(highest_high), h_close, cp)

        # 1. 모드별 배수 파라미터 결정
        if trade_mode == "RECOVERY":
            init_stop_mul = 1.5
            trail_stop_mul = 1.5
            profit_act_mul = ATR_CONFIG.get("recovery_profit_activation_multiple", 1.2)
            trail_mul = ATR_CONFIG.get("recovery_trail_multiple", 0.3)
        elif trade_mode == "EMERGENCY":
            init_stop_mul = 1.0
            trail_stop_mul = 1.0
            profit_act_mul = ATR_CONFIG.get("emergency_profit_activation_multiple", 1.0)
            trail_mul = ATR_CONFIG.get("emergency_trail_multiple", 0.15)
        else: # NORMAL, CONCENTRATION_RISK, etc.
            init_stop_mul = ATR_CONFIG.get("initial_stop_multiple", 1.5)
            trail_stop_mul = ATR_CONFIG.get("trailing_stop_multiple", 1.5)
            profit_act_mul = ATR_CONFIG.get("profit_activation_multiple", 3.0)
            trail_mul = ATR_CONFIG.get("normal_profit_trail_multiple", 0.8)

        # 2. 초기손절 (S0 = P0 - 1.5 * A0)
        raw_initial_stop = max(0.0, p0 - (init_stop_mul * a0))

        # 3. 장마감 후 손절 래칫 (S_new = H_close - 1.5 * At, S_final = max(S_prev, S0, S_new))
        candidate_stop = max(0.0, h_close - (trail_stop_mul * at))
        valid_prev_stop = float(prev_confirmed_stop) if (0 < float(prev_confirmed_stop) < cp) else 0.0
        ratchet_stop = max(valid_prev_stop, raw_initial_stop, candidate_stop)

        kiwoom_stop_tick = adjust_krx_tick_size(ratchet_stop, "down", is_etf=is_etf)

        # 4. 익절 트레일링 활성 및 추적선
        raw_profit_activation = p0 + (profit_act_mul * a0)
        profit_act_status = "ACTIVE" if (h_high >= raw_profit_activation or prev_activation_status == "ACTIVE") else "INACTIVE"

        profit_trail_delta = int(round(at * trail_mul))
        prev_pt = float(prev_profit_trail) if float(prev_profit_trail) > 0 else 0.0

        if profit_act_status == "ACTIVE":
            candidate_profit_trail = h_high - (trail_mul * at)
            profit_trail = max(prev_pt, candidate_profit_trail)
            kiwoom_target_tick = adjust_krx_tick_size(profit_trail, "down", is_etf=is_etf)
        else:
            profit_trail = 0.0
            kiwoom_target_tick = adjust_krx_tick_size(raw_profit_activation, "up", is_etf=is_etf)

        # 5. 최종 유효 청산선 (Effective Exit Line = max(RatchetStop, ProfitTrail))
        effective_exit_line = max(ratchet_stop, profit_trail)
        kiwoom_exit_tick = adjust_krx_tick_size(effective_exit_line, "down", is_etf=is_etf)

        # 6. 포지션 사이징 및 위험예산
        krx_unit = adjust_krx_tick_size(cp, "down", is_etf=is_etf) - adjust_krx_tick_size(cp - 1, "down", is_etf=is_etf) or 10
        slippage_buffer = max(0.1 * a0, 5.0 * krx_unit)
        risk_per_share = max(1.0, (p0 - raw_initial_stop) + slippage_buffer)

        account_risk_pct = ATR_CONFIG.get("max_account_risk_pct", 0.0075) if is_top_confirmed else ATR_CONFIG.get("default_account_risk_pct", 0.005)
        risk_budget_amount = total_equity * account_risk_pct

        risk_target_qty = math.floor(round(risk_budget_amount / risk_per_share, 6))
        max_weight_pct = ATR_CONFIG.get("max_position_weight_pct", 20.0)
        weight_cap_qty = math.floor(round((total_equity * (max_weight_pct / 100.0)) / cp, 6)) if cp > 0 else 0
        final_risk_target_qty = max(0, min(risk_target_qty, weight_cap_qty))

        excess_qty = max(0, current_qty - final_risk_target_qty)
        weight_excess_qty = max(0, current_qty - weight_cap_qty)

        # 7. 분할매수 감시선
        buy_watch_mul = ATR_CONFIG.get("buy_watch_multiple", 1.5)
        raw_buy_watch = max(0.0, p0 - (buy_watch_mul * a0))
        kiwoom_buy_tick = adjust_krx_tick_size(raw_buy_watch, "down", is_etf=is_etf) if raw_buy_watch > 0 else 0
        rebound_delta = int(round(a0 * ATR_CONFIG.get("buy_rebound_multiple", 0.5)))

        return {
            "parameter_version": ATR_ENGINE_VERSION,
            "trade_mode": trade_mode,
            "entry_stage": entry_stage,
            "lifecycle_status": lifecycle_status,
            "anchor_price_p0": int(round(p0)),
            "anchor_atr_a0": round(a0, 1),
            "current_completed_atr": round(at, 1),
            "raw_initial_stop": raw_initial_stop,
            "candidate_stop": candidate_stop,
            "ratchet_stop": ratchet_stop,
            "kiwoom_stop_tick": kiwoom_stop_tick,
            "raw_profit_activation": raw_profit_activation,
            "profit_activation_status": profit_act_status,
            "profit_trail_delta": profit_trail_delta,
            "profit_trail": profit_trail,
            "kiwoom_target_tick": kiwoom_target_tick,
            "effective_exit_line": effective_exit_line,
            "kiwoom_exit_tick": kiwoom_exit_tick,
            "raw_buy_watch": raw_buy_watch,
            "kiwoom_buy_tick": kiwoom_buy_tick,
            "buy_rebound_delta": rebound_delta,
            "slippage_buffer": slippage_buffer,
            "risk_per_share": risk_per_share,
            "account_risk_pct": account_risk_pct,
            "risk_budget_amount": risk_budget_amount,
            "risk_target_qty": risk_target_qty,
            "weight_cap_qty": weight_cap_qty,
            "final_risk_target_qty": final_risk_target_qty,
            "excess_qty": excess_qty,
            "weight_excess_qty": weight_excess_qty
        }
