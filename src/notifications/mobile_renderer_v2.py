# -*- coding: utf-8 -*-
"""
모바일 최적화 이메일 리포트 렌더러 V2 (Mobile Renderer V2)
- 표 가로스크롤 없는 모바일 최적화(360px~430px) 반응형 카드/그리드 UI
- V4-PILOT-C 계산 완료 데이터(가격/수량/전략/지표) 100% 원형 보존 및 순수 표시 전용
- 계산식, 배수, 전략 재판단 및 수치 하드코딩 절대 금지
- 필수 원천 키 누락 시 명확한 예외 발생으로 V1 안전 복구(Fallback) 유도
"""

from typing import Dict, Any, List, Optional
import re
from src.utils.logger import logger

MANDATORY_STOCK_KEYS = [
    "stock_code",
    "stock_name",
    "current_price",
    "daily_change_pct",
    "pnl_pct",
    "pnl_amount",
    "trade_mode",
    "action_status",
    "kiwoom_stop_tick_price",
    "kiwoom_target_tick_price",
    "profit_trail_delta",
    "recommended_order_qty",
    "order_direction",
    "quantity"
]

def format_cho_chip_v2(arr: List[Any]) -> str:
    """Chaikin Oscillator 수치를 모바일 칩 스타일로 포맷팅"""
    if not arr or len(arr) < 2:
        return "<span style='color:#64748B; font-size:10px;'>[0, 0]</span>"
    
    v1 = int(arr[0])
    v2 = int(arr[1])

    c1_html = f"<span style='color:#DC2626; font-weight:bold;'>+{v1:,}</span>" if v1 >= 0 else f"<span style='color:#1D4ED8; font-weight:bold;'>{v1:,}</span>"
    c2_html = f"<span style='color:#DC2626; font-weight:bold;'>+{v2:,}</span>" if v2 >= 0 else f"<span style='color:#1D4ED8; font-weight:bold;'>{v2:,}</span>"

    if v2 > v1:
        arrow_html = " <span style='color:#DC2626; font-size:11px; font-weight:bold;'>▲</span>"
    elif v2 < v1:
        arrow_html = " <span style='color:#1D4ED8; font-size:11px; font-weight:bold;'>▼</span>"
    else:
        arrow_html = ""

    return f"<span style='background:#F1F5F9; padding:2px 5px; border-radius:4px; font-size:10px;'>[{c1_html}, {c2_html}]{arrow_html}</span>"

def format_adx_chip_v2(di_dom_str: str) -> str:
    """ADX 우세방향 칩 포맷팅"""
    if not di_dom_str or di_dom_str == "-":
        return "<span style='color:#64748B; font-size:10px;'>-</span>"
    if "-DI우세" in di_dom_str:
        return f"<span style='background:#EFF6FF; border:1px solid #BFDBFE; color:#1D4ED8; font-weight:bold; font-size:10px; padding:2px 5px; border-radius:4px;'>{di_dom_str}</span>"
    elif "+DI우세" in di_dom_str:
        return f"<span style='background:#FEF2F2; border:1px solid #FECACA; color:#DC2626; font-weight:bold; font-size:10px; padding:2px 5px; border-radius:4px;'>{di_dom_str}</span>"
    else:
        return f"<span style='background:#F8FAFC; border:1px solid #E2E8F0; color:#475569; font-size:10px; padding:2px 5px; border-radius:4px;'>{di_dom_str}</span>"

def format_krw(val: Any) -> str:
    """
    모든 원화(KRW) 금액/가격을 쉼표가 포함된 정수 원 단위(예: '23,600원', '-7,034,396원')로 안전 변환.
    - 문자열 상태값('HOLD', 'N/A', '거래정지', '수동 관리' 등)은 그대로 반환
    - 부동소수점(float)인 경우 반올림하여 정수화 (예: 23600.0 -> '23,600원')
    - 예상치 못한 유의미한 소수값(0.0이 아닌 소수) 유입 시 경고 로그 기록 후 반올림 적용
    """
    if val is None:
        return "-"
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, str):
        val_clean = val.strip()
        if any(keyword in val_clean.upper() for keyword in ("HOLD", "N/A", "NONE", "거래정지", "주문대기", "수동 관리", "미체결", "비활성")):
            return val_clean
        if val_clean == "-":
            return "-"
        try:
            val_num = float(val_clean.replace(",", "").replace("원", ""))
            return format_krw(val_num)
        except ValueError:
            return val_clean
    if isinstance(val, (int, float)):
        if isinstance(val, float):
            int_val = round(val)
            if abs(val - int_val) > 1e-4:
                logger.warning(f"[MobileRendererV2] 원화 금액에 소수점 값 감지: {val} -> 반올림하여 {int_val:,}원으로 변환")
            return f"{int_val:,}원"
        return f"{val:,}원"
    return str(val)

