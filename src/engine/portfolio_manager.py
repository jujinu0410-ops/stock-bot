import json
import pathlib
from typing import Dict, Any, List
import pandas as pd
from src.database.db_manager import DatabaseManager
from src.utils.logger import logger
from src.api.kiwoom_api import KiwoomAPIClient

class PortfolioManager:
    """
    사용자의 실제 키움증권 계좌 보유 종목을 관리하고
    실시간 계좌 잔고(kt00018) 동기화, 트레일링 손절가 산출,
    일봉/45분봉 복합 대응전략(1~5순위) 평가를 총괄합니다.
    """
    def __init__(self, db_manager: DatabaseManager, kiwoom_client=None):
        self.db = db_manager
        self.kiwoom = kiwoom_client if kiwoom_client else KiwoomAPIClient()

    def clear_all_holdings(self):
        """테스트 및 세션 갱신 시 기존 보유 종목 데이터 초기화"""
        logger.info("[PortfolioManager] 기존 보유 종목 데이터 초기화")
        self.db.execute_non_query("DELETE FROM portfolio_positions")

    def add_holding(self, stock_code: str, stock_name: str, quantity: int, avg_buy_price: float):
        """
        보유 종목을 portfolio_positions 및 stock_info DB 테이블에 수량 및 평단가와 함께 저장합니다.
        """
        code = str(stock_code).zfill(6)
        
        # stock_info 기본 테이블에 종목 정보 업데이트
        self.db.execute_non_query("""
            INSERT OR REPLACE INTO stock_info (stock_code, stock_name, market_type, is_active, updated_at)
            VALUES (?, ?, 'KRX', 1, CURRENT_TIMESTAMP)
        """, (code, stock_name))

        # portfolio_positions 테이블에 보유 잔고 저장 (수량, 평단가 연동)
        self.db.execute_non_query("""
            INSERT INTO portfolio_positions (stock_code, quantity, avg_buy_price, highest_close_price, confirmed_stop_price, updated_at)
            VALUES (?, ?, ?, 0.0, 0.0, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code) DO UPDATE SET
                quantity = excluded.quantity,
                avg_buy_price = excluded.avg_buy_price,
                updated_at = CURRENT_TIMESTAMP
        """, (code, quantity, float(avg_buy_price)))

        logger.info(f"[PortfolioManager] 실제 보유 종목 등록 완료: {stock_name}({code}) {quantity}주 @ {avg_buy_price:,}원")

    def sync_portfolio_from_kiwoom(self) -> List[Dict[str, Any]]:
        """
        키움 REST API (kt00018) 실시간 계좌평가 잔고 조회를 1순위로 실행하여
        실제 보유 종목(11개 등)만 DB에 최신화하고 config/portfolio_holdings.json 파일도 동기화합니다.
        """
        logger.info("[PortfolioManager] 키움 API 실시간 계좌 보유 종목 동기화 시작...")
        
        positions = self.kiwoom.get_account_positions()
        
        if positions and len(positions) > 0 and self.kiwoom.is_valid_key():
            logger.info(f"[PortfolioManager] 🔥 키움 REST API 실계좌 연동 성공! (실제 보유: {len(positions)}개 종목)")
            
            # 기존 오프라인 잔고 DB 완전 초기화 후 키움 실계좌 잔고만 등록
            self.clear_all_holdings()
            for pos in positions:
                self.add_holding(pos["stock_code"], pos["stock_name"], pos["quantity"], pos["avg_buy_price"])
            
            # config/portfolio_holdings.json 파일도 실계좌 잔고로 자동 동기화 덮어쓰기
            cfg_path = pathlib.Path("config/portfolio_holdings.json")
            try:
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(positions, f, ensure_ascii=False, indent=2)
                logger.info(f"[PortfolioManager] config/portfolio_holdings.json 파일도 실계좌 잔고({len(positions)}개)로 동기화 완료")
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
                        self.clear_all_holdings()
                        for pos in positions:
                            self.add_holding(pos["stock_code"], pos["stock_name"], pos["quantity"], pos["avg_buy_price"])
                        return positions
                except Exception as e:
                    logger.error(f"[PortfolioManager] JSON 백업 로드 실패: {e}")

        # 모든 연동 실패 시 기본 mock 반환
        mock_positions = self.kiwoom._get_mock_account_positions()
        self.clear_all_holdings()
        for pos in mock_positions:
            self.add_holding(pos["stock_code"], pos["stock_name"], pos["quantity"], pos["avg_buy_price"])
        return mock_positions

    def get_held_portfolio_status(self, engine=None) -> List[Dict[str, Any]]:
        """
        DB에 저장된 실제 보유 종목들의 현재가, 평가손익, 14일 ATR, 트레일링 손절가,
        일봉/45분봉 복합 5단계 대응전략 및 원자값 데이터를 생성합니다.
        """
        rows = self.db.execute_query("""
            SELECT p.stock_code, s.stock_name, p.quantity, p.avg_buy_price, p.highest_close_price, p.confirmed_stop_price
            FROM portfolio_positions p
            JOIN stock_info s ON p.stock_code = s.stock_code
            WHERE p.quantity > 0
        """)

        if not rows:
            logger.warning("[PortfolioManager] 계좌 내 보유 종목이 없습니다.")
            return []

        eval_list = []
        for r in rows:
            code = r["stock_code"]
            name = r["stock_name"]
            qty = int(r["quantity"])
            avg_p = float(r["avg_buy_price"])
            total_inv = qty * avg_p

            # 일봉 시세 데이터 및 기술적 지표 산출
            from src.analysis.technical_analysis import TechnicalAnalysis, adjust_krx_tick_size
            daily_df = self.db.get_daily_prices(code)

            analysis = None
            current_price = avg_p
            atr_14 = current_price * 0.03
            atr_pct = 3.0
            f_sc = 50.0
            t_sc = 50.0
            final_sc = 50.0
            completeness = 100.0
            is_etf = False
            f_confirmed = True

            if engine:
                try:
                    analysis = engine.analyze_stock(code, name)
                    current_price = float(analysis.get("current_price", avg_p) or avg_p)
                    atr_14 = float(analysis.get("atr_14", current_price * 0.03) or (current_price * 0.03))
                    atr_pct = float(analysis.get("atr_pct", 3.0) or 3.0)
                    f_sc = float(analysis.get("f_score", 50.0) or 50.0)
                    t_sc = float(analysis.get("t_score", 50.0) or 50.0)
                    final_sc = float(analysis.get("final_score", 50.0) or 50.0)
                    completeness = float(analysis.get("data_completeness", 100.0) or 100.0)
                    is_etf = bool(analysis.get("is_etf", False))
                    f_confirmed = bool(analysis.get("f_score_confirmed", True))
                except Exception as e_an:
                    logger.error(f"종목 분석 중 예외 발생 ({code}): {e_an}")

            # 만약 DB 일봉 데이터가 충실하면 TechnicalAnalysis 직접 호출하여 검증 원자값 100% 확보
            tech_eval = {}
            if not daily_df.empty and len(daily_df) >= 5:
                ta = TechnicalAnalysis(daily_df)
                tech_eval = ta.evaluate_signals()
            elif analysis:
                tech_eval = analysis

            eval_amount = qty * current_price
            pnl_amount = eval_amount - total_inv
            pnl_pct = round((pnl_amount / total_inv) * 100.0, 2) if total_inv > 0 else 0.0

            # ATR 기반 트레일링 가격 수식 산출
            raw_tbuy = current_price - (1.5 * atr_14)
            raw_tstop = current_price - (2.0 * atr_14)
            raw_ttarget = current_price + (2.5 * atr_14)

            rebound_delta = int(round(atr_14 * 0.5))
            drop_delta = int(round(atr_14 * 0.8))

            if is_etf:
                stop_dist = max(2.0 * atr_14, current_price * 0.04)
                target_stop = float(current_price - stop_dist)
            else:
                stop_dist = min(max(1.5 * atr_14, current_price * 0.06), current_price * 0.15)
                target_stop = float(current_price - stop_dist)

            r_keys = r.keys()
            prev_highest = float(r["highest_close_price"] or 0.0) if "highest_close_price" in r_keys else 0.0
            highest_close_price = max(prev_highest, float(current_price))
            prev_confirmed_stop = float(r["confirmed_stop_price"] or 0.0) if "confirmed_stop_price" in r_keys else 0.0

            confirmed_stop_price = target_stop

            if confirmed_stop_price >= current_price or confirmed_stop_price <= 0:
                confirmed_stop_price = target_stop

            if not f_confirmed:
                confirmed_stop_price = 0.0
                raw_ttarget = 0.0
                raw_tbuy = 0.0

            if prev_confirmed_stop == 0:
                stop_update_status = "🆕 신규설정"
            elif confirmed_stop_price > prev_confirmed_stop:
                stop_update_status = "⬆️ 상향갱신"
            else:
                stop_update_status = "유지"

            self.db.execute_non_query("""
                UPDATE portfolio_positions 
                SET highest_close_price = ?, confirmed_stop_price = ?, updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ?
            """, (highest_close_price, confirmed_stop_price, code))

            if not f_confirmed:
                kiwoom_stop = 0
                kiwoom_target = 0
                kiwoom_buy = 0
            else:
                kiwoom_stop_p = adjust_krx_tick_size(confirmed_stop_price, "down")
                kiwoom_buy = adjust_krx_tick_size(raw_tbuy, "down") if raw_tbuy > 0 else 0
                kiwoom_target = adjust_krx_tick_size(raw_ttarget, "up")
                kiwoom_stop = kiwoom_stop_p if kiwoom_stop_p > 0 else adjust_krx_tick_size(confirmed_stop_price, "down")

            # 45분봉 ADX, OBV, Chaikin Oscillator 수집
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

            # 🔥 [100% 전송 보장] 원자값 및 기술검증 데이터 통합 할당
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
                "eval_weight_pct": 0.0,  # 아래에서 재계산
                "pnl_amount": int(pnl_amount),
                "pnl_pct": pnl_pct,
                "highest_close_price": int(round(highest_close_price)),
                "prev_confirmed_stop": int(round(prev_confirmed_stop)),
                "confirmed_stop_price": kiwoom_stop,
                "stop_update_status": stop_update_status,
                "target_profit_price": kiwoom_target,
                "atr_14": atr_14,
                "atr_pct": atr_pct,
                "rebound_delta": rebound_delta,
                "drop_delta": drop_delta,
                "trailing_buy_price": kiwoom_buy,
                "trailing_stop_price": kiwoom_stop,
                "trailing_target_price": kiwoom_target,
                "kiwoom_buy_tick_price": kiwoom_buy,
                "kiwoom_stop_tick_price": kiwoom_stop,
                "kiwoom_target_tick_price": kiwoom_target,
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

        # 포트폴리오 총 평가금액 및 계좌비중(%) 산출
        total_portfolio_eval = sum(item["eval_amount"] for item in eval_list) or 1
        for item in eval_list:
            item["eval_weight_pct"] = round((item["eval_amount"] / total_portfolio_eval) * 100.0, 1)

        # 1. 정렬 및 순위 3원화 (확정 순위 / 잠정 순위 / ETF 순위)
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

        # 2. 🔥 [매매 대응전략 5단계 복합 판정 매트릭스 및 차등 라벨 체계]
        for item in eval_list:
            f_sc = item["f_score"]
            t_sc = item["t_score"]
            chg_pct = item["daily_change_pct"]
            weight_pct = item["eval_weight_pct"]
            is_etf = item["is_etf"]
            f_confirmed = item["f_score_confirmed"]
            completeness = item["data_completeness"]

            is_tier3_sell = item.get("is_tier3_sell", False)
            is_minus_di_dominant = item.get("is_minus_di_dominant", False)
            adx_note = " (ADX -DI우세 확인)" if is_minus_di_dominant else " (ADX 방향 불일치, 참고)"
            intraday_cho_note = item.get("intraday_cho_note", "")
            rank = item.get("rank", "순위")
            is_45m_bearish_2plus = item.get("is_45m_bearish_2plus", False)

            is_45m_breakdown = item.get("is_45m_breakdown", False)
            is_obv_dead = item.get("is_obv_dead", False)
            is_cho_outflow = item.get("is_cho_outflow", False)

            # 1순위 — DART 재무 미확정 종목 ("보류" 고정, 아래 2~5순위 무시)
            if not f_confirmed or completeness < 90.0:
                item["action_status"] = "⚠️ DART 재무 미확정 (보류)"

            # 2순위 — 계좌비중 20% 초과 종목
            elif weight_pct > 20.0:
                if is_tier3_sell:
                    item["action_status"] = f"⚠️ 비중과다({weight_pct}%) + 🚨 매도조건 충족{adx_note}{intraday_cho_note} (추매금지/보유)"
                elif is_45m_breakdown:
                    item["action_status"] = f"⚠️ 비중과다({weight_pct}%) + 🚨 45m 이중수급이탈 (추매금지/보유)"
                elif is_cho_outflow:
                    item["action_status"] = f"⚠️ 비중과다({weight_pct}%) + ⚠️ 45m CHO유출 (추매금지/보유)"
                else:
                    item["action_status"] = f"⚠️ 비중과다({weight_pct}%) 집중위험 (추매금지/보유)"

            # 3순위 — 매도 조건 (일봉 OBV 9일 데드 2일차 이상 AND 일봉 Chaikin 2봉 연속 <= 0)
            elif is_tier3_sell:
                item["action_status"] = f"🚨 매도{adx_note}{intraday_cho_note} ({rank})"

            # 4순위 — 분할매수 조건 (일봉 3순위 미충족 AND 45분봉 지표 중 2개 이상 하락신호)
            elif is_45m_bearish_2plus and t_sc >= 50.0:
                item["action_status"] = f"🎯 분할매수 ({rank} / 45m 단기조정 감지)"

            # 5순위 — 그 외 전부 (단기 45분봉 차등 경고 라벨 100% 복원 및 계속 보유)
            else:
                if is_45m_breakdown:
                    item["action_status"] = f"🚨 단기 매도 ({rank} / OBV이탈·CHO유출)"
                elif is_obv_dead:
                    item["action_status"] = f"⚠️ OBV 이탈 ({rank} / 45m OBV 데드)"
                elif is_cho_outflow:
                    if t_sc < 50.0:
                        item["action_status"] = f"🎯 차익실현 + ⚠️ CHO유출 ({rank})"
                    else:
                        item["action_status"] = f"⚠️ CHO 유출 ({rank} / 45m 자금유출)"
                elif f_sc < 50.0 or t_sc < 50.0:
                    item["action_status"] = f"⚠️ {rank} 펀더멘탈/기술약세 (반등시 분할매도)"
                else:
                    item["action_status"] = f"🟢 {rank} (계속 보유/홀딩)"

        return eval_list
