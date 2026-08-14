import pandas as pd
import numpy as np
from typing import Dict, Any, List
from src.analysis.technical_analysis import calculate_wilder_atr, adjust_krx_tick_size

class ATRRiskBacktester:
    """
    ATR 위험관리 엔진 버전별 성과 비교 백테스터:
    - A안: V3.5 (익절 활성 2.5 ATR / 초기손절 2.0 ATR)
    - B안: 최신 방식 (익절 활성 3.0 ATR / 초기손절 1.5 ATR)
    - C안: V4-PILOT-C (익절 활성 3.0 ATR / 초기손절 2.0 ATR -> +1.0 ATR 진행 후 1.5 ATR 트레일링 래칫)
    """
    def __init__(self, slippage_pct: float = 0.002, commission_pct: float = 0.00015, tax_pct: float = 0.0018):
        self.slippage_pct = slippage_pct
        self.fee_total = commission_pct + tax_pct + slippage_pct

    def run_backtest_on_series(self, df_daily: pd.DataFrame, entry_index: int = 15) -> Dict[str, Any]:
        """
        단일 종목 일봉 시계열에 대해 A안, B안, C안의 전략 시뮬레이션 및 성과 지표 비교
        """
        if df_daily.empty or len(df_daily) <= entry_index + 5:
            return {
                "status": "INSUFFICIENT_DATA",
                "message": "백테스트 데이터 부족 (최소 20봉 이상 필요)"
            }

        df = df_daily.copy().sort_values("stk_date").reset_index(drop=True)
        df["atr_14"] = calculate_wilder_atr(df, period=14)

        entry_row = df.iloc[entry_index]
        p0 = float(entry_row["close_price"])
        a0 = float(entry_row["atr_14"])
        if a0 <= 0:
            a0 = p0 * 0.03

        results = {}
        models = {
            "Option_A_V3.5": {"act_mul": 2.5, "init_stop_mul": 2.0, "trail_stop_mul": 2.0, "trail_profit_mul": 0.8, "progress_threshold": None},
            "Option_B_Recent": {"act_mul": 3.0, "init_stop_mul": 1.5, "trail_stop_mul": 1.5, "trail_profit_mul": 0.8, "progress_threshold": None},
            "Option_C_V4_PILOT_C": {"act_mul": 3.0, "init_stop_mul": 2.0, "trail_stop_mul": 1.5, "trail_profit_mul": 0.8, "progress_threshold": 1.0}
        }

        for model_name, cfg in models.items():
            trade_log = self._simulate_single_trade(df, entry_index, p0, a0, cfg)
            results[model_name] = trade_log

        return {
            "status": "SUCCESS",
            "entry_date": str(entry_row["stk_date"]),
            "entry_price_p0": p0,
            "anchor_atr_a0": a0,
            "comparison": results
        }

    def _simulate_single_trade(self, df: pd.DataFrame, entry_idx: int, p0: float, a0: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
        act_target = p0 + (cfg["act_mul"] * a0)
        initial_stop = p0 - (cfg["init_stop_mul"] * a0)
        
        highest_close = p0
        highest_intraday = p0
        confirmed_stop = initial_stop
        profit_active = False
        highest_after_act = 0.0
        profit_trail = 0.0
        progress_reached = False

        exit_price = None
        exit_date = None
        exit_reason = None
        holding_days = 0
        mae = 0.0
        mfe = 0.0

        for i in range(entry_idx + 1, len(df)):
            holding_days += 1
            row = df.iloc[i]
            cur_high = float(row["high_price"])
            cur_low = float(row["low_price"])
            cur_close = float(row["close_price"])
            cur_at = float(row["atr_14"]) if float(row["atr_14"]) > 0 else a0

            # MFE / MAE 기록
            cur_max_gain = (cur_high - p0) / p0
            cur_max_loss = (cur_low - p0) / p0
            mfe = max(mfe, cur_max_gain)
            mae = min(mae, cur_max_loss)

            highest_close = max(highest_close, cur_close)
            highest_intraday = max(highest_intraday, cur_high)

            # 손절선 래칫
            if cfg["progress_threshold"] is not None:
                if highest_close >= p0 + (cfg["progress_threshold"] * a0) or progress_reached:
                    progress_reached = True
                    cand_stop = highest_close - (cfg["trail_stop_mul"] * cur_at)
                else:
                    cand_stop = highest_close - (cfg["init_stop_mul"] * cur_at)
            else:
                cand_stop = highest_close - (cfg["init_stop_mul"] * cur_at)

            confirmed_stop = max(confirmed_stop, cand_stop)

            # 익절 트레일링
            if cur_high >= act_target or profit_active:
                profit_active = True
                highest_after_act = max(highest_after_act, cur_high)
                cand_trail = highest_after_act - (cfg["trail_profit_mul"] * cur_at)
                profit_trail = max(profit_trail, cand_trail)

            # 청산 검증 (장중 저가가 손절/익절선 이탈 시)
            effective_exit = max(confirmed_stop, profit_trail) if profit_active else confirmed_stop

            if cur_low <= effective_exit:
                exit_price = effective_exit
                exit_date = str(row["stk_date"])
                exit_reason = "PROFIT_TRAIL" if (profit_active and profit_trail >= confirmed_stop) else "STOP_LOSS"
                break

        if exit_price is None:
            last_row = df.iloc[-1]
            exit_price = float(last_row["close_price"])
            exit_date = str(last_row["stk_date"])
            exit_reason = "HOLDING_END"

        raw_return = (exit_price - p0) / p0
        net_return = raw_return - self.fee_total
        is_win = net_return > 0

        return {
            "p0": p0,
            "a0": a0,
            "exit_price": exit_price,
            "exit_date": exit_date,
            "exit_reason": exit_reason,
            "holding_days": holding_days,
            "raw_return_pct": round(raw_return * 100.0, 2),
            "net_return_pct": round(net_return * 100.0, 2),
            "is_win": is_win,
            "mfe_pct": round(mfe * 100.0, 2),
            "mae_pct": round(mae * 100.0, 2),
            "profit_activated": profit_active,
            "1atr_progress_reached": progress_reached
        }