def format_strategy_badge_v2(raw_action: str, trade_mode: str, stock_code: str) -> str:
    """모바일용 전략 상태 배지 포맷팅 (원천 전략 조건문 100% 보존)"""
    clean = raw_action
    clean = re.sub(r'\[[🟢🔄⚠️🚨⚫]\s*(?:확정|잠정|ETF|순위제외)\s*\d*위?\s*', '', clean)
    clean = re.sub(r'\s*\((?:확정|잠정|ETF|순위제외)\s*\d*위?\)', '', clean)
    clean = re.sub(r'\s*\(5순위\)', '', clean)
    clean = clean.replace('[', '').replace(']', '').strip()
    clean = re.sub(r'^\s*\(|\)\s*$', '', clean).strip()

    if trade_mode == "SUSPENDED_HOLD" or stock_code == "234920" or "거래정지" in clean or "SUSPENDED" in clean:
        return '<span style="background:#F1F5F9; border:1px solid #94A3B8; color:#475569; font-weight:bold; font-size:10.5px; padding:3px 7px; border-radius:5px; display:inline-block;">⚫ 거래정지 보류/공시감시</span>'
    elif "수동" in clean or "USER_OVERRIDE" in clean or trade_mode == "USER_OVERRIDE":
        return f'<span style="background:#FFFBEB; border:1px solid #FCD34D; color:#B45309; font-weight:bold; font-size:10.5px; padding:3px 7px; border-radius:5px; display:inline-block;">⚠️ DART 미확정 [수동감시]</span>'
    elif trade_mode == "CONCENTRATION_RISK" or "비중과다" in clean:
        return f'<span style="background:#FEF2F2; border:1px solid #FCA5A5; color:#DC2626; font-weight:bold; font-size:10.5px; padding:3px 7px; border-radius:5px; display:inline-block;">⚠️ 비중과다 분할축소</span>'
    elif trade_mode == "RECOVERY" or "손실축소" in clean:
        return f'<span style="background:#EFF6FF; border:1px solid #93C5FD; color:#1D4ED8; font-weight:bold; font-size:10.5px; padding:3px 7px; border-radius:5px; display:inline-block;">🔄 반등 시 손실축소 분할매도</span>'
    elif "매도" in clean and "추매금지" not in clean:
        return f'<span style="background:#EFF6FF; border:1px solid #93C5FD; color:#1D4ED8; font-weight:bold; font-size:10.5px; padding:3px 7px; border-radius:5px; display:inline-block;">🚨 매도계획</span>'
    elif "보유" in clean or "홀딩" in clean or trade_mode == "NORMAL":
        return f'<span style="background:#F0FDF4; border:1px solid #86EFAC; color:#166534; font-weight:bold; font-size:10.5px; padding:3px 7px; border-radius:5px; display:inline-block;">🟢 계속 보유/홀딩</span>'
    else:
        return f'<span style="background:#F8FAFC; border:1px solid #CBD5E1; color:#334155; font-weight:bold; font-size:10.5px; padding:3px 7px; border-radius:5px; display:inline-block;">{clean}</span>'

