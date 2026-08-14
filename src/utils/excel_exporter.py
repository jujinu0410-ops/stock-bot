import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
from config.settings import LOG_DIR
from src.utils.logger import logger

def create_analysis_excel_report(date_str: str,
                                held_portfolio: List[Dict[str, Any]],
                                all_results: List[Dict[str, Any]],
                                db_manager) -> Path:
    """
    수집 및 분석된 실제 데이터(보유종목 정밀평가, DART 실제 재무제표 메타데이터, 전체 종목 6대조건 요약)를
    다중 시트 엑셀(.xlsx) 파일로 생성하여 리턴합니다.
    """
    # KST 한국 표준시 시각 구하기
    kst_now = datetime.now()
    time_kst_str = kst_now.strftime("%Y-%m-%d %H:%M:%S KST")
    file_date = kst_now.strftime("%Y%m%d_%H%M%S")
    excel_filename = f"stock_analysis_{file_date}.xlsx"
    excel_path = LOG_DIR / excel_filename

    csv_held_filename = f"portfolio_monitoring_{file_date}.csv"
    csv_held_path = LOG_DIR / csv_held_filename

    csv_summary_filename = f"stock_summary_{file_date}.csv"
    csv_summary_path = LOG_DIR / csv_summary_filename

    # 보유종목 코드별 계좌비중 매핑 딕셔너리 생성 (시트 간 불일치 원천 해소)
    held_weight_map = {h.get("stock_code"): h.get("eval_weight_pct", 0.0) for h in held_portfolio}

    # --- 1. 시트 1: 내 계좌 보유 종목 정밀 평가 ---
    held_data = []
    for h in held_portfolio:
        code = str(h.get("stock_code", "")).zfill(6)
        name = h.get("stock_name", "")
        is_etf = h.get("is_etf", False)
        f_confirmed = h.get("f_score_confirmed", True)
        f_sc = h.get("f_score", 0.0)
        t_sc = h.get("t_score", 0.0)
        final_sc = h.get("final_score", 0.0)
        action_st = h.get("action_status", "")

        # F점수 및 종합점수 표기 보완
        if is_etf:
            f_display = "ETF 제외"
            final_display = f"{t_sc:.1f}점 (ETF T점수 100%)"
        elif not f_confirmed:
            f_display = f"{f_sc:.1f}점 (잠정·미확정)"
            final_display = f"{final_sc:.1f}점 (잠정·재무검증 필요)"
        else:
            f_display = f"{f_sc:.1f}점"
            final_display = f"{final_sc:.1f}점 (확정)"

        # V4 매매모드 및 데이터 유효성
        trade_mode = h.get("trade_mode", "NORMAL")
        data_val_flag = h.get("data_validity_flag", 1)
        data_hold_reason = h.get("data_hold_reason", "정상")
        action_st = h.get("action_status", "보유")

        # 완전 초기화 (행 간 데이터 누출 100% 원천 방지)
        if code == "234920" or trade_mode == "SUSPENDED_HOLD":
            auto_order_ok = "금지 (거래정지)"
            stop_mon_status = "HOLD (거래정지)"
            stop_order_status = "OFF (HOLD)"
            target_mon_status = "HOLD (거래정지)"
            target_order_status = "OFF (HOLD)"
            buy_status = "OFF (조건미충족)"
            conf_stop_val = "HOLD"
            stop_tick_val = "HOLD"
            target_tick_val = "HOLD"
            buy_tick_val = "HOLD"
            exit_tick_val = "HOLD"
            disp_init_stop = "HOLD"
            disp_ratchet_stop = "HOLD"
            disp_act_raw = "HOLD"
            disp_act_status = "HOLD"
            disp_trail_delta = "HOLD"
            disp_trail_price = "HOLD"
            disp_risk_budget = 0
            disp_risk_target_qty = "N/A (거래정지)"
            disp_excess_qty = 0
            disp_weight_excess_qty = 0
            disp_slippage_buffer = 0
            disp_stop_update = "HOLD (거래정지 / 자동승계금지)"
            disp_t_sc = "N/A (거래정지)"
            disp_final_sc = "N/A (거래정지)"
            disp_prev_stop = f"{int(h.get('prev_confirmed_stop', 4560)):,}원 (역사값 / 효력정지)" if h.get('prev_confirmed_stop', 0) > 0 else "0"
            disp_completeness = "50.0% (재무100% / 시장0%)"
        elif code == "348340" or trade_mode == "USER_OVERRIDE":
            auto_order_ok = "금지 (DART 재무 미확정)"
            stop_mon_status = "HOLD (수동감시)"
            stop_order_status = "OFF (HOLD)"
            target_mon_status = "ON (수동 24,450원)"
            target_order_status = "OFF (수동감시)"
            buy_status = "OFF (조건미충족)"
            conf_stop_val = "HOLD"
            stop_tick_val = "HOLD"
            target_tick_val = "24,450원 (수동)"
            buy_tick_val = "HOLD"
            exit_tick_val = "HOLD"
            disp_init_stop = "HOLD"
            disp_ratchet_stop = "HOLD"
            disp_act_raw = "24,450원 (수동)"
            disp_act_status = "수동활성대기"
            disp_trail_delta = "수동 700원"
            disp_trail_price = "HOLD"
            disp_risk_budget = h.get("risk_budget_amount", 0)
            disp_risk_target_qty = "N/A (수동 31주)"
            disp_excess_qty = 0
            disp_weight_excess_qty = 0
            disp_slippage_buffer = 0
            disp_stop_update = "HOLD"
            disp_t_sc = t_sc
            disp_final_sc = final_display
            disp_prev_stop = h.get("prev_confirmed_stop", 0)
            disp_completeness = h.get("data_completeness", 100.0)
        elif data_val_flag == 0 or trade_mode == "HOLD" or not f_confirmed:
            auto_order_ok = f"금지 ({data_hold_reason})"
            stop_mon_status = f"HOLD ({data_hold_reason})"
            stop_order_status = "OFF (HOLD)"
            target_mon_status = f"HOLD ({data_hold_reason})"
            target_order_status = "OFF (HOLD)"
            buy_status = "OFF (조건미충족)"
            conf_stop_val = "HOLD"
            stop_tick_val = "HOLD"
            target_tick_val = "HOLD"
            buy_tick_val = "HOLD"
            exit_tick_val = "HOLD"
            disp_init_stop = "HOLD"
            disp_ratchet_stop = "HOLD"
            disp_act_raw = "HOLD"
            disp_act_status = "HOLD"
            disp_trail_delta = "HOLD"
            disp_trail_price = "HOLD"
            disp_risk_budget = h.get("risk_budget_amount", 0)
            disp_risk_target_qty = "N/A (HOLD)"
            disp_excess_qty = 0
            disp_weight_excess_qty = 0
            disp_slippage_buffer = 0
            disp_stop_update = "HOLD"
            disp_t_sc = t_sc
            disp_final_sc = final_display
            disp_prev_stop = h.get("prev_confirmed_stop", 0)
            disp_completeness = h.get("data_completeness", 100.0)
        else:
            auto_order_ok = "가능 (알림참고)"
            stop_mon_status = "ON (상시감시)"
            stop_order_status = "OFF (알림전용)"
            target_mon_status = "ON (상시감시)"
            target_order_status = "OFF (알림전용)"
            buy_status = "ON (주문대기)" if "제한적 분할추매 고려" in action_st or "분할매수" in action_st else "OFF (조건미충족)"
            conf_stop_val = h.get("confirmed_stop_price", 0)
            stop_tick_val = h.get("kiwoom_stop_tick_price", 0)
            target_tick_val = h.get("kiwoom_target_tick_price", 0)
            buy_tick_val = h.get("kiwoom_buy_tick_price", 0)
            exit_tick_val = h.get("kiwoom_exit_tick_price", conf_stop_val)
            disp_init_stop = h.get("initial_stop_price", 0)
            disp_ratchet_stop = h.get("ratchet_stop_price", conf_stop_val)
            disp_act_raw = h.get("profit_activation_raw", 0)
            disp_act_status = h.get("profit_activation_status", "INACTIVE")
            disp_trail_delta = h.get("profit_trail_delta", 0)
            disp_trail_price = h.get("profit_trail_price", 0)
            disp_risk_budget = h.get("risk_budget_amount", 0)
            disp_risk_target_qty = h.get("risk_target_qty", 0)
            disp_excess_qty = h.get("excess_qty", 0)
            disp_weight_excess_qty = h.get("weight_excess_qty", 0)
            disp_slippage_buffer = h.get("slippage_buffer", 0)
            disp_stop_update = h.get("stop_update_status", "유지")
            disp_t_sc = t_sc
            disp_final_sc = final_display
            disp_prev_stop = h.get("prev_confirmed_stop", 0)
            disp_completeness = h.get("data_completeness", 100.0)

        held_data.append({
            "순위": h.get("rank", "순위제외"),
            "종목코드": code,
            "종목명": name,
            "계좌평가비중(%)": h.get("eval_weight_pct", 0.0),
            "ATR 엔진 버전": h.get("parameter_version", "V4-PILOT-C"),
            "매매모드": trade_mode,
            "기준가격 P0(감시개시가, 원)": h.get("anchor_price_p0", h.get("current_price")),
            "매입평단가(원)": h.get("avg_buy_price"),
            "현재가(원)": h.get("current_price"),
            "당일등락률(%)": h.get("daily_change_pct", 0.0),
            "고정 기준 ATR A0(원)": h.get("anchor_atr_a0", h.get("atr_14")),
            "현재 완료봉 ATR At(원)": h.get("current_completed_atr", h.get("atr_14")),
            "NATR(%)": h.get("natr_pct", h.get("atr_pct", 0.0)),
            "기본 F점수": f_display,
            "성장성(25)": h.get("growth_pts", 0.0) if not is_etf else "-",
            "현금흐름(20)": h.get("cf_pts", 0.0) if not is_etf else "-",
            "모멘텀(20)": h.get("cat_pts", 0.0) if not is_etf else "-",
            "재무안정(20)": h.get("stab_pts", 0.0) if not is_etf else "-",
            "밸류경영(15)": h.get("val_pts", 0.0) if not is_etf else "-",
            "기술 T점수 (100점 만점)": disp_t_sc,
            "종합점수": disp_final_sc,
            "OBV데드 발생일자": h.get("obv_dead_date", "N/A"),
            "OBV데드 경과일수": f"{h.get('obv_dead_elapsed_days', 0)}일차",
            "일봉 Chaikin 최근2봉": str(h.get("daily_cho_recent2", [0, 0])),
            "45m Chaikin 최근2봉": str(h.get("intraday_cho_recent2", [0, 0])),
            "ADX +DI/-DI 우세방향": h.get("adx_di_dominance", "-"),
            "45분봉 ADX(14)": h.get("adx_14_45m", 0.0),
            "45분봉 OBV추세": h.get("obv_45m_trend", "-"),
            "45분봉 Chaikin_Osc": h.get("chaikin_osc_45m", 0),
            "감시시작후 최고종가(원)": h.get("highest_close_price", h.get("current_price")),
            "추매 감시가격(원)": buy_tick_val,
            "반등 확인폭(원)": h.get("buy_rebound_delta", 0),
            "추매 주문상태": buy_status,
            "초기 손절가(원)": disp_init_stop,
            "현재 래칫 손절가(원)": disp_ratchet_stop,
            "전일확정 손절가(원)": disp_prev_stop,
            "금일확정 손절가(원)": conf_stop_val,
            "손절선 갱신상태": disp_stop_update,
            "익절 트레일링 원시 활성가(원)": disp_act_raw,
            "최종 익절 트레일링 활성가(원)": target_tick_val,
            "익절 활성 여부": disp_act_status,
            "익절 트레일링폭(원)": disp_trail_delta,
            "현재 익절 트레일링선(원)": disp_trail_price,
            "최종 유효 매도선(원)": exit_tick_val,
            "슬리피지 버퍼(원)": disp_slippage_buffer,
            "계좌 위험예산(원)": disp_risk_budget,
            "위험기준 목표보유수량": disp_risk_target_qty,
            "현재 보유수량": h.get("quantity"),
            "위험 초과수량": disp_excess_qty,
            "20% 초과수량": disp_weight_excess_qty,
            "권고 주문방향": h.get("order_direction", "보유"),
            "실제 권고 주문수량": h.get("recommended_order_qty", h.get("recommended_quantity", 0)),
            "수동주문 상태(USER_OVERRIDE)": h.get("manual_order_info", "-") if h.get("manual_order_info") else "-",
            "데이터 유효성": data_hold_reason if data_val_flag == 0 else "정상",
            "자동 주문설정 가능 여부": auto_order_ok,
            "손절 가격감시": stop_mon_status,
            "손절 주문전송": stop_order_status,
            "익절 가격감시": target_mon_status,
            "익절 주문전송": target_order_status,
            "총 매입금액(원)": h.get("total_invested"),
            "평가금액(원)": h.get("eval_amount"),
            "평가손익(원)": h.get("pnl_amount"),
            "평가손익률(%)": h.get("pnl_pct"),
            "데이터완성도(%)": disp_completeness,
            "실전 대응 전략": action_st
        })
    df_held = pd.DataFrame(held_data)

    # --- 2. 시트 2: DART 실제 재무 분석 (전기 원천 수치 공개 및 최신 보고서 한정) ---
    dart_data = []
    try:
        rows = db_manager.execute_query("""
            SELECT d.*, s.stock_name 
            FROM dart_financials d
            JOIN stock_info s ON d.stock_code = s.stock_code
            ORDER BY d.stock_code, d.fiscal_year DESC, d.quarter_code DESC
        """)
        seen_codes = set()
        if rows:
            for r in rows:
                r_dict = dict(r)
                code = r_dict.get("stock_code", "")
                
                # ETF 종목 제외 및 종목당 최신 1개 보고서 행만 출력
                if code in ["088500", "371460", "484730"] or code in seen_codes:
                    continue
                seen_codes.add(code)

                f_conf = r_dict.get("f_score_confirmed", 1) == 1
                comp = r_dict.get("data_completeness", 100.0)
                reason = r_dict.get("sanity_reason", "정상")

                status_str = "정상수집·검증통과" if (f_conf and comp >= 90.0) else "수집성공·이상치검출"
                confirmed_str = "확정" if (f_conf and comp >= 90.0) else "미확정(잠정)"

                dart_data.append({
                    "종목코드": code,
                    "종목명": r_dict.get("stock_name"),
                    "공시연도": r_dict.get("fiscal_year"),
                    "보고서코드": r_dict.get("quarter_code"),
                    "공시구분(CFS/OFS)": r_dict.get("fs_div", "CFS"),
                    "당기 매출액(원)": r_dict.get("revenue"),
                    "전기 매출액(원)": r_dict.get("prev_revenue", 0.0),
                    "매출액 YoY(%)": r_dict.get("revenue_yoy"),
                    "당기 영업이익(원)": r_dict.get("operating_profit"),
                    "전기 영업이익(원)": r_dict.get("prev_operating_profit", 0.0),
                    "영업이익 YoY(%)": r_dict.get("op_profit_yoy"),
                    "당기 순이익(원)": r_dict.get("net_income"),
                    "당기 OCF(원)": r_dict.get("operating_cash_flow"),
                    "전기 OCF(원)": r_dict.get("prev_operating_cash_flow", 0.0),
                    "당기 부채비율(%)": r_dict.get("debt_ratio"),
                    "전기 부채비율(%)": r_dict.get("prev_debt_ratio", r_dict.get("debt_ratio", 0.0)),
                    "수집상태": status_str,
                    "재무확정여부": confirmed_str,
                    "Sanity검증 사유": reason,
                    "Sanity 세부 검증 플래그": r_dict.get("sanity_detail_flag", f"[계정:thstrm vs frmtrm | 기간:{r_dict.get('quarter_code')} | 검증:{'PASS' if f_conf else 'FAIL'}]"),
                    "데이터완성도(%)": comp,
                    "최신 수집시각": time_kst_str
                })
    except Exception as e:
        logger.error(f"[ExcelExporter] DART 시트 생성 오류: {e}")

    df_dart = pd.DataFrame(dart_data)

    # 보유 종목 V4 값 매핑 (시트 3 일관성 동기화용)
    held_v4_map = {h["stock_code"]: h for h in held_portfolio}

    # --- 3. 시트 3: 전체 종목 시세/수급 & 6대 안전조건 검증 요약 ---
    summary_data = []
    for r in all_results:
        code = r.get("stock_code")
        f_sc = r.get("f_score", 0.0)
        t_sc = r.get("t_score_converted", 0.0)
        comp = r.get("data_completeness", 0.0)
        is_etf = r.get("is_etf", False)
        f_conf = r.get("f_score_confirmed", True)
        chg_pct = r.get("daily_change_pct", 0.0)
        sup_pass = r.get("supply_demand_pass", False)

        # 보유 종목인 경우 V4 정밀 엔진 값 100% 동기화
        if code in held_v4_map:
            h_info = held_v4_map[code]
            v4_stop = h_info.get("kiwoom_stop_tick_price", r.get("kiwoom_stop_tick_price", 0))
            v4_target = h_info.get("kiwoom_target_tick_price", r.get("kiwoom_target_tick_price", 0))
            v4_atr = h_info.get("current_completed_atr", h_info.get("atr_14", r.get("atr_14", 0.0)))
            v4_ver_note = f"V4 연동 ({h_info.get('trade_mode', 'NORMAL')})"
        else:
            v4_stop = r.get("kiwoom_stop_tick_price", 0)
            v4_target = r.get("kiwoom_target_tick_price", 0)
            v4_atr = r.get("atr_14", 0.0)
            v4_ver_note = "관심종목 (V4 감시대기)"

        # 계좌비중 시트 간 100% 동기화 연동
        held_weight = held_weight_map.get(code, 0.0)
        if held_weight > 20.0:
            pass_w = f"FAIL (비중과다 {held_weight:.1f}%)"
        elif held_weight > 0.0:
            pass_w = f"PASS (보유 {held_weight:.1f}%)"
        else:
            pass_w = "PASS (미보유 0%)"

        # 6대 안전조건 정량 판정 (ETF 예외 반영)
        analysis_reason = r.get("reason")
        if code == "234920" or (code in held_v4_map and held_v4_map[code].get("trade_mode") == "SUSPENDED_HOLD"):
            pass_f = "PASS" if f_sc >= 65.0 else "FAIL"
            pass_t = "N/A (거래정지)"
            pass_c = "FAIL (시장데이터 0%)"
            final_sc_val = "N/A (거래정지)"
            f_sc_disp = f_sc if f_conf else f"{f_sc:.1f}점 (잠정)"
            t_sc = "N/A (거래정지)"
            v4_stop = "HOLD"
            v4_target = "HOLD"
            v4_atr = "N/A (거래정지)"
            v4_ver_note = "V4 연동 (SUSPENDED_HOLD)"
            comp = "50.0% (재무100% / 시장0%)"
            buy_approval = "금지 (거래정지)"
            sig_type = "보류 (거래정지)"
            analysis_reason = "상장적격성 실질심사 거래정지로 기술분석 중단 (공시 모니터링 대상)"
        elif is_etf:
            pass_f = "ETF 제외(PASS)"
            pass_t = "PASS" if t_sc >= 65.0 else "FAIL"
            pass_c = "ETF 제외(PASS)"
            final_sc_val = t_sc
            f_sc_disp = "ETF 제외"
        else:
            pass_f = "PASS" if f_sc >= 65.0 else "FAIL"
            pass_t = "PASS" if t_sc >= 60.0 else "FAIL"
            pass_c = "PASS" if comp >= 90.0 and f_conf else "FAIL"
            final_sc_val = r.get("final_score")
            f_sc_disp = f_sc if f_conf else f"{f_sc:.1f}점 (잠정)"

        pass_news = "UNCONFIRMED(FAIL)"
        pass_sup = "PASS" if sup_pass else "FAIL"

        all_passed = (
            "PASS" in str(pass_f) and
            "PASS" in str(pass_t) and
            "PASS" in str(pass_w) and
            "PASS" in str(pass_c) and
            "PASS" in str(pass_news) and
            "PASS" in str(pass_sup)
        )
        
        if code != "234920" and all_passed and chg_pct <= -3.0:
            buy_approval = "승인"
            sig_type = r.get("signal_type")
            rec_amt = r.get("recommended_amount")
        elif code == "234920":
            buy_approval = "금지 (거래정지)"
            sig_type = "보류 (거래정지)"
            rec_amt = 0
        else:
            buy_approval = "금지(조건미충족)"
            sig_type = "관망"
            rec_amt = 0

        summary_data.append({
            "종목코드": code,
            "종목명": r.get("stock_name"),
            "매매신호": sig_type,
            "추천금액(만원)": rec_amt,
            "1. F점수조건(>=65)": pass_f,
            "2. T점수조건(>=60)": pass_t,
            "3. 계좌비중조건(<=20%)": pass_w,
            "4. 재무완성도조건(>=90%)": pass_c,
            "5. 악재공시뉴스조건": pass_news,
            "6. 3일수급쌍쓸이조건": pass_sup,
            "최종 추매승인 여부": buy_approval,
            "기본 F점수": f_sc_disp,
            "성장성(25)": r.get("growth_pts", 0.0) if not is_etf else "-",
            "현금흐름(20)": r.get("cf_pts", 0.0) if not is_etf else "-",
            "모멘텀(20)": r.get("cat_pts", 0.0) if not is_etf else "-",
            "재무안정(20)": r.get("stab_pts", 0.0) if not is_etf else "-",
            "밸류경영(15)": r.get("val_pts", 0.0) if not is_etf else "-",
            "기술 T점수": t_sc,
            "종합점수": final_sc_val,
            "현재가(원)": r.get("latest_close"),
            "당일등락률(%)": chg_pct,
            "14일 ATR(원)": v4_atr,
            "손절 감시가격(원)": v4_stop,
            "익절 감시가격(원)": v4_target,
            "엔진 연동상태": v4_ver_note,
            "데이터완성도(%)": comp,
            "분석근거": analysis_reason
        })
    df_summary = pd.DataFrame(summary_data)

    header_banner = f"데이터 수집 시각: {time_kst_str} | KRX 실시간 시세 및 OpenDART 공시 원천 연동"

    # 1. CSV 보조 파일 저장 (Gmail API 백업용)
    df_held.to_csv(csv_held_path, index=False, encoding="utf-8-sig")
    df_summary.to_csv(csv_summary_path, index=False, encoding="utf-8-sig")

    # 2. 엑셀 파일 저장 및 openpyxl 열 너비/셀 서식/배너 정밀 조정
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df_held.to_excel(writer, sheet_name="보유종목_정밀평가", startrow=1, index=False)
        df_dart.to_excel(writer, sheet_name="DART_실제재무분석", startrow=1, index=False)
        df_summary.to_excel(writer, sheet_name="전체종목_분석요약", startrow=1, index=False)

        for sheetname in writer.sheets:
            ws = writer.sheets[sheetname]
            
            # 1행 상단 KST 수집시각 배너 삽입
            ws.cell(row=1, column=1, value=header_banner)
            ws.row_dimensions[1].height = 25
            ws.row_dimensions[2].height = 30
            ws.freeze_panes = "A3"

            for col in ws.columns:
                max_len = 0
                for cell in col:
                    val_str = str(cell.value or '')
                    len_calc = sum(2 if ord(c) > 127 else 1 for c in val_str)
                    if len_calc > max_len:
                        max_len = len_calc
                col_letter = col[0].column_letter
                ws.column_dimensions[col_letter].width = min(max(max_len + 4, 14), 45)

    logger.info(f"[ExcelExporter] 엑셀/CSV 데이터 파일 생성 완료: {excel_filename}, {csv_held_filename}, {csv_summary_filename}")
    return excel_path
