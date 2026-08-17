import math
from typing import Dict, Any, List, Optional
from src.database.db_manager import DatabaseManager
from src.utils.logger import logger

class OutcomeEvaluator:
    """
    Phase 7 Outcome Evaluator
    - Scan Journal 스냅샷 시점의 진입 기준가(entry_reference_price)와 고정 14일 ATR(entry_atr14)을 기준으로,
      이후 실제 거래일(Trading Days) 5d, 10d, 20d, 40d 동안의 성과, MFE/MAE, ATR 레벨 터치 여부를 계산합니다.
    - 달력일(Calendar Days) 및 비영업일/주말을 엄격히 배제하고 실제 개장된 거래일만 집계합니다.
    - 과거 스냅샷 기준선을 변경하거나 미래 데이터를 소급하지 않습니다 (Look-Ahead Bias 차단).
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def evaluate_journal_outcome(
        self,
        journal_id: str,
        outcome_type: str = "SHADOW_OUTCOME"
    ) -> Optional[Dict[str, Any]]:
        """
        단일 Scan Journal에 대해 사후 거래일 성과 평가 및 DB 저장
        """
        journal = self.db.get_scan_journal(journal_id)
        if not journal:
            logger.warning(f"Journal not found: {journal_id}")
            return None

        stock_code = journal["stock_code"]
        trading_date = journal["trading_date"] # 'YYYY-MM-DD' or 'YYYYMMDD'
        clean_date = trading_date.replace("-", "")

        entry_price = float(journal["market_price"])
        entry_atr = float(journal["atr14"]) if journal["atr14"] and float(journal["atr14"]) > 0 else (entry_price * 0.03)

        # kiwoom_daily에서 스캔 당일 이후의 거래일 40봉 조회 (오름차순)
        query = """
            SELECT stk_date, open_price, high_price, low_price, close_price, volume
            FROM kiwoom_daily
            WHERE stock_code = ? AND stk_date > ?
            ORDER BY stk_date ASC
            LIMIT 40
        """
        rows = self.db.execute_query(query, (stock_code, clean_date))
        daily_bars = [dict(r) for r in rows] if rows else []

        eval_count = len(daily_bars)

        # 변동 및 극값 추적
        max_p_40d = entry_price
        min_p_40d = entry_price

        # ATR 레벨 플래그
        hit_plus_1atr = False
        hit_plus_2atr = False
        hit_plus_3atr = False
        hit_minus_1atr = False
        hit_minus_1_5atr = False
        stop_hit = False
        trailing_hit = False

        # 기간별 성과 슬롯
        returns = {5: None, 10: None, 20: None, 40: None}
        mfes = {5: None, 10: None, 20: None, 40: None}
        maes = {5: None, 10: None, 20: None, 40: None}

        # 일별 누적 추적
        cur_max = entry_price
        cur_min = entry_price

        for idx, bar in enumerate(daily_bars, 1):
            h = float(bar["high_price"])
            l = float(bar["low_price"])
            c = float(bar["close_price"])

            if h > cur_max:
                cur_max = h
            if l < cur_min:
                cur_min = l

            # ATR 레벨 터치 검사
            if h >= entry_price + (1.0 * entry_atr):
                hit_plus_1atr = True
            if h >= entry_price + (2.0 * entry_atr):
                hit_plus_2atr = True
            if h >= entry_price + (3.0 * entry_atr):
                hit_plus_3atr = True
                trailing_hit = True

            if l <= entry_price - (1.0 * entry_atr):
                hit_minus_1atr = True
            if l <= entry_price - (1.5 * entry_atr):
                hit_minus_1_5atr = True
                stop_hit = True

            # 5d, 10d, 20d, 40d 시점 기록
            if idx == 5:
                returns[5] = round(((c - entry_price) / entry_price) * 100.0, 2)
                mfes[5] = round(((cur_max - entry_price) / entry_price) * 100.0, 2)
                maes[5] = round(((cur_min - entry_price) / entry_price) * 100.0, 2)
            elif idx == 10:
                returns[10] = round(((c - entry_price) / entry_price) * 100.0, 2)
                mfes[10] = round(((cur_max - entry_price) / entry_price) * 100.0, 2)
                maes[10] = round(((cur_min - entry_price) / entry_price) * 100.0, 2)
            elif idx == 20:
                returns[20] = round(((c - entry_price) / entry_price) * 100.0, 2)
                mfes[20] = round(((cur_max - entry_price) / entry_price) * 100.0, 2)
                maes[20] = round(((cur_min - entry_price) / entry_price) * 100.0, 2)
            elif idx == 40:
                returns[40] = round(((c - entry_price) / entry_price) * 100.0, 2)
                mfes[40] = round(((cur_max - entry_price) / entry_price) * 100.0, 2)
                maes[40] = round(((cur_min - entry_price) / entry_price) * 100.0, 2)

        max_p_40d = cur_max
        min_p_40d = cur_min

        # 20D ATR 배수
        mfe_20d_atr = round((mfes[20] / (entry_atr / entry_price * 100.0)), 2) if (mfes[20] is not None and entry_atr > 0) else None
        mae_20d_atr = round((maes[20] / (entry_atr / entry_price * 100.0)), 2) if (maes[20] is not None and entry_atr > 0) else None

        # 상태 결정
        if eval_count >= 40:
            outcome_status = "COMPLETED_40D"
        elif eval_count >= 20:
            outcome_status = "PARTIAL_20D"
        elif eval_count >= 10:
            outcome_status = "PARTIAL_10D"
        elif eval_count >= 5:
            outcome_status = "PARTIAL_5D"
        elif eval_count > 0:
            outcome_status = f"IN_PROGRESS_{eval_count}D"
        else:
            outcome_status = "PENDING"

        outcome_entry = {
            "journal_id": journal_id,
            "outcome_type": outcome_type,
            "entry_reference_price": entry_price,
            "entry_atr14": entry_atr,
            "trading_days_evaluated": eval_count,
            "return_5d": returns[5],
            "return_10d": returns[10],
            "return_20d": returns[20],
            "return_40d": returns[40],
            "mfe_5d": mfes[5],
            "mae_5d": maes[5],
            "mfe_10d": mfes[10],
            "mae_10d": maes[10],
            "mfe_20d": mfes[20],
            "mae_20d": maes[20],
            "mfe_40d": mfes[40],
            "mae_40d": maes[40],
            "mfe_20d_atr": mfe_20d_atr,
            "mae_20d_atr": mae_20d_atr,
            "max_price_40d": max_p_40d,
            "min_price_40d": min_p_40d,
            "hit_plus_1atr": hit_plus_1atr,
            "hit_plus_2atr": hit_plus_2atr,
            "hit_plus_3atr": hit_plus_3atr,
            "hit_minus_1atr": hit_minus_1atr,
            "hit_minus_1_5atr": hit_minus_1_5atr,
            "stop_hit": stop_hit,
            "trailing_activation_hit": trailing_hit,
            "outcome_status": outcome_status
        }

        self.db.upsert_signal_outcome(outcome_entry)
        return outcome_entry