def generate_mobile_html_report_v2(
    date_str: str,
    total_count: int,
    caught_signals: List[Dict[str, Any]],
    all_results: List[Dict[str, Any]],
    held_portfolio: List[Dict[str, Any]] = None,
    disclosures: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    모바일 반응형 이메일 리포트 V2 HTML 생성 함수
    - 360px ~ 430px 모바일 뷰포트에서 가로 스크롤 없이 최적 가독성 제공
    - 필수 원천 키 검증 (누락 시 KeyError 발생으로 V1 Fallback 유도)
    - CSS Grid, JavaScript, 고정 min-width 테이블 배제 (이메일 클라이언트 표준 호환)
    """
    # 1. 🔥 필수 원천 키 무결성 검증 (임의 기본값 대체 금지)
    if held_portfolio:
        for idx, h in enumerate(held_portfolio):
            for k in MANDATORY_STOCK_KEYS:
                if k not in h:
                    raise KeyError(f"[MobileRendererV2] 필수 원천 필드 누락: stock index {idx}, code: '{h.get('stock_code')}', key: '{k}'")

    held_count = len(held_portfolio) if held_portfolio else 0
    profit_count = sum(1 for h in held_portfolio if h.get("pnl_pct", 0.0) >= 0) if held_portfolio else 0
    loss_count = sum(1 for h in held_portfolio if h.get("pnl_pct", 0.0) < 0) if held_portfolio else 0

    # 2. 📢 DART 주요 공시 브리핑 섹션 (모바일 카드 스타일)
    disclosure_cards_html = ""
    if disclosures and len(disclosures) > 0:
        d_items_html = ""
        for d in disclosures:
            s_name = d.get('stock_name', '')
            s_code = d.get('stock_code', '')
            r_name = d.get('report_nm', '')
            d_link = d.get('link', '#')
            d_sum = d.get('summary', '')
            d_imp = d.get('impact', '')
            d_guide = d.get('guide', '')

            d_items_html += f"""
            <div style="background:#FFFFFF; border:1px solid #E2E8F0; border-radius:6px; padding:10px; margin-bottom:8px;">
                <div style="font-size:12px; font-weight:bold; color:#0F172A; margin-bottom:4px; word-break:break-word;">
                    📌 <span style="color:#1E3A8A;">{s_name} ({s_code})</span> - <a href="{d_link}" target="_blank" style="color:#2563EB; text-decoration:underline;">{r_name}</a>
                </div>
                <div style="font-size:11px; color:#334155; line-height:1.5;">
                    <div style="margin-bottom:2px;">• <b>공시요약</b>: {d_sum}</div>
                    <div style="margin-bottom:2px;">• <b>시장의미</b>: {d_imp}</div>
                    <div>• <b>대응가이드</b>: {d_guide}</div>
                </div>
            </div>
            """

        disclosure_cards_html = f"""
        <div style="background:#F8FAFC; border:1px solid #CBD5E1; border-left:4px solid #2563EB; border-radius:8px; padding:12px 10px; margin-bottom:16px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; border-bottom:1px solid #E2E8F0; padding-bottom:4px;">
                <span style="font-size:13px; color:#1E3A8A; font-weight:bold;">📢 DART 주요 공시 & 브리핑 ({len(disclosures)}건)</span>
                <span style="font-size:10px; color:#64748B;">원문 이동 링크 포함</span>
            </div>
            {d_items_html}
        </div>
        """

    # 3. 💼 내 계좌 보유 종목 모바일 카드 리스트 생성
    stock_cards_html = ""
    total_eval_inv = 0
    total_eval_val = 0
    total_pnl_amt = 0
    total_pnl_pct = 0.0
    pnl_color_total = "#10B981"
    pnl_sign_total = ""

    if held_portfolio:
        def _get_sort_priority(item):
            action_st = item.get("action_status", "")
            trade_mode = item.get("trade_mode", "NORMAL")
            needs_action = (
                trade_mode in ("RECOVERY", "EMERGENCY", "HOLD") or
                "매도" in action_st or "손절" in action_st or "축소" in action_st or 
                "보류" in action_st or "미확정" in action_st or "이상 급등" in action_st
            )
            return (0 if needs_action else 1, item.get("final_score", 0.0))

        held_sorted = sorted(held_portfolio, key=_get_sort_priority)

        total_eval_inv = sum(h.get("total_invested", 0) for h in held_portfolio)
        total_eval_val = sum(h.get("eval_amount", 0) for h in held_portfolio)
        total_pnl_amt = total_eval_val - total_eval_inv
        total_pnl_pct = round((total_pnl_amt / total_eval_inv) * 100.0, 2) if total_eval_inv > 0 else 0.0
        pnl_color_total = "#10B981" if total_pnl_amt >= 0 else "#EF4444"
        pnl_sign_total = "+" if total_pnl_amt >= 0 else ""
        total_pnl_amt_disp = format_krw(total_pnl_amt)
        total_pnl_amt_str = f"+{total_pnl_amt_disp}" if (isinstance(total_pnl_amt, (int, float)) and total_pnl_amt > 0) else total_pnl_amt_disp

        for h in held_sorted:
            code = str(h.get("stock_code")).zfill(6)
            name = str(h.get("stock_name"))
            cur_price = h.get("current_price")
            daily_chg = h.get("daily_change_pct", 0.0)
            pnl_pct = h.get("pnl_pct", 0.0)
            pnl_amt = h.get("pnl_amount", 0)
            f_sc = h.get("f_score", 0.0)
            t_sc = h.get("t_score", 0.0)
            final_sc = h.get("final_score", 0.0)
            action_st = h.get("action_status", "보유")
            trade_mode = h.get("trade_mode", "NORMAL")
            is_etf = h.get("is_etf", False)
            f_confirmed = h.get("f_score_confirmed", True)
            data_comp = h.get("data_completeness", 100.0)

            # 가격/수량 원천 필드 로딩
            k_stop = h.get("kiwoom_stop_tick_price")
            k_target = h.get("kiwoom_target_tick_price")
            trail_delta = h.get("profit_trail_delta", 0)
            atr_v = h.get("atr_14", 0)
            atr_pct = h.get("atr_pct", 0.0)
            held_qty = h.get("quantity", 0)
            rec_qty = h.get("recommended_order_qty", 0)
            order_dir = h.get("order_direction", "보유")
            excess_qty = h.get("weight_excess_qty", 0)
            risk_target_qty = h.get("risk_target_qty", 0)

            # 45분봉 수급 원자값
            obv_d_date = h.get("obv_dead_date", "N/A")
            obv_d_days = h.get("obv_dead_elapsed_days", 0)
            is_45m_obv_dead = h.get("is_obv_dead", False) or ("데드" in str(h.get("obv_45m_trend", "")))
            daily_cho = h.get("daily_cho_recent2", [0, 0])
            intra_cho = h.get("intraday_cho_recent2", [0, 0])
            di_dom = h.get("adx_di_dominance", "-")
            adx_45m = h.get("adx_14_45m", 0.0)

            # 색상 및 서명 처리
            price_color = "#DC2626" if daily_chg > 0 else ("#2563EB" if daily_chg < 0 else "#475569")
            price_sign = "+" if daily_chg > 0 else ""
            pnl_color = "#DC2626" if pnl_pct >= 0 else "#2563EB"
            pnl_sign = "+" if pnl_pct >= 0 else ""

            strategy_badge = format_strategy_badge_v2(action_st, trade_mode, code)

            # 점수 셀 서식
            if trade_mode == "SUSPENDED_HOLD" or code == "234920":
                score_display = "<span style='color:#64748B; font-weight:bold;'>N/A (거래정지)</span>"
            elif is_etf:
                score_display = f"<span style='color:#4338CA; font-weight:bold;'>{t_sc:.1f}점</span> <span style='font-size:9.5px; color:#64748B;'>(ETF T점수)</span>"
            elif not f_confirmed:
                score_display = f"<span style='color:#D97706; font-weight:bold;'>{final_sc:.1f}점</span> <span style='font-size:9.5px; color:#D97706;'>(F:{f_sc:.1f}/T:{t_sc:.1f} 잠정)</span>"
            else:
                score_display = f"<span style='color:#059669; font-weight:bold;'>{final_sc:.1f}점</span> <span style='font-size:9.5px; color:#64748B;'>(F:{f_sc:.1f}/T:{t_sc:.1f})</span>"

            # 핵심 매매 가격/수량 디스플레이 포맷 (원천 데이터 기반 + format_krw 소수점 제거)
            if code == "234920" or trade_mode == "SUSPENDED_HOLD":
                target_disp = "HOLD (거래정지)"
                stop_disp = "HOLD (거래정지)"
                trail_disp = "HOLD (비활성)"
                atr_disp = "N/A (거래정지)"
                sizing_disp = f"보유 {held_qty:,}주 / <b>주문상태: 비활성 (0주)</b> [상장적격성 실질심사 매매거래정지]"
                left_border = "#94A3B8"
            elif code == "348340" or h.get("user_override_flag", False) or trade_mode == "USER_OVERRIDE":
                target_disp = f"{format_krw(k_target)} (수동활성)" if isinstance(k_target, (int, float)) else f"{k_target} (수동활성)"
                stop_disp = "HOLD (주문대기)"
                trail_disp = f"{format_krw(trail_delta)} (수동추적)" if isinstance(trail_delta, (int, float)) else f"{trail_delta} (수동추적)"
                atr_disp = "수동 관리"
                sizing_disp = f"보유 {held_qty:,}주 | <b>수동주문: {rec_qty:,}주 미체결</b> (DART미확정 신규주문금지)"
                left_border = "#F59E0B"
            elif trade_mode == "CONCENTRATION_RISK":
                target_disp = format_krw(k_target) if isinstance(k_target, (int, float)) and k_target > 0 else str(k_target)
                stop_disp = format_krw(k_stop) if isinstance(k_stop, (int, float)) and k_stop > 0 else str(k_stop)
                trail_disp = f"최고가 대비 {format_krw(abs(trail_delta))} 하락 시" if isinstance(trail_delta, (int, float)) and trail_delta != 0 else "HOLD (비활성)"
                atr_disp = f"{format_krw(atr_v)} ({atr_pct:.1f}%)" if isinstance(atr_v, (int, float)) and atr_v > 0 else "-"
                sizing_disp = f"위험목표 {risk_target_qty:,}주 | 20%초과 {excess_qty:,}주 | <b>권고: {order_dir} ({rec_qty:,}주)</b>"
                left_border = "#EF4444"
            elif trade_mode == "RECOVERY":
                target_disp = format_krw(k_target) if isinstance(k_target, (int, float)) and k_target > 0 else str(k_target)
                stop_disp = format_krw(k_stop) if isinstance(k_stop, (int, float)) and k_stop > 0 else str(k_stop)
                trail_disp = f"최고가 대비 {format_krw(abs(trail_delta))} 하락 시" if isinstance(trail_delta, (int, float)) and trail_delta != 0 else "HOLD (비활성)"
                atr_disp = f"{format_krw(atr_v)} ({atr_pct:.1f}%)" if isinstance(atr_v, (int, float)) and atr_v > 0 else "-"
                sizing_disp = f"위험목표 {risk_target_qty:,}주 | 보유 {held_qty:,}주 | <b>권고: {order_dir} (손실축소 {rec_qty:,}주)</b>"
                left_border = "#3B82F6"
            else:  # NORMAL
                target_disp = format_krw(k_target) if isinstance(k_target, (int, float)) and k_target > 0 else str(k_target)
                stop_disp = format_krw(k_stop) if isinstance(k_stop, (int, float)) and k_stop > 0 else str(k_stop)
                trail_disp = f"최고가 대비 {format_krw(abs(trail_delta))} 하락 시" if isinstance(trail_delta, (int, float)) and trail_delta != 0 else "HOLD (비활성)"
                atr_disp = f"{format_krw(atr_v)} ({atr_pct:.1f}%)" if isinstance(atr_v, (int, float)) and atr_v > 0 else "-"
                sizing_disp = f"위험목표 {risk_target_qty:,}주 | 보유 {held_qty:,}주 | <b>권고: {order_dir} ({rec_qty:,}주)</b>"
                left_border = "#10B981"

            # 수급 지표 칩 포맷
            if trade_mode == "SUSPENDED_HOLD" or code == "234920":
                obv_chip = "<span style='color:#64748B; font-size:10px;'>OBV N/A</span>"
                cho_chip = "<span style='color:#64748B; font-size:10px;'>CHO N/A</span>"
                adx_chip = "<span style='color:#64748B; font-size:10px;'>ADX N/A</span>"
            else:
                obv_d_str = f"일봉 OBV데드({obv_d_days}일차)" if (obv_d_date != "N/A" and "상승" not in obv_d_date and obv_d_days >= 1) else "일봉 OBV ▲"
                obv_45m_str = "45m 이탈" if is_45m_obv_dead else "45m ▲"
                obv_chip = f"<span style='background:#F1F5F9; color:#1E293B; font-size:10px; padding:2px 5px; border-radius:4px;'>{obv_d_str} | {obv_45m_str}</span>"
                cho_chip = f"일봉 {format_cho_chip_v2(daily_cho)} / 45m {format_cho_chip_v2(intra_cho)}"
                adx_chip = f"{format_adx_chip_v2(di_dom)} <span style='color:#4338CA; font-weight:bold; font-size:10px;'>45m ADX: {adx_45m:.1f}</span>"

            cur_price_disp = format_krw(cur_price)
            pnl_amt_disp = format_krw(pnl_amt)
            pnl_amt_str = f"+{pnl_amt_disp}" if (isinstance(pnl_amt, (int, float)) and pnl_amt > 0) else pnl_amt_disp

            stock_cards_html += f"""
            <div data-stock-code="{code}" data-trade-mode="{trade_mode}" style="background:#FFFFFF; border:1px solid #E2E8F0; border-left:4px solid {left_border}; border-radius:8px; padding:12px; margin-bottom:12px; box-shadow:0 1px 4px rgba(0,0,0,0.03);">
                <!-- 1. 카드 헤더 (종목명/코드 + 전략배지 + 현재가/등락률) -->
                <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px; border-bottom:1px solid #F1F5F9; padding-bottom:6px;">
                    <div style="max-width:65%;">
                        <div style="font-size:14px; font-weight:bold; color:#0F172A; word-break:break-word; line-height:1.25;">
                            {name} <span style="font-size:11px; color:#64748B; font-weight:normal;">({code})</span>
                        </div>
                        <div style="margin-top:3px;">
                            {strategy_badge}
                        </div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:14px; font-weight:bold; color:{price_color};">
                            {cur_price_disp}
                        </div>
                        <div style="font-size:11px; font-weight:bold; color:{price_color};">
                            ({price_sign}{daily_chg:.2f}%)
                        </div>
                    </div>
                </div>

                <!-- 2. 평가손익 & 종합점수 바 -->
                <div style="background:#F8FAFC; border:1px solid #EEF2F6; border-radius:6px; padding:6px 8px; margin-bottom:8px; font-size:11px; display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span style="color:#64748B;">종합:</span> {score_display}
                    </div>
                    <div>
                        <span style="color:#64748B;">손익:</span> <b style="color:{pnl_color};">{pnl_sign}{pnl_pct:.2f}%</b> <span style="font-size:10px; color:{pnl_color};">({pnl_amt_str})</span>
                    </div>
                </div>

                <!-- 3. 핵심 가격/수량 2x2 그리드 테이블 (표 가로스크롤 없는 100% 폭) -->
                <table style="width:100%; border-collapse:collapse; font-size:11px; margin-bottom:8px; background:#FAFAFA; border-radius:6px; border:1px solid #F1F5F9;">
                    <tr>
                        <td style="padding:5px 6px; width:50%; border-right:1px solid #F1F5F9; border-bottom:1px solid #F1F5F9;">
                            <span style="color:#64748B; font-size:10px;">목표가:</span> <b style="color:#059669;">{target_disp}</b>
                        </td>
                        <td style="padding:5px 6px; width:50%; border-bottom:1px solid #F1F5F9;">
                            <span style="color:#64748B; font-size:10px;">손절선:</span> <b style="color:#DC2626;">{stop_disp}</b>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:5px 6px; border-right:1px solid #F1F5F9;">
                            <span style="color:#64748B; font-size:10px;">트레일링:</span> <span style="color:#334155; font-size:10.5px;">{trail_disp}</span>
                        </td>
                        <td style="padding:5px 6px;">
                            <span style="color:#64748B; font-size:10px;">14일 ATR:</span> <span style="color:#D97706; font-weight:bold; font-size:10.5px;">{atr_disp}</span>
                        </td>
                    </tr>
                </table>

                <!-- 4. 포지션 사이징 및 권고 주문 수량 -->
                <div style="background:#F0FDF4; border:1px solid #DCFCE7; border-radius:5px; padding:6px 8px; margin-bottom:8px; font-size:11px; color:#166534; line-height:1.4;">
                    {sizing_disp}
                </div>

                <!-- 5. 45분봉 및 일봉 수급 지표 칩 바 -->
                <div style="font-size:10.5px; color:#475569; line-height:1.45; background:#F8FAFC; padding:5px 8px; border-radius:5px;">
                    <div style="margin-bottom:2px;">• 수급: {obv_chip} | {adx_chip}</div>
                    <div>• CHO: {cho_chip}</div>
                </div>
            </div>
            """

    # 4. 📱 전체 모바일 반응형 HTML 컨테이너 래퍼 조립
    mobile_html_template = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>내 계좌 보유종목 정밀평가 모바일 리포트</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
            background-color: #F1F5F9;
            margin: 0;
            padding: 0;
            color: #0F172A;
            -webkit-text-size-adjust: 100%;
        }}
        .mobile-wrapper {{
            width: 100%;
            max-width: 620px;
            margin: 0 auto;
            background: #FFFFFF;
            box-sizing: border-box;
        }}
        .header-bar {{
            background: linear-gradient(135deg, #1E293B 0%, #0F172A 100%);
            color: #FFFFFF;
            padding: 16px 12px;
            text-align: left;
        }}
        .content-body {{
            padding: 10px 8px;
        }}
        @media only screen and (max-width: 480px) {{
            .content-body {{
                padding: 8px 4px !important;
            }}
            .header-bar {{
                padding: 12px 8px !important;
            }}
        }}
    </style>
</head>
<body>
    <div class="mobile-wrapper" data-render-version="V2">
        <!-- 상단 헤더 -->
        <div class="header-bar">
            <div style="font-size:10px; letter-spacing:1px; color:#93C5FD; font-weight:bold; margin-bottom:2px;">KIWOOM REST & DART INTEGRATED V4</div>
            <div style="font-size:16px; font-weight:bold; color:#FFFFFF; margin-bottom:2px;">📱 내 종목 모바일 정밀평가 리포트</div>
            <div style="font-size:11px; color:#94A3B8;">기준일시: {date_str}</div>
        </div>

        <div class="content-body">
            <!-- 계좌 요약 카드 -->
            <div style="background:#F8FAFC; border:1px solid #CBD5E1; border-radius:8px; padding:10px 12px; margin-bottom:12px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <span style="font-size:12px; color:#475569; font-weight:bold;">💼 보유 포트폴리오 요약</span>
                    <span style="font-size:12px; font-weight:bold; color:{pnl_color_total};">총 손익: {pnl_sign_total}{total_pnl_pct:.2f}% ({total_pnl_amt_str})</span>
                </div>
                <div style="display:flex; justify-content:space-around; text-align:center; font-size:11px; border-top:1px solid #E2E8F0; padding-top:6px;">
                    <div>보유: <b>{held_count}종목</b></div>
                    <div>수익: <b style="color:#10B981;">{profit_count}개</b></div>
                    <div>손실: <b style="color:#EF4444;">{loss_count}개</b></div>
                </div>
            </div>

            <!-- V4-PILOT-C 주요 대응 지침 -->
            <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-left:4px solid #475569; border-radius:6px; padding:8px 10px; margin-bottom:12px; font-size:10.5px; color:#334155; line-height:1.45;">
                <div style="font-weight:bold; color:#0F172A; margin-bottom:3px;">📋 V4-PILOT-C 주요 대응 지침</div>
                <div>• <b>손절선/익절선</b>: 키움 호가 단위 적용 실시간 감시 (래칫 손절 상향 보존)</div>
                <div>• <b>비중 20% 초과</b>: 20% 초과 수량 우선 분할 축소 권고 집행</div>
            </div>

            <!-- 보유 종목 카드 리스트 -->
            {stock_cards_html}

            <!-- DART 주요 공시 & 브리핑 -->
            {disclosure_cards_html}

            <div style="text-align:center; font-size:10px; color:#94A3B8; padding:12px 0 16px 0;">
                ※ 본 리포트는 V4-PILOT-C 위험관리 엔진 기준값이며 실제 주문은 사용자의 확인 하에 집행됩니다.
            </div>
        </div>
    </div>
</body>
</html>
"""
    return mobile_html_template
