import pandas as pd
from typing import Dict, Any, Optional, List
from src.database.db_manager import DatabaseManager
from src.analysis.technical_analysis import TechnicalAnalysis
from src.analysis.fundamental_analysis import FundamentalAnalysis
from src.utils.logger import logger

class TradingEngine:
    """
    종목별 시세, 수급, 재무 데이터를 종합 분석하여 1차 매수, 2차 추매, 3차 불타기, 익절/축소 신호를 포착합니다.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def analyze_stock(self, stock_code: str, analysis_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        단일 종목에 대한 종합 정밀 분석 수행 및 매매 신호 추출
        """
        try:
            # 1. 종목 기본 정보 조회
            stock_rows = self.db.execute_query("SELECT * FROM stock_info WHERE stock_code = ?", (stock_code,))
            if not stock_rows:
                logger.warning(f"[{stock_code}] 종목 기본 정보가 stock_info 테이블에 존재하지 않습니다.")
                return None
            stock_info = dict(stock_rows[0])
            stock_name = stock_info.get("stock_name", stock_code)

            # 2. 키움 일봉 수급 데이터 조회 (최신순 60봉)
            daily_rows = self.db.execute_query(
                "SELECT * FROM kiwoom_daily WHERE stock_code = ? ORDER BY stk_date ASC", (stock_code,)
            )
            if not daily_rows or len(daily_rows) < 5:
                logger.warning(f"[{stock_name}({stock_code})] 일봉 시세 데이터가 부족하여 분석을 건너뜁니다.")
                return None

            df_daily = pd.DataFrame([dict(r) for r in daily_rows])
            if analysis_date is None:
                analysis_date = str(df_daily.iloc[-1]['stk_date'])

            # 3. DART 재무 데이터 조회
            dart_rows = self.db.execute_query(
                "SELECT * FROM dart_financials WHERE stock_code = ? ORDER BY fiscal_year DESC, quarter_code DESC LIMIT 1",
                (stock_code,)
            )
            dart_data = dict(dart_rows[0]) if dart_rows else {}

            # 4. 기술적 및 기본적 분석 엔진 수행
            ta = TechnicalAnalysis(df_daily)
            tech_res = ta.evaluate_signals()

            fa = FundamentalAnalysis(dart_data)
            fund_res = fa.evaluate()

            # 종합 데이터 완성도 (%)
            total_completeness = min(tech_res['tech_completeness'], fund_res['data_completeness'])

            f_score = fund_res['f_score']
            t_raw = tech_res['t_raw']
            t_score = tech_res['t_score']

            # 1차, 2차, 3차 종합점수 계산 (ETF는 T점수 100%, 일반기업은 F 40% + T 60%)
            if fund_res.get('is_etf', False):
                score_stage1 = round(t_score, 2)
                score_stage2 = round(t_score, 2)
                score_stage3 = round(t_score, 2)
            else:
                score_stage1 = round(f_score * 0.4 + t_score * 0.6, 2)
                score_stage2 = round(f_score * 0.3 + t_score * 0.7, 2)
                score_stage3 = round(f_score * 0.2 + t_score * 0.8, 2)

            # 현재 보유 포지션 조회
            pos_rows = self.db.execute_query(
                "SELECT * FROM portfolio_positions WHERE stock_code = ?", (stock_code,)
            )
            current_stage = pos_rows[0]['quantity'] if pos_rows and pos_rows[0]['quantity'] > 0 else 0

            latest_close = int(df_daily.iloc[-1]['close_price'])
            prev_close = int(df_daily.iloc[-2]['close_price']) if len(df_daily) >= 2 else int(df_daily.iloc[-1]['open_price'])
            high_p = int(df_daily.iloc[-1]['high_price'])
            low_p = int(df_daily.iloc[-1]['low_price'])

            # 당일 등락률 (%) 및 고저 변동폭 (%)
            daily_change_pct = round(((latest_close - prev_close) / prev_close) * 100.0, 2) if prev_close > 0 else 0.0
            high_low_swing_pct = round(((high_p - low_p) / low_p) * 100.0, 2) if low_p > 0 else 0.0

            # 손절가 및 목표가 계산 (손절폭 약 5.2%, 익절 15%)
            stop_loss_price = int(latest_close * 0.948)
            target_profit_price = int(latest_close * 1.15)
            expected_loss_pct = round(((latest_close - stop_loss_price) / latest_close) * 100, 1)

            signal_type = "관망"
            recommended_amount = "0만 원"
            next_stage = current_stage

            # --- 포지션 판단 로직 ---
            if current_stage == 0:
                if fund_res['is_eligible_stage1'] and score_stage1 >= 70.0:
                    signal_type = "1차 신규매수"
                    recommended_amount = "400만 원"
                    next_stage = 1
            elif current_stage == 1:
                if score_stage2 >= 75.0:
                    signal_type = "2차 추가매수"
                    recommended_amount = "300만 원"
                    next_stage = 2
                elif latest_close <= stop_loss_price or t_score < 40.0:
                    signal_type = "1차 비중축소/손절"
                    next_stage = 0
            elif current_stage == 2:
                if score_stage3 >= 80.0:
                    signal_type = "3차 불타기매수"
                    recommended_amount = "300만 원"
                    next_stage = 3
                elif latest_close <= stop_loss_price or t_score < 40.0:
                    signal_type = "전량익절/손절"
                    next_stage = 0

            # DB에 분석 신호 기록 저장
            signal_db_record = {
                "stock_code": stock_code,
                "analysis_date": analysis_date,
                "f_score": f_score,
                "t_score_raw": t_raw,
                "t_score_converted": t_score,
                "score_stage1": score_stage1,
                "score_stage2": score_stage2,
                "score_stage3": score_stage3,
                "position_stage": next_stage,
                "signal_type": signal_type,
                "reason": tech_res['reason']
            }
            self.db.upsert_trading_signal(signal_db_record)

            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "analysis_date": analysis_date,
                "signal_type": signal_type,
                "recommended_amount": recommended_amount,
                "f_score": f_score,
                "growth_pts": fund_res.get('growth_pts', 0.0),
                "cf_pts": fund_res.get('cf_pts', 0.0),
                "cat_pts": fund_res.get('cat_pts', 0.0),
                "stab_pts": fund_res.get('stab_pts', 0.0),
                "val_pts": fund_res.get('val_pts', 0.0),
                "t_score_raw": t_raw,
                "t_score_converted": t_score,
                "final_score": score_stage1,
                "data_completeness": total_completeness,
                "is_etf": fund_res.get('is_etf', False),
                "f_score_confirmed": fund_res.get('f_score_confirmed', True),
                "latest_close": latest_close,
                "daily_change_pct": daily_change_pct,
                "high_low_swing_pct": high_low_swing_pct,
                "stop_loss_price": stop_loss_price,
                "expected_loss_pct": expected_loss_pct,
                "target_profit_price": target_profit_price,
                "atr_14": tech_res.get('atr_14', 0.0),
                "atr_pct": tech_res.get('atr_pct', 0.0),
                "trailing_buy_price": tech_res.get('trailing_buy_price', stop_loss_price),
                "trailing_stop_price": tech_res.get('trailing_stop_price', stop_loss_price),
                "trailing_target_price": tech_res.get('trailing_target_price', target_profit_price),
                "kiwoom_buy_tick_price": tech_res.get('kiwoom_buy_tick_price', stop_loss_price),
                "kiwoom_stop_tick_price": tech_res.get('kiwoom_stop_tick_price', stop_loss_price),
                "kiwoom_target_tick_price": tech_res.get('kiwoom_target_tick_price', target_profit_price),
                "supply_demand_pass": tech_res.get('supply_demand_pass', False),
                "reason": tech_res['reason']
            }

        except Exception as e:
            logger.error(f"[{stock_code}] 종목 분석 중 오류 발생: {e}", exc_info=True)
            return None

    def scan_all_stocks(self) -> List[Dict[str, Any]]:
        """
        DB에 등록된 전 종목을 스캔하여 매매 신호 대상 목록 반환
        """
        signals = []
        stock_rows = self.db.execute_query("SELECT stock_code FROM stock_info")
        if not stock_rows:
            logger.warning("스캔할 종목 정보가 stock_info 테이블에 없습니다.")
            return signals

        for row in stock_rows:
            code = row['stock_code']
            res = self.analyze_stock(code)
            if res and res['signal_type'] != "관망":
                signals.append(res)

        return signals
