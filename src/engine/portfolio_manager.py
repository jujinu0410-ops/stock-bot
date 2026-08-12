from typing import Dict, Any, List, Optional
from src.database.db_manager import DatabaseManager
from src.api.kiwoom_api import KiwoomAPIClient
from src.analysis.technical_analysis import adjust_krx_tick_size
from src.utils.logger import logger

class PortfolioManager:
    """
    사용자의 보유 종목 현황을 키움 API 및 DB와 연동하여 관리하고,
    보유 종목별 F점수(기본)+T점수(기술) 합산 종합점수 내림차순 정렬 및 손절/익절/비중축소 대응 전략을 수립합니다.
    """
    def __init__(self, db_manager: DatabaseManager, kiwoom_api: Optional[KiwoomAPIClient] = None):
        self.db = db_manager
        self.kiwoom = kiwoom_api or KiwoomAPIClient()

    def clear_all_holdings(self) -> bool:
        """기존 보유 종목 데이터를 초기화"""
        logger.info("[PortfolioManager] 기존 보유 종목 데이터 초기화")
        return self.db.execute_non_query("DELETE FROM portfolio_positions")

    def add_holding(self, stock_code: str, stock_name: str, quantity: int, avg_buy_price: float) -> bool:
        """사용자 실제 보유 종목 수동/직접 등록"""
        self.db.upsert_stock_info({
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market_type": "KOSPI",
            "sector": "주요업종",
            "market_cap": 0,
            "floating_shares": 0
        })

        total_inv = quantity * avg_buy_price
        stop_loss = round(avg_buy_price * 0.95)
        target_profit = round(avg_buy_price * 1.15)

        query = """
            INSERT INTO portfolio_positions (stock_code, quantity, avg_buy_price, total_invested, max_allowed_loss, stop_loss_price, target_profit_price, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code) DO UPDATE SET
                quantity=excluded.quantity,
                avg_buy_price=excluded.avg_buy_price,
                total_invested=excluded.total_invested,
                stop_loss_price=excluded.stop_loss_price,
                target_profit_price=excluded.target_profit_price,
                updated_at=CURRENT_TIMESTAMP;
        """
        success = self.db.execute_non_query(query, (stock_code, quantity, avg_buy_price, total_inv, 0, stop_loss, target_profit))
        if success:
            logger.info(f"[PortfolioManager] 실제 보유 종목 등록 완료: {stock_name}({stock_code}) {quantity}주 @ {avg_buy_price:,}원")
        return success

    def sync_portfolio_from_kiwoom(self) -> List[Dict[str, Any]]:
        """키움 API에서 계좌 보유 종목을 조회하여 DB portfolio_positions 및 stock_info 동기화"""
        logger.info("[PortfolioManager] 키움 API 계좌 보유 종목 동기화 진행 중...")
        
        # 1. 17개 사용자 실제 보유 종목 리스트 기본 동기화
        mock_positions = self.kiwoom._get_mock_account_positions()

        # portfolio_positions 테이블 내 중복/오래된 무효 종목 코드(088500, 219550, 484730 등) 완전 삭제
        valid_codes = {p["stock_code"] for p in mock_positions}
        if valid_codes:
            valid_clause = ",".join(f"'{c}'" for c in valid_codes)
            self.db.execute_non_query(f"DELETE FROM portfolio_positions WHERE stock_code NOT IN ({valid_clause})")

        for pos in mock_positions:
            self.add_holding(pos["stock_code"], pos["stock_name"], pos["quantity"], pos["avg_buy_price"])

        # 2. 키움 REST API 실시간 잔고가 있으면 체결 잔고 최신화
        positions = self.kiwoom.get_account_positions()
        if positions and len(positions) > 0:
            for pos in positions:
                code = pos["stock_code"]
                name = pos["stock_name"]
                qty = pos["quantity"]
                avg_p = pos["avg_buy_price"]
                if qty > 0:
                    self.add_holding(code, name, qty, avg_p)
            logger.info(f"[PortfolioManager] 키움 실시간 보유 종목 {len(positions)}개 동기화 반영 완료")
        
        return mock_positions

    def get_held_portfolio_status(self, trading_engine) -> List[Dict[str, Any]]:
        """
        현재 보유 중인 종목들을 F점수(기본)+T점수(기술) 합산 종합점수 내림차순(높은 순->낮은 순)으로 정렬하고,
        하위 순위 종목을 우선 비중 축소하도록 전략을 수립하여 반환
        """
        held_rows = self.db.execute_query("""
            SELECT p.*, s.stock_name
            FROM portfolio_positions p
            JOIN stock_info s ON p.stock_code = s.stock_code
            WHERE p.quantity > 0
        """)

        if not held_rows:
            logger.info("[PortfolioManager] 현재 보유 중인 종목이 없습니다.")
            return []

        eval_list = []
        for r in held_rows:
            code = r["stock_code"]
            name = r["stock_name"]
            qty = r["quantity"]
            avg_p = r["avg_buy_price"]
            total_inv = r["total_invested"]

            analysis = trading_engine.analyze_stock(code)
            current_price = analysis.get("latest_close", avg_p) if analysis else avg_p

            eval_amount = qty * current_price
            pnl_amount = eval_amount - total_inv
            pnl_pct = round(((current_price - avg_p) / avg_p) * 100.0, 2) if avg_p > 0 else 0.0

            # 14일 ATR 수치 수집
            atr_14 = analysis.get("atr_14", current_price * 0.03) if analysis else current_price * 0.03
            atr_pct = analysis.get("atr_pct", 3.0) if analysis else 3.0
            f_sc = analysis.get("f_score", 0.0) if analysis else 0.0
            t_sc = analysis.get("t_score_converted", 0.0) if analysis else 0.0
            completeness = analysis.get("data_completeness", 100.0) if analysis else 0.0
            f_confirmed = analysis.get("f_score_confirmed", True) if analysis else False
            
            # ETF/ETN/커버드콜 상품 100% 자동 판별 (3/4 시작 코드 및 키워드 연동)
            is_etf = (
                (analysis.get("is_etf", False) if analysis else False) or
                code.startswith('3') or code.startswith('4') or
                any(k in name.upper() for k in ['ETF', 'TIGER', 'RISE', 'PLUS', 'KODEX', 'ACE', 'SOL', 'KBSTAR', 'ARIRANG', 'HANARO', '커버드콜', 'SOLACTIVE'])
            )

            # ETF는 T점수 100% 적용, 일반기업은 (F*0.4 + T*0.6) 잠정/확정 종합점수 산출
            if is_etf:
                final_sc = t_sc
                f_confirmed = True
            else:
                final_sc = round((f_sc * 0.4) + (t_sc * 0.6), 1)

            # [손실 중증도별 차등 ATR 트레일링 수식 적용 (안 1 - 명확화)]
            # 대형 손실 종목(손실률 -25% 이하 OR T점수 50미만): 목표가 +1.2 ATR, 트레일링 매도폭 -0.5 ATR, 손절가 -10.0%
            # 정상/수익 종목(손실률 -25% 초과 AND T점수 50이상): 목표가 +2.5 ATR, 트레일링 매도폭 -0.8 ATR, 손절가 -2.0 ATR
            is_heavy_loss = (pnl_pct <= -25.0 or (t_sc < 50.0 and not is_etf))

            if is_heavy_loss:
                rebound_delta = int(atr_14 * 0.5)
                drop_delta = int(atr_14 * 0.5)
                raw_tbuy = current_price - (1.0 * atr_14)
                raw_ttarget = current_price + (1.2 * atr_14)
                target_stop = float(current_price * 0.90)  # 현재가 대비 -10.0% 손절선
            else:
                rebound_delta = int(atr_14 * 0.5)
                drop_delta = int(atr_14 * 0.8)
                raw_tbuy = current_price - (1.5 * atr_14)
                raw_ttarget = current_price + (2.5 * atr_14)
                target_stop = max(float(current_price - (2.0 * atr_14)), float(current_price * 0.85))

            r_keys = r.keys()
            prev_highest = float(r["highest_close_price"] or 0.0) if "highest_close_price" in r_keys else 0.0
            highest_close_price = max(prev_highest, float(current_price))
            prev_confirmed_stop = float(r["confirmed_stop_price"] or 0.0) if "confirmed_stop_price" in r_keys else 0.0

            if prev_confirmed_stop > 0 and prev_confirmed_stop < current_price:
                confirmed_stop_price = max(prev_confirmed_stop, target_stop)
            else:
                confirmed_stop_price = target_stop

            if confirmed_stop_price >= current_price or confirmed_stop_price <= 0:
                confirmed_stop_price = target_stop

            # DART 재무 미확정 종목 (뉴로메카 등)은 자동 감시 주문 오발동 방지를 위해 손절가 0원 보류 처리
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

            # DB에 최신 최고종가 및 확정 손절선 업데이트
            self.db.execute_non_query("""
                UPDATE portfolio_positions 
                SET highest_close_price = ?, confirmed_stop_price = ?, updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ?
            """, (highest_close_price, confirmed_stop_price, code))

            # KRX 호가단위 보정 손절가 및 목표가/매수가 산출
            if not f_confirmed:
                kiwoom_stop = 0
                kiwoom_target = 0
                kiwoom_buy = 0
            else:
                kiwoom_stop_p = adjust_krx_tick_size(confirmed_stop_price, "down")
                kiwoom_buy = adjust_krx_tick_size(raw_tbuy, "down") if raw_tbuy > 0 else 0
                kiwoom_target = adjust_krx_tick_size(raw_ttarget, "up")
                kiwoom_stop = kiwoom_stop_p if kiwoom_stop_p > 0 else adjust_krx_tick_size(confirmed_stop_price, "down")

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
                "eval_weight_pct": 0.0,  # 아래에서 포트폴리오 총액 대비 비중 재계산
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

        # 2. 실전 매매 대응 전략 및 6대 안전조건 평가
        for item in eval_list:
            f_sc = item["f_score"]
            t_sc = item["t_score"]
            chg_pct = item["daily_change_pct"]
            weight_pct = item["eval_weight_pct"]
            is_etf = item["is_etf"]
            f_confirmed = item["f_score_confirmed"]
            completeness = item["data_completeness"]

            # [안전 가드레일 1] 단일 종목 계좌 비중 20% 초과 집중위험 종목 ➔ 추매 절대 금지 및 우선 축소 권고
            if weight_pct > 20.0:
                if t_sc < 50.0:
                    item["action_status"] = f"⚠️ 비중과다({weight_pct}%) / 기술약세 (우선 분할축소)"
                else:
                    item["action_status"] = f"⚠️ 비중과다({weight_pct}%) 집중위험 (추매금지/보유)"

            # [안전 가드레일 2] ETF 전용 트레이딩 평가 규칙 (DART 재무 손절 오류 방지)
            elif is_etf:
                if t_sc < 50.0:
                    item["action_status"] = "🚨 ETF 기술추세 약세 (손절/비중축소 검토)"
                elif 50.0 <= t_sc < 60.0:
                    item["action_status"] = "⏸️ ETF 추세 중립 (조정 구간 관망)"
                else:
                    item["action_status"] = f"🟢 {item['rank']} (안정 보유/홀딩)"

            # [안전 가드레일 3] DART 재무 미확정 또는 Sanity Fail ➔ 추매 금지 및 잠정 지정
            elif not f_confirmed or completeness < 90.0:
                item["action_status"] = "⚠️ DART 재무 미확정 (추매금지/재수집필요)"

            # [안전 가드레일 4] 일반기업 3x3 펀더멘탈/기술 매트릭스
            elif f_sc < 50.0:
                if t_sc < 50.0:
                    item["action_status"] = "🚨 펀더멘탈/기술 동시약세 (실질 손절/비중축소)"
                else:
                    item["action_status"] = "⚠️ 펀더멘탈 약세/기술반등 (반등시 분할매도)"
            
            elif f_sc >= 65.0:
                # 6가지 엄격한 안전 조건 100% 동시 충족 (뉴스/수급 포함) 시에만 제한적 추매 고려
                # 공시/뉴스 검증이 UNCONFIRMED(FAIL)인 경우 추매 승인 절대 불가
                if chg_pct <= -3.0:
                    item["action_status"] = "⚠️ -3% 조정이나 6대조건 미충족 (추매금지/관망)"
                elif t_sc < 50.0:
                    item["action_status"] = "🎯 펀더멘탈유지/기술꺾임 (차익실현/익절)"
                elif 50.0 <= t_sc < 60.0:
                    item["action_status"] = f"🟢 {item['rank']} (안정 보유/홀딩)"
                else:
                    item["action_status"] = f"🟢 {item['rank']} (안정 보유/홀딩)"
            
            else: # 50.0 <= f_sc < 65.0 (펀더멘탈 중립 구간 50~64점)
                if t_sc < 50.0:
                    item["action_status"] = "⚠️ 펀더멘탈중립/기술약세 (추매금지/비중축소)"
                elif 50.0 <= t_sc < 60.0:
                    item["action_status"] = f"⏸️ {item['rank']} 펀더멘탈·기술 중립 (관망/신규매수금지)"
                else:
                    item["action_status"] = f"🔄 {item['rank']} 펀더멘탈중립/기술반등 (안정홀딩/상승시 축소)"

        return eval_list
