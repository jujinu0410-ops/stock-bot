# -*- coding: utf-8 -*-
"""
모바일 최적화 이메일 리포트 렌더러 V2 (Mobile Renderer V2)
- 표 가로스크롤 없는 모바일 최적화(360px~430px) 반응형 카드/그리드 UI
- V4-PILOT-C 계산 완료 데이터(가격/수량/전략/지표) 100% 원형 보존 및 순수 표시 전용
- 계산식, 배수, 전략 재판단 및 수치 하드코딩 절대 금지
- 필수 원천 키 누락 시 명확한 예외 발생으로 V1 안전 복구(Fallback) 유도
"""

import html
import os
import sys
import sqlite3
from typing import Dict, Any, List, Optional
import re
from src.utils.logger import logger
from src.analysis.marketcap_leadership_radar import build_marketcap_radar_section_html

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

def sanitize_url(url: Optional[str]) -> str:
    """공시 링크 URL 검증: http:// 또는 https:// 로 시작하는 경우만 허용하고 그 외는 '#' 반환"""
    if not url:
        return "#"
    url_str = str(url).strip()
    if url_str.startswith("http://") or url_str.startswith("https://"):
        return html.escape(url_str, quote=True)
    return "#"

def is_meaningful_action_item(item: Dict[str, Any]) -> bool:
    """
    [Section 5 V4-PILOT-C 단일 공통 대응 필요 여부 판정 함수]
    보유 종목(held_status) 중 당일 실제 대응 또는 유의미한 변동이 발생한 종목을 선정:
    1. 매매 상태: '매도', '손절', '축소', '보류', '미확정', '이상 급등', 'USER_OVERRIDE', '수동'
    2. 특별 위험 모드: 'RECOVERY', 'EMERGENCY', 'HOLD', 'CONCENTRATION_RISK', 'USER_OVERRIDE', 'SUSPENDED_HOLD'
    3. 거래정지 / 감시 플래그: 종목코드 234920(자이글), is_suspended=True, user_override_flag=True
    4. 권고 주문 수량 발생: recommended_order_qty > 0
    5. 일일 유의미한 변동: abs(daily_change_pct) >= max(2.5, atr_pct * 0.8)
    """
    if not item or not isinstance(item, dict):
        return False

    action_st = str(item.get("action_status", ""))
    trade_mode = str(item.get("trade_mode", "NORMAL")).upper()
    code = str(item.get("stock_code", "")).strip().zfill(6)
    sp_action = str(item.get("smartphone_action", ""))
    stop_update_status = str(item.get("stop_update_status", ""))
    prev_stop = item.get("previous_confirmed_stop") if item.get("previous_confirmed_stop") is not None else item.get("prev_confirmed_stop")
    cur_stop = item.get("confirmed_stop_price") if item.get("confirmed_stop_price") is not None else item.get("kiwoom_stop_tick_price")

    # 0. 손절선 침범 및 스마트폰 조치 필요
    if bool(item.get("is_stop_breached")) or ("침범" in action_st) or ("침범" in sp_action):
        return True
    if any(k in sp_action for k in ("상향", "신규", "수동", "손절선")):
        return True
    if any(k in stop_update_status for k in ("상향", "신규", "수동")):
        return True
    if isinstance(prev_stop, (int, float)) and isinstance(cur_stop, (int, float)) and cur_stop > prev_stop and prev_stop > 0:
        return True

    # 1. 매매 상태 키워드
    if any(k in action_st for k in ("매도", "손절", "축소", "보류", "미확정", "이상 급등", "USER_OVERRIDE", "수동")):
        return True

    # 2. 특별 위험 모드
    if trade_mode in ("RECOVERY", "EMERGENCY", "HOLD", "CONCENTRATION_RISK", "USER_OVERRIDE", "SUSPENDED_HOLD"):
        return True

    # 3. 거래정지 / 감시 플래그
    if code == "234920" or bool(item.get("is_suspended")) or bool(item.get("user_override_flag")):
        return True

    # 4. 권고 주문 수량
    rec_qty = item.get("recommended_order_qty", 0)
    if isinstance(rec_qty, (int, float)) and rec_qty > 0:
        return True

    # 5. 동적 변동성 임계치: abs(daily_change_pct) >= max(2.5, atr_pct * 0.8)
    daily_chg = item.get("daily_change_pct", 0.0)
    atr_pct = item.get("atr_pct", 0.0)
    try:
        daily_chg_val = abs(float(daily_chg))
        atr_pct_val = float(atr_pct) if atr_pct is not None else 0.0
        threshold = max(2.5, atr_pct_val * 0.8)
        if daily_chg_val >= threshold:
            return True
    except (ValueError, TypeError):
        pass

    return False

# 호환성을 위한 별칭
is_action_needed_stock = is_meaningful_action_item

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

def format_display_val(val: Any, default: str = "DATA_GAP") -> str:
    """None, null, NULL, 빈 문자열 등을 안전하게 사용자 친화적 텍스트로 변환"""
    if val is None:
        return default
    s = str(val).strip()
    if s.upper() in ("NONE", "NULL", ""):
        return default
    return s

def parse_date_str_for_freshness(date_str: Optional[str]) -> tuple:
    """
    date_str로부터 KST 거래일(YYYY-MM-DD) 및 상한 bar_timestamp(YYYY-MM-DD HH:MM:SS)를 파싱합니다.
    """
    if not date_str or not isinstance(date_str, str):
        from datetime import datetime
        today_str = datetime.now().strftime("%Y-%m-%d")
        return today_str, None
    clean = date_str.strip()
    m_date = re.match(r"^(\d{4}-\d{2}-\d{2})", clean)
    if not m_date:
        from datetime import datetime
        today_str = datetime.now().strftime("%Y-%m-%d")
        return today_str, None
    target_date = m_date.group(1)
    m_time = re.search(r"(\d{2}:\d{2}(?::\d{2})?)", clean)
    if m_time:
        time_part = m_time.group(1)
        if len(time_part) == 5:
            time_part = f"{time_part}:00"
        max_bar_ts = f"{target_date} {time_part}"
        return target_date, max_bar_ts
    return target_date, None

def get_latest_add_advisory_entry(
    stock_code: str,
    target_date: Optional[str] = None,
    max_bar_timestamp: Optional[str] = None,
    db_manager: Any = None
) -> Dict[str, Any]:
    """
    KST 당일(trading_date) 최신 완성 45분봉 add_advisory_45m 레코드 조회 (Freshness Guard)
    - 당일 첫 45분 스캔 전 또는 당일 데이터 부재 시 전일 값을 재사용하지 않고 WAITING_TODAY_BAR 반환
    """
    if target_date is None:
        target_date, max_bar_timestamp = parse_date_str_for_freshness(None)

    if db_manager is not None:
        try:
            entry = db_manager.get_latest_add_advisory_for_stock(
                stock_code,
                trading_date=target_date,
                max_bar_timestamp=max_bar_timestamp
            )
            if entry and isinstance(entry, dict):
                if str(entry.get("data_quality", "")).startswith("VALID"):
                    return entry
                return {
                    "stock_code": stock_code,
                    "trading_date": target_date,
                    "add_advisory_state": "UNKNOWN",
                    "data_quality": "INVALID",
                    "reason_codes": "INVALID_DATA_QUALITY",
                    "evidence_text": "데이터 결측"
                }
            return {
                "stock_code": stock_code,
                "trading_date": target_date,
                "add_advisory_state": "UNKNOWN",
                "data_quality": "WAITING_TODAY_BAR",
                "reason_codes": "WAITING_TODAY_45M_BAR",
                "evidence_text": "당일 45분봉 대기"
            }
        except Exception as e:
            logger.warning(f"[MobileRendererV2] add_advisory DB조회 실패 ({stock_code}): {e}")
            return {
                "stock_code": stock_code,
                "trading_date": target_date,
                "add_advisory_state": "UNKNOWN",
                "data_quality": "WAITING_TODAY_BAR",
                "reason_codes": "WAITING_TODAY_45M_BAR",
                "evidence_text": "당일 45분봉 대기"
            }

    if os.environ.get("STOCKBOT_TEST_MODE") == "1":
        return {
            "stock_code": stock_code,
            "trading_date": target_date,
            "add_advisory_state": "UNKNOWN",
            "data_quality": "WAITING_TODAY_BAR",
            "reason_codes": "WAITING_TODAY_45M_BAR",
            "evidence_text": "당일 45분봉 대기"
        }

    try:
        curr_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() or hasattr(sys.modules[__name__], "__file__") else os.getcwd()
        state_dir = os.environ.get("STOCKBOT_STATE_DIR")
        db_paths = ([] if not state_dir else [os.path.join(state_dir, "data", "stock_system.db")]) + [
            os.path.abspath("data/stock_system.db"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_system.db") if "__file__" in globals() else "data/stock_system.db",
            os.path.join(os.getcwd(), "data", "stock_system.db")
        ]
        for dp in db_paths:
            if os.path.exists(dp):
                conn = sqlite3.connect(dp)
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                if target_date and max_bar_timestamp:
                    query = "SELECT * FROM add_advisory_45m WHERE stock_code = ? AND trading_date = ? AND bar_timestamp <= ? ORDER BY bar_timestamp DESC, id DESC LIMIT 1"
                    row = c.execute(query, (stock_code, target_date, max_bar_timestamp)).fetchone()
                elif target_date:
                    query = "SELECT * FROM add_advisory_45m WHERE stock_code = ? AND trading_date = ? ORDER BY bar_timestamp DESC, id DESC LIMIT 1"
                    row = c.execute(query, (stock_code, target_date)).fetchone()
                else:
                    query = "SELECT * FROM add_advisory_45m WHERE stock_code = ? ORDER BY bar_timestamp DESC, id DESC LIMIT 1"
                    row = c.execute(query, (stock_code,)).fetchone()
                conn.close()
                if row:
                    if str(row["data_quality"]).startswith("VALID"):
                        return dict(row)
                    return {
                        "stock_code": stock_code,
                        "trading_date": target_date,
                        "add_advisory_state": "UNKNOWN",
                        "data_quality": "INVALID",
                        "reason_codes": "INVALID_DATA_QUALITY",
                        "evidence_text": "데이터 결측"
                    }
    except Exception as e:
        logger.warning(f"[MobileRendererV2] sqlite3 add_advisory DB조회 실패 ({stock_code}): {e}")

    return {
        "stock_code": stock_code,
        "trading_date": target_date,
        "add_advisory_state": "UNKNOWN",
        "data_quality": "WAITING_TODAY_BAR",
        "reason_codes": "WAITING_TODAY_45M_BAR",
        "evidence_text": "당일 45분봉 대기"
    }

def format_add_advisory_badge_and_action(state: str) -> tuple:
    """
    45분 ADD 상태 및 행동 문구 포맷팅:
    - ADD_STRONG -> STRONG / 추가매수 검토
    - ADD_WATCH -> WATCH / 관찰
    - NEUTRAL -> NEUTRAL / 추가매수 보류
    - ADD_BLOCKED -> BLOCKED / 추가매수 금지
    - UNKNOWN -> UNKNOWN / 데이터 확인
    """
    st_upper = str(state or "UNKNOWN").upper()
    if st_upper == "ADD_STRONG":
        badge = "<span style='background:#FEF2F2; border:1px solid #FECACA; color:#DC2626; font-weight:bold; padding:2px 6px; border-radius:4px; font-size:10.5px;'>STRONG</span>"
        action = "<b style='color:#DC2626;'>추가매수 검토</b>"
    elif st_upper == "ADD_WATCH":
        badge = "<span style='background:#EFF6FF; border:1px solid #BFDBFE; color:#1D4ED8; font-weight:bold; padding:2px 6px; border-radius:4px; font-size:10.5px;'>WATCH</span>"
        action = "<span style='color:#1D4ED8; font-weight:bold;'>관찰</span>"
    elif st_upper == "NEUTRAL":
        badge = "<span style='background:#F8FAFC; border:1px solid #E2E8F0; color:#475569; font-weight:bold; padding:2px 6px; border-radius:4px; font-size:10.5px;'>NEUTRAL</span>"
        action = "<span style='color:#475569;'>추가매수 보류</span>"
    elif st_upper == "ADD_BLOCKED":
        badge = "<span style='background:#FFFBEB; border:1px solid #FDE68A; color:#B45309; font-weight:bold; padding:2px 6px; border-radius:4px; font-size:10.5px;'>BLOCKED</span>"
        action = "<span style='color:#B45309; font-weight:bold;'>추가매수 금지</span>"
    else:
        badge = "<span style='background:#F1F5F9; border:1px solid #E2E8F0; color:#64748B; font-weight:bold; padding:2px 6px; border-radius:4px; font-size:10.5px;'>UNKNOWN</span>"
        action = "<span style='color:#64748B;'>데이터 확인</span>"
    return badge, action

def format_advisory_evidence_korean(entry: Optional[Dict[str, Any]]) -> str:
    """핵심근거 최대 2~3개 한국어 간결 문구 생성 (Freshness Guard 지원)"""
    if not entry:
        return "데이터 결측"
    
    if entry.get("evidence_text"):
        return entry["evidence_text"]
    
    data_quality = str(entry.get("data_quality", ""))
    if data_quality == "WAITING_TODAY_BAR" or entry.get("reason_codes") == "WAITING_TODAY_45M_BAR":
        return "당일 45분봉 대기"
    
    if not data_quality.startswith("VALID") or entry.get("add_advisory_state") == "UNKNOWN":
        return "데이터 결측"
    
    vwap_st = entry.get("vwap_state")
    obv_st = entry.get("obv_state")
    gap_st = entry.get("obv_gap_state")
    cho_st = entry.get("chaikin_state")
    tech_ref = entry.get("technical_state_reference")

    items = []

    # 1. VWAP
    if vwap_st in ("VWAP_GOLD", "VWAP_GOLD_CROSS"):
        items.append("VWAP 골드")
    elif vwap_st in ("VWAP_DEAD", "VWAP_DEAD_CROSS"):
        items.append("VWAP 데드")

    # 2. OBV / Gap
    if obv_st in ("OBV_GOLD", "OBV_GOLD_CROSS"):
        if gap_st == "CONTRACTING":
            items.append("OBV 이격축소")
        else:
            items.append("OBV 골드")
    elif obv_st in ("OBV_DEAD", "OBV_DEAD_CROSS"):
        items.append("OBV 약화")

    # 3. Chaikin
    if cho_st == "CHAIKIN_RISING":
        items.append("Chaikin↑")
    elif cho_st == "CHAIKIN_FALLING":
        items.append("Chaikin↓")

    # 4. TechRef Damaged
    if tech_ref == "DAMAGED" and "VWAP 데드" not in items:
        items.append("수급약화")

    if not items:
        return "수급 관망"

    return " · ".join(items[:3])

def build_45m_add_advisory_table_section_html(
    held_portfolio: Optional[List[Dict[str, Any]]],
    target_date: Optional[str] = None,
    max_bar_timestamp: Optional[str] = None,
    db_manager: Any = None
) -> str:
    """모바일 최적화 45M ADD ADVISORY 4컬럼 요약 테이블 생성 HTML (Freshness Guard 적용)"""
    if not held_portfolio:
        return ""
    
    rows_html = ""
    for h in held_portfolio:
        code = str(h.get("stock_code", "")).strip().zfill(6)
        name = h.get("stock_name") or code
        
        entry = get_latest_add_advisory_entry(
            code,
            target_date=target_date,
            max_bar_timestamp=max_bar_timestamp,
            db_manager=db_manager
        )
        st = entry.get("add_advisory_state", "UNKNOWN")
        badge_html, action_html = format_add_advisory_badge_and_action(st)
        evidence_str = format_advisory_evidence_korean(entry)
        
        rows_html += f"""
        <tr style="border-bottom:1px solid #F1F5F9;">
            <td style="padding:6px 4px; font-weight:bold; color:#0F172A; overflow-wrap:anywhere; word-break:break-word;">{name}<br><span style="font-size:9.5px; color:#64748B; font-weight:normal;">({code})</span></td>
            <td style="padding:6px 4px; overflow-wrap:anywhere; word-break:break-word;">{badge_html}</td>
            <td style="padding:6px 4px; color:#334155; font-size:10.5px; overflow-wrap:anywhere; word-break:break-word;">{evidence_str}</td>
            <td style="padding:6px 4px; overflow-wrap:anywhere; word-break:break-word;">{action_html}</td>
        </tr>
        """
        
    table_section_html = f"""
    <div style="background:#FFFFFF; border:1px solid #E2E8F0; border-radius:8px; padding:12px 10px; margin-bottom:12px; box-shadow:0 1px 4px rgba(0,0,0,0.03); box-sizing:border-box; width:100%;">
        <div style="font-size:13px; font-weight:bold; color:#0F172A; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
            <span>📈 45M ADD ADVISORY (추가매수 관찰)</span>
            <span style="font-size:10px; color:#64748B; font-weight:normal;">완성 45분봉 기준</span>
        </div>
        <table style="width:100%; border-collapse:collapse; font-size:11px; text-align:left; table-layout:fixed; box-sizing:border-box;">
            <thead>
                <tr style="background:#F8FAFC; border-bottom:1px solid #E2E8F0; color:#475569; font-size:10.5px;">
                    <th style="padding:6px 4px; width:25%;">종목</th>
                    <th style="padding:6px 4px; width:22%;">45분 ADD</th>
                    <th style="padding:6px 4px; width:31%;">핵심근거</th>
                    <th style="padding:6px 4px; width:22%;">행동</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
    """
    return table_section_html

def format_t_3d(val: Any) -> str:
    """T 3D 변화량을 정수 + 부호 (+22, +8, -6, 0) 또는 DATA_GAP으로 포맷팅"""
    if val is None:
        return "DATA_GAP"
    if isinstance(val, str) and (val.strip().upper() in ("NONE", "NULL", "DATA_GAP", "") or val.strip() == "-"):
        return "DATA_GAP"
    try:
        num = round(float(val))
        if num > 0:
            return f"+{num}"
        return f"{num}"
    except (ValueError, TypeError):
        return "DATA_GAP"

def build_today_action_summary_html(held_portfolio: Optional[List[Dict[str, Any]]], policy_display: Optional[Dict[str, Any]]) -> str:
    """
    Gmail 최상단 '📌 오늘의 핵심 조치' 한 줄 요약 HTML 블록 생성.
    우선순위:
    1. 실제 권고 매도수량 (비중축소 / 감축: {name} {qty}주)
    2. 손절선 상향 종목 (손절 상향: {name} {old} → {new} (+{diff}))
    3. LOSS_DEFENSE 발동 상태
    4. NEW_433 Stage READY 또는 blocker
    5. 거래정지 등 주문금지
    """
    lines = []
    held_portfolio = held_portfolio or []
    policy_display = policy_display or {}
    policy_rows = policy_display.get("rows", [])
    loss_defenses = []
    new_433_items = []

    # 1. 실제 권고 매도수량
    reductions = []
    for h in held_portfolio:
        code = str(h.get("stock_code", "")).strip().zfill(6)
        if code == "234920" or h.get("trade_mode") == "SUSPENDED_HOLD":
            continue
        rec_qty = h.get("recommended_order_qty", 0)
        order_dir = str(h.get("order_direction", ""))
        name = h.get("stock_name") or code
        if isinstance(rec_qty, (int, float)) and rec_qty > 0 and ("매도" in order_dir or "축소" in order_dir or h.get("trade_mode") in ("CONCENTRATION_RISK", "RECOVERY")):
            reductions.append(f"{name} {int(rec_qty):,}주")
    if reductions:
        lines.append(f"• <b>비중축소</b>: {' / '.join(reductions)}")

    # 2. 손절선 상향 종목
    raised_stops = []
    for h in held_portfolio:
        code = str(h.get("stock_code", "")).strip().zfill(6)
        if code == "234920" or h.get("trade_mode") == "SUSPENDED_HOLD":
            continue
        prev_stop = h.get("previous_confirmed_stop") if h.get("previous_confirmed_stop") is not None else h.get("prev_confirmed_stop")
        cur_stop = h.get("confirmed_stop_price") if h.get("confirmed_stop_price") is not None else h.get("kiwoom_stop_tick_price")
        name = h.get("stock_name") or code
        if isinstance(prev_stop, (int, float)) and isinstance(cur_stop, (int, float)):
            if cur_stop > prev_stop and prev_stop > 0:
                diff = round(cur_stop - prev_stop)
                raised_stops.append(f"{name} {int(prev_stop):,} → {int(cur_stop):,} (+{diff:,})")
        elif "상향" in str(h.get("smartphone_action", "")) or "상향" in str(h.get("stop_update_status", "")):
            raised_stops.append(f"{name} 상향 수정")
    if raised_stops:
        lines.append(f"• <b>손절 상향</b>: {' / '.join(raised_stops)}")

    # 3. LOSS_DEFENSE 발동 상태 및 4. NEW_433
    if policy_display.get("status") == "UNAVAILABLE":
        lines.append("• ⚠️ <b>Policy Shadow</b>: DATA_UNAVAILABLE")
    else:
        loss_defenses = []
        for p in policy_rows:
            if p.get("is_suspended") or p.get("hard_stop_already_breached_at_shadow_start"):
                continue
            if p.get("loss_defense_trigger") == "ON" or p.get("defense_state") in ("WAIT_REBOUND", "TRAIL_ACTIVE"):
                name = p.get("stock_name") or p.get("stock_code")
                state = p.get("defense_state", "ON")
                loss_defenses.append(f"{name} ({state})")
        if loss_defenses:
            lines.append(f"• <b>LOSS_DEFENSE 발동</b>: {' / '.join(loss_defenses)}")
        elif any(p.get("loss_defense_trigger") == "OFF" for p in policy_rows):
            lines.append("• <b>LOSS_DEFENSE 신규 발동</b>: 없음")

        # 4. NEW_433 Stage READY 또는 blocker
        new_433_items = []
        for p in policy_rows:
            if p.get("position_kind") == "NEW_433_CYCLE" or p.get("cycle_position_kind") == "NEW_433_CYCLE":
                name = p.get("stock_name") or p.get("stock_code")
                stage = p.get("stage", 1)
                if stage == 1:
                    gate = format_display_val(p.get("stage2_gate"), "DATA_GAP")
                    if gate == "STAGE2_READY":
                        new_433_items.append(f"{name} Stage2 준비완료")
                    else:
                        new_433_items.append(f"{name} Stage2 차단 — {gate}")
                elif stage == 2:
                    gate = format_display_val(p.get("stage3_gate"), "DATA_GAP")
                    if gate == "STAGE3_READY":
                        new_433_items.append(f"{name} Stage3 준비완료")
                    else:
                        new_433_items.append(f"{name} Stage3 차단 — {gate}")
        if new_433_items:
            lines.append(f"• <b>NEW_433</b>: {' / '.join(new_433_items)}")

    # 5. 거래정지 등 주문금지
    suspended_items = []
    for h in held_portfolio:
        code = str(h.get("stock_code", "")).strip().zfill(6)
        if code == "234920" or h.get("trade_mode") == "SUSPENDED_HOLD":
            name = h.get("stock_name") or code
            suspended_items.append(f"{name} (거래정지)")
    if suspended_items and not (reductions or raised_stops or loss_defenses):
        lines.append(f"• <b>주문금지</b>: {' / '.join(suspended_items)}")

    # Check if there is anything actionable or special
    has_actions = bool(lines)
    if not has_actions:
        content_html = "<div>• <b>특이 조치 없음</b> (전 종목 정상 감시 유지)</div>"
    else:
        content_html = f"<div>{' &nbsp;|&nbsp; '.join(lines)}</div>"

    return f"""
    <div style="background:#F8FAFC; border:1px solid #CBD5E1; border-left:4px solid #0F172A; border-radius:6px; padding:10px 12px; margin-bottom:12px; font-size:11px; line-height:1.6; color:#1E293B; box-sizing:border-box; width:100%;">
        <div style="font-size:12px; font-weight:bold; color:#0F172A; margin-bottom:5px;">📌 오늘의 핵심 조치</div>
        {content_html}
    </div>
    """

def build_all_holdings_strategy_table_html(
    held_portfolio: Optional[List[Dict[str, Any]]] = None,
    policy_display: Optional[Dict[str, Any]] = None,
) -> str:
    """
    2) 📊 전체 보유종목 현황
    - 현재 실제 보유종목(quantity > 0) 전부 빠짐없이 표시
    - 모바일 최적화 compact card row (종목당 5줄)
    - 이메일 본문에는 행동·가격 감시선·Policy만 표시하고 세부 지표는 XLSX에 유지
    """
    if not held_portfolio:
        return ""

    held_items = [h for h in held_portfolio if (h.get("quantity", 0) or 0) > 0]
    if not held_items:
        return ""

    policy_display = policy_display or {}
    policy_rows = policy_display.get("rows", [])
    policy_map = {str(p.get("stock_code", "")).zfill(6): p for p in policy_rows}

    rows_html = []
    for h in held_items:
        code = str(h.get("stock_code", "")).zfill(6)
        action_audit_attr = (
            f' data-stock-code="{code}"' if is_meaningful_action_item(h) else ""
        )
        name = html.escape(str(h.get("stock_name") or code))
        cur_p = h.get("current_price", 0)
        cur_price_disp = format_krw(cur_p)
        pnl_pct = float(h.get("pnl_pct", 0.0) or 0.0)
        daily_chg = float(h.get("daily_change_pct", 0.0) or 0.0)
        trade_mode = str(h.get("trade_mode", "NORMAL")).upper()
        action_st = str(h.get("action_status", ""))
        rec_qty = h.get("recommended_order_qty", 0) or 0
        excess_qty = h.get("excess_qty", 0) or 0
        user_override_flag = bool(h.get("user_override_flag")) or trade_mode == "USER_OVERRIDE"
        is_stop_breached = bool(h.get("is_stop_breached")) or ("침범" in action_st)
        prev_stop = h.get("previous_confirmed_stop") if h.get("previous_confirmed_stop") is not None else h.get("prev_confirmed_stop")
        cur_stop = h.get("confirmed_stop_price") if h.get("confirmed_stop_price") is not None else h.get("kiwoom_stop_tick_price")
        profit_act_p = h.get("profit_activation_price")
        profit_act_status = h.get("profit_activation_status")
        profit_trail_p = h.get("profit_trail_price")
        k_target = h.get("kiwoom_target_tick_price")
        avg_buy_price = h.get("avg_buy_price")
        buy_watch_price = h.get("buy_watch_price")
        buy_rebound_delta = h.get("buy_rebound_delta")
        p = policy_map.get(code)

        # 1. PnL color and sign
        pnl_color = "#DC2626" if pnl_pct > 0 else ("#2563EB" if pnl_pct < 0 else "#64748B")
        pnl_sign = "+" if pnl_pct > 0 else ""

        # 2. Strategy Badge & Left Accent Border
        if trade_mode == "SUSPENDED_HOLD" or code == "234920" or (p and p.get("is_suspended")):
            strategy_badge = '<span style="background:#F1F5F9; border:1px solid #94A3B8; color:#475569; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">⚫ 거래정지 HOLD / 주문 금지</span>'
            accent_border = "#94A3B8"
        elif user_override_flag:
            strategy_badge = '<span style="background:#FFFBEB; border:1px solid #FCD34D; color:#B45309; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">⚠️ DART 미확정 [수동감시]</span>'
            accent_border = "#F59E0B"
        elif is_stop_breached:
            strategy_badge = '<span style="background:#FEF2F2; border:1px solid #FCA5A5; color:#DC2626; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">🚨 손절선 침범 [손실축소 판단 필요]</span>'
            accent_border = "#EF4444"
        elif p and p.get("hard_stop_already_breached_at_shadow_start"):
            strategy_badge = '<span style="background:#FEF2F2; border:1px solid #FCA5A5; color:#DC2626; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">🔴 HARD STOP SHADOW (기존손실)</span>'
            accent_border = "#EF4444"
        elif p and p.get("shadow_action") == "HARD_STOP_EXIT":
            strategy_badge = '<span style="background:#FEF2F2; border:1px solid #FCA5A5; color:#DC2626; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">🔴 HARD STOP EXIT SHADOW</span>'
            accent_border = "#EF4444"
        elif p and (p.get("loss_defense_trigger") == "ON" or p.get("defense_state") in ("WAIT_REBOUND", "TRAIL_ACTIVE")):
            defense_st = p.get("defense_state", "ON")
            strategy_badge = f'<span style="background:#FEF2F2; border:1px solid #FCA5A5; color:#DC2626; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">🔴 LOSS_DEFENSE 활성 ({defense_st})</span>'
            accent_border = "#EF4444"
        elif p and (p.get("position_kind") == "NEW_433_CYCLE" or p.get("cycle_position_kind") == "NEW_433_CYCLE"):
            gate = p.get("stage2_gate", "DATA_GAP")
            stg = p.get("stage", 1)
            if gate == "STAGE2_READY":
                strategy_badge = f'<span style="background:#EFF6FF; border:1px solid #93C5FD; color:#1D4ED8; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">🔵 NEW_433 Stage{stg} (Stage2 READY)</span>'
                accent_border = "#3B82F6"
            else:
                strategy_badge = f'<span style="background:#F8FAFC; border:1px solid #CBD5E1; color:#334155; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">⚪ NEW_433 Stage{stg} (Stage2 BLOCKED)</span>'
                accent_border = "#64748B"
        elif trade_mode == "CONCENTRATION_RISK" or excess_qty > 0:
            strategy_badge = '<span style="background:#FEF2F2; border:1px solid #FCA5A5; color:#DC2626; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">🟡 20% 초과분 분할축소</span>'
            accent_border = "#EF4444"
        elif trade_mode == "RECOVERY":
            strategy_badge = '<span style="background:#EFF6FF; border:1px solid #93C5FD; color:#1D4ED8; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">🟠 손실축소형 반등 관찰</span>'
            accent_border = "#F97316"
        elif isinstance(prev_stop, (int, float)) and isinstance(cur_stop, (int, float)) and cur_stop > prev_stop and prev_stop > 0:
            strategy_badge = '<span style="background:#F0FDF4; border:1px solid #86EFAC; color:#166534; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">🟢 래칫 상향 보유</span>'
            accent_border = "#10B981"
        elif daily_chg > 0 and pnl_pct >= 0:
            strategy_badge = '<span style="background:#F0FDF4; border:1px solid #86EFAC; color:#166534; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">🟢 상승추세 보유</span>'
            accent_border = "#10B981"
        else:
            strategy_badge = '<span style="background:#F0FDF4; border:1px solid #86EFAC; color:#166534; font-weight:bold; font-size:10px; padding:2px 6px; border-radius:4px;">🟢 보유 / 관찰</span>'
            accent_border = "#10B981"

        # 3. Monitoring Lines (표시 전용; 기존 계산값을 그대로 사용)
        avg_price_disp = format_krw(avg_buy_price)
        daily_sign = "+" if daily_chg > 0 else ""
        target_val = profit_act_p if (
            isinstance(profit_act_p, (int, float)) and profit_act_p > 0
        ) else k_target
        target_disp = format_krw(target_val)
        trail_delta = h.get("profit_trail_delta")
        trail_disp = (
            f"최고가 대비 {format_krw(abs(trail_delta))} 하락 시"
            if isinstance(trail_delta, (int, float)) and trail_delta != 0
            else "비활성"
        )
        buy_watch_disp = format_krw(buy_watch_price)
        rebound_disp = (
            f"{format_krw(buy_rebound_delta)} 상승 확인"
            if isinstance(buy_rebound_delta, (int, float)) and buy_rebound_delta > 0
            else "-"
        )
        if trade_mode == "SUSPENDED_HOLD" or code == "234920":
            stop_disp = target_disp = trail_disp = buy_watch_disp = rebound_disp = "HOLD (거래정지)"
        elif trade_mode == "CONCENTRATION_RISK":
            stop_disp = format_krw(cur_stop)
            target_disp = format_krw(target_val)
        elif trade_mode == "USER_OVERRIDE":
            stop_disp = "HOLD (주문대기)"
            target_disp = format_krw(k_target)
        else:
            stop_disp = format_krw(cur_stop)

        # 4. Policy Compact String
        if policy_display.get("status") == "UNAVAILABLE":
            policy_compact_str = "Policy: ⚠️ DATA_UNAVAILABLE"
        elif trade_mode == "SUSPENDED_HOLD" or code == "234920" or (p and p.get("is_suspended")):
            policy_compact_str = "Policy: 거래정지 / V4 HOLD"
        elif not p or not p.get("display_allowed"):
            fresh_st = format_display_val(p.get("freshness_status") if p else None, "STALE")
            policy_compact_str = f"Policy: {fresh_st} — 행동판정 금지"
        elif p.get("hard_stop_already_breached_at_shadow_start"):
            policy_compact_str = "Policy: LEGACY | 기존 -22%선 기통과 | LD OFF"
        elif p.get("shadow_action") == "HARD_STOP_EXIT":
            policy_compact_str = "Policy: HARD_STOP_EXIT SHADOW"
        elif p.get("loss_defense_trigger") == "ON" or p.get("defense_state") not in (None, "OFF"):
            defense_st = format_display_val(p.get("defense_state"), "ON")
            t_3d = format_t_3d(p.get("t_change_3d"))
            policy_compact_str = f"Policy: LD {defense_st} | T 3D: {t_3d}"
        elif p.get("position_kind") == "NEW_433_CYCLE" or p.get("cycle_position_kind") == "NEW_433_CYCLE":
            is_etf = bool(p.get("is_etf", False)) or code in ['371460', '484730', '490590', '161510', '088500']
            f_val = "N/A" if is_etf else format_display_val(p.get("f_score"), "DATA_GAP")
            t_val = format_display_val(p.get("t_score"), "DATA_GAP")
            gate = format_display_val(p.get("stage2_gate"), "DATA_GAP")
            gate_short = "S2 READY" if gate == "STAGE2_READY" else f"S2 BLOCKED ({gate})"
            stg = format_display_val(p.get("stage"), "1")
            policy_compact_str = f"Policy: S{stg} | F{f_val}/T{t_val} | {gate_short}"
        else:
            t_3d = format_t_3d(p.get("t_change_3d"))
            policy_compact_str = f"Policy: LEGACY | T 3D: {t_3d} | LD OFF"

        bb_atr_html = ""
        bb_atr = h.get("bb_atr")
        if isinstance(bb_atr, dict):
            bb_mode = html.escape(str(bb_atr.get("bb_mode", "NO_ADVISORY")))
            bb_text = html.escape(str(bb_atr.get("advisory_text", "데이터 확인 필요 · 기존 V4 전략 유지")))
            bb_floor = bb_atr.get("effective_advisory_floor")
            bb_atr_html = f"""
            <div style="font-size:10.5px; color:#334155; margin-bottom:2px;">
                • <b>BB-ATR ADVISORY:</b> {bb_text} ({bb_mode})
            </div>
            <div style="font-size:10.5px; color:#334155; margin-bottom:2px;">
                • <b>BB-ATR FLOOR:</b> {format_krw(bb_floor) if bb_floor else '-'}
            </div>"""

        rows_html.append(f"""
        <div data-overview-code="{code}"{action_audit_attr} style="background:#FFFFFF; border:1px solid #E2E8F0; border-left:3px solid {accent_border}; border-radius:6px; padding:7px 9px; margin-bottom:6px; box-sizing:border-box; width:100%;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:3px;">
                <div style="font-size:12.5px; font-weight:bold; color:#0F172A; word-break:break-word;">
                    {name} <span style="font-size:10.5px; color:#64748B; font-weight:normal;">({code})</span>
                </div>
                <div style="font-size:12px; font-weight:bold; color:{pnl_color}; text-align:right; flex-shrink:0;">
                    {cur_price_disp} <span style="font-size:10.5px;">({daily_sign}{daily_chg:.1f}%)</span>
                </div>
            </div>
            <div style="font-size:11px; margin-bottom:3px;">
                <span style="color:#64748B;">현재 행동:</span> {strategy_badge}
            </div>
            <div style="font-size:10.5px; color:#334155; margin-bottom:2px;">
                • 내 평단 {avg_price_disp} | 평가손익 <b style="color:{pnl_color};">{pnl_sign}{pnl_pct:.1f}%</b>
            </div>
            <div style="font-size:10.5px; color:#334155; margin-bottom:2px;">
                • 손절가 {stop_disp} | 익절 목표가 {target_disp}
            </div>
            <div style="font-size:10.5px; color:#334155; margin-bottom:2px;">
                • 익절 trailing 하락폭 {trail_disp}
            </div>
            <div style="font-size:10.5px; color:#334155; margin-bottom:2px;">
                • 추매 감시가 {buy_watch_disp} | 반등확인 {rebound_disp}
            </div>
            {bb_atr_html}
            <div style="font-size:10px; color:#64748B;">
                • {policy_compact_str}
            </div>
        </div>""")

    total_held_count = len(held_items)
    return f"""
    <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:10px 12px; margin-bottom:12px; box-sizing:border-box; width:100%;">
        <div style="font-size:12.5px; font-weight:bold; color:#0F172A; margin-bottom:8px;">
            📊 전체 보유종목 현황 ({total_held_count}종목)
        </div>
        {''.join(rows_html)}
    </div>
    """

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
    elif "손절" in clean or "STOP_BREACHED" in clean:
        return f'<span style="background:#FEF2F2; border:1px solid #FCA5A5; color:#DC2626; font-weight:bold; font-size:10.5px; padding:3px 7px; border-radius:5px; display:inline-block;">🚨 손절선 침범 [손실축소 판단 필요]</span>'
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
    disclosures: Optional[List[Dict[str, Any]]] = None,
    policy_display: Optional[Dict[str, Any]] = None,
    db_manager: Any = None,
    marketcap_radar_items: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    모바일 반응형 이메일 리포트 V2 HTML 생성 함수
    - 360px ~ 430px 모바일 뷰포트에서 가로 스크롤 없이 최적 가독성 제공
    - 1) 📌 오늘의 핵심 조치 (최대 4~5줄)
    - 2) 📊 전체 보유종목 현황 (현재 실제 보유종목 전부, 종목당 1회)
    - 3) 📈 45M ADD ADVISORY
    - 4) 📢 DART 주요 공시 & 브리핑
    - 감사용 전체 보유 종목코드 집합을 data-held-stock-codes 속성에 기록
    - CSS Grid, JavaScript, 고정 min-width 테이블 배제 (이메일 클라이언트 표준 호환)
    """
    # 1. 🔥 필수 원천 키 무결성 검증 (임의 기본값 대체 금지)
    if held_portfolio:
        for idx, h in enumerate(held_portfolio):
            for k in MANDATORY_STOCK_KEYS:
                if k not in h:
                    raise KeyError(f"[MobileRendererV2] 필수 원천 필드 누락: stock index {idx}, code: '{h.get('stock_code')}', key: '{k}'")

    all_held_codes = sorted([str(h.get("stock_code")).zfill(6) for h in held_portfolio]) if held_portfolio else []
    all_held_codes_str = ",".join(all_held_codes)

    # 1) 📌 최상단 오늘의 핵심 조치 HTML 생성
    today_action_summary_html = build_today_action_summary_html(held_portfolio, policy_display)

    # 2) 📊 전체 보유종목 전략 현황 HTML 생성
    all_holdings_strategy_table_html = build_all_holdings_strategy_table_html(held_portfolio, policy_display)

    # Policy Shadow is display-only.
    policy_display = policy_display or {}
    policy_rows = policy_display.get("rows", [])
    if policy_display.get("status") == "UNAVAILABLE":
        policy_section_html = """
        <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-left:4px solid #F59E0B; border-radius:6px; padding:9px 10px; margin-bottom:12px; font-size:10.5px; color:#64748B;">
            ⚠️ <b>Policy Shadow: DATA_UNAVAILABLE</b> (V4 분석 및 리포트는 정상 기준으로 발송됩니다)
        </div>"""
    elif policy_rows:
        # Common Policy Snapshot Timestamp Header
        first_p = policy_rows[0]
        asof_ts = first_p.get("asof_timestamp", "")
        rep_ts = first_p.get("report_timestamp", "") or date_str
        asof_time = asof_ts[11:16] if len(asof_ts) >= 16 else format_display_val(asof_ts, "N/A")
        rep_time = rep_ts[11:16] if len(rep_ts) >= 16 else (rep_ts[-5:] if len(rep_ts) >= 5 else format_display_val(rep_ts, "N/A"))
        age_min = first_p.get("snapshot_age_minutes")
        age_str = f"{int(round(age_min))}분 경과" if isinstance(age_min, (int, float)) else "0분 경과"

        all_fresh = all(p.get("freshness_status") == "FRESH" for p in policy_rows)
        all_match = all(p.get("position_match_status") == "MATCH" for p in policy_rows)
        if all_fresh and all_match:
            status_tag = "FRESH/MATCH"
        elif all_fresh:
            status_tag = "FRESH"
        else:
            status_tag = "PARTIAL_CHECK"

        policy_meta_line = f"<div style='font-size:10px; color:#4F46E5; margin-bottom:6px;'>Policy 기준 {asof_time} | Report {rep_time} | {age_str} | {status_tag}</div>"

        policy_items = []
        for p in policy_rows:
            name = html.escape(str(p.get("stock_name") or p.get("stock_code")))
            code = html.escape(str(p.get("stock_code", "")).zfill(6))
            is_etf = bool(p.get("is_etf", False)) or code in ['371460', '484730', '490590', '161510', '088500']

            if not p.get("display_allowed"):
                fresh_st = format_display_val(p.get('freshness_status'), "STALE_POLICY_SNAPSHOT")
                policy_items.append(f"<div style='margin:4px 0;'>⚪ <b>{name} ({code})</b> | {html.escape(fresh_st)} — 최신 계좌 상태와 Policy snapshot 불일치 또는 지연. 후속 Shadow 반영 대기 / 행동판정 사용 금지</div>")
                continue
            if p.get("is_suspended"):
                policy_items.append(f"<div style='margin:4px 0;'>⏸ <b>{name} ({code})</b> | 거래정지 참고 상태 — V4 HOLD / 주문 설정 금지</div>")
                continue
            if p.get("hard_stop_already_breached_at_shadow_start"):
                hard_text = "기존 손실 상태 — 신규 -22% 매도신호 아님"
            elif p.get("shadow_action") == "HARD_STOP_EXIT":
                hard_text = "⚠️ HARD_STOP_EXIT SHADOW"
            else:
                hard_text = format_display_val(p.get("hard_stop_state"), "정상")

            t_3d = format_t_3d(p.get("t_change_3d"))

            if p.get("position_kind") == "NEW_433_CYCLE" or p.get("cycle_position_kind") == "NEW_433_CYCLE":
                f0_disp = "NOT_APPLICABLE" if is_etf else format_display_val(p.get('f0'), "DATA_GAP")
                f_disp = "NOT_APPLICABLE" if is_etf else format_display_val(p.get('f_score'), "DATA_GAP")
                t0_disp = format_display_val(p.get('t0'), "DATA_GAP")
                t_disp = format_display_val(p.get('t_score'), "DATA_GAP")
                target_qty_disp = format_display_val(p.get('cycle_target_qty'), "DATA_GAP")
                stage1_target_disp = format_display_val(p.get('stage1_target_qty'), "DATA_GAP")
                qty_curr_disp = format_display_val(p.get('quantity_current', p.get('quantity')), "DATA_GAP")
                stage_size_disp = format_display_val(p.get('stage_size_status'), "TARGET_QTY_DATA_GAP")
                stage2_gate_disp = format_display_val(p.get('stage2_gate'), "BASELINE_DATA_GAP")
                stage2_gate_text = f"차단 — {stage2_gate_disp}" if "GAP" in stage2_gate_disp or "BLOCK" in stage2_gate_disp or stage2_gate_disp == "NOT_READY" else stage2_gate_disp

                policy_items.append(
                    f"<div style='margin:6px 0;'><b>{name} ({code}) | NEW_433_CYCLE | Stage {format_display_val(p.get('stage'), '1')}</b><br>"
                    f"F0: {f0_disp} / F: {f_disp}<br>"
                    f"T0: {t0_disp} / T: {t_disp}<br>"
                    f"목표수량: {target_qty_disp} | Stage1 40% 목표: {stage1_target_disp} | 실제수량: {qty_curr_disp}<br>"
                    f"Stage1 size: {stage_size_disp} | Stage2 Gate: {stage2_gate_text}<br>"
                    f"실제 주문 영향: 없음</div>"
                )
            elif p.get("loss_defense_trigger") == "ON" or p.get("defense_state") not in (None, "OFF"):
                loss_pct = p.get('loss_pct')
                loss_disp = f"{loss_pct:.1f}" if isinstance(loss_pct, (int, float)) else format_display_val(loss_pct, "0")
                defense_st = format_display_val(p.get('defense_state'), "OFF")
                policy_items.append(
                    f"<div style='margin:6px 0;'><b>⚠️ LOSS_DEFENSE SHADOW — {name} ({code})</b><br>"
                    f"손실 {loss_disp}% | T 3D: {t_3d} | 45m Bearish {bool(p.get('is_45m_bearish_gate'))} | defense {html.escape(defense_st)} | hard stop {hard_text} | 가상판정 / 주문 영향 없음</div>"
                )
            elif p.get("hard_stop_already_breached_at_shadow_start") or p.get("shadow_action") == "HARD_STOP_EXIT":
                policy_items.append(
                    f"<div style='margin:4px 0;'><b>{name} ({code})</b> | LEGACY | {hard_text} | Policy 정상 감시 | LD OFF | 주문영향 0</div>"
                )
            else:
                # Regular LEGACY items are already shown in Section 2 overview. If no other items, show concise line.
                pass

        if not policy_items:
            policy_items_html = "<div style='color:#64748B; font-size:10.5px; margin:4px 0;'>• 모든 보유종목 Policy 정상 감시 유지 (특이 Policy 발동 없음)</div>"
        else:
            policy_items_html = ''.join(policy_items)

        policy_section_html = f"""
        <div style="background:#F8FAFC; border:1px solid #C7D2FE; border-left:4px solid #6366F1; border-radius:6px; padding:9px 10px; margin-bottom:12px; font-size:10.5px; color:#334155; line-height:1.45; box-sizing:border-box; width:100%;">
            <div style="font-weight:bold; color:#3730A3; margin-bottom:2px;">🧪 Policy Shadow 특이사항 — 실제 주문 영향 없음</div>
            {policy_meta_line}
            {policy_items_html}
        </div>"""
    else:
        policy_section_html = ""

    # 2. 📢 DART 주요 공시 브리핑 섹션 (모바일 카드 스타일)
    if disclosures and len(disclosures) > 0:
        seen_rcept_nos = set()
        d_items_html = ""
        for idx, d in enumerate(disclosures):
            r_no = d.get("rcept_no")
            if r_no is None or not str(r_no).strip():
                raise ValueError(f"[MobileRendererV2] DART 공시 rcept_no 누락 또는 빈 문자열: index {idx}, item {d}")
            r_id_str = str(r_no).strip()
            if r_id_str in seen_rcept_nos:
                raise ValueError(f"[MobileRendererV2] DART 공시 rcept_no 중복 감지: '{r_id_str}'")
            seen_rcept_nos.add(r_id_str)

            s_name = html.escape(str(d.get('stock_name', '')))
            s_code = html.escape(str(d.get('stock_code', '')).zfill(6))
            r_name = html.escape(str(d.get('report_nm', '')))
            r_id = html.escape(r_id_str)
            d_link = sanitize_url(d.get('link'))
            d_sum = html.escape(str(d.get('summary', '')))
            d_imp = html.escape(str(d.get('impact', '')))
            d_guide = html.escape(str(d.get('guide', '')))

            d_items_html += f"""
            <div data-disclosure-id="{r_id}" data-disclosure-code="{s_code}" style="background:#FFFFFF; border:1px solid #E2E8F0; border-radius:6px; padding:10px; margin-bottom:8px; box-sizing:border-box; width:100%; min-width:0; word-break:break-word; overflow-wrap:anywhere;">
                <div style="font-size:12px; font-weight:bold; color:#0F172A; margin-bottom:4px; word-break:break-word; overflow-wrap:anywhere;">
                    📌 <span style="color:#1E3A8A;">{s_name} ({s_code})</span> - <a href="{d_link}" target="_blank" style="color:#2563EB; text-decoration:underline; word-break:break-word; overflow-wrap:anywhere;">{r_name}</a>
                </div>
                <div style="font-size:11px; color:#334155; line-height:1.5; word-break:break-word; overflow-wrap:anywhere;">
                    <div style="margin-bottom:2px;">• <b>공시요약</b>: {d_sum}</div>
                    <div style="margin-bottom:2px;">• <b>시장의미</b>: {d_imp}</div>
                    <div>• <b>대응가이드</b>: {d_guide}</div>
                </div>
            </div>
            """

        disclosure_section_html = f"""
        <div style="background:#F8FAFC; border:1px solid #CBD5E1; border-left:4px solid #2563EB; border-radius:8px; padding:12px 10px; margin-bottom:16px; box-sizing:border-box; width:100%;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; border-bottom:1px solid #E2E8F0; padding-bottom:4px; min-width:0; box-sizing:border-box;">
                <span style="font-size:13px; color:#1E3A8A; font-weight:bold; overflow-wrap:anywhere;">📢 DART 주요 공시 & 브리핑 ({len(disclosures)}건)</span>
                <span style="font-size:10px; color:#64748B; flex-shrink:0;">원문 링크 포함</span>
            </div>
            {d_items_html}
        </div>
        """
    else:
        disclosure_section_html = """
        <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:10px 12px; margin-bottom:16px; font-size:11px; color:#475569;">
            📢 <b>보유종목 DART 공시</b>: 오늘 확인된 주요 신규 공시 없음
        </div>
        """

    # 3. 💼 대응 필요 종목 모바일 카드 리스트 생성 (계속 보유/변화 없음 종목 제외)
    target_date, max_bar_ts = parse_date_str_for_freshness(date_str)
    actionable_cards_html = ""
    actionable_held = [h for h in held_portfolio if is_meaningful_action_item(h)] if held_portfolio else []

    if actionable_held:
        for h in actionable_held:
            code = html.escape(str(h.get("stock_code")).zfill(6))
            name = html.escape(str(h.get("stock_name")))
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
            profit_act_status = str(h.get("profit_activation_status", "INACTIVE")).upper()
            profit_act_p = h.get("profit_activation_price") or h.get("raw_profit_activation")
            profit_trail_p = h.get("profit_trail_price") or h.get("profit_trail") or 0
            prev_stop = h.get("previous_confirmed_stop") if h.get("previous_confirmed_stop") is not None else h.get("prev_confirmed_stop")
            cur_stop = h.get("confirmed_stop_price") if h.get("confirmed_stop_price") is not None else h.get("kiwoom_stop_tick_price")
            stop_update_status = str(h.get("stop_update_status", ""))
            is_stop_breached = bool(h.get("is_stop_breached")) or ("침범" in action_st) or ("침범" in stop_update_status)

            smartphone_action = h.get("smartphone_action")
            if trade_mode == "SUSPENDED_HOLD" or code == "234920":
                smartphone_action = "HOLD — 주문 설정 금지"
            elif h.get("user_override_flag") or trade_mode == "USER_OVERRIDE":
                smartphone_action = "수동감시 — 자동 변경 금지"
            elif is_stop_breached:
                smartphone_action = "손절선 침범 — 즉시 확인"
            elif isinstance(prev_stop, (int, float)) and isinstance(cur_stop, (int, float)) and cur_stop > prev_stop and prev_stop > 0:
                diff = round(cur_stop - prev_stop)
                smartphone_action = f"손절선 {int(prev_stop):,} → {int(cur_stop):,} (+{diff:,})"
            elif smartphone_action and smartphone_action != "변경 없음":
                pass
            elif "상향" in stop_update_status:
                smartphone_action = "상향 수정"
            elif "신규" in stop_update_status:
                smartphone_action = "신규 입력"
            elif trade_mode == "HOLD":
                smartphone_action = "HOLD — 주문 설정 금지"
            else:
                smartphone_action = "변경 없음"

            if code == "234920" or trade_mode == "SUSPENDED_HOLD":
                target_label = "익절선:"
                target_disp = "HOLD (거래정지)"
                stop_disp = "HOLD (거래정지)"
                trail_disp = "HOLD (비활성)"
                atr_disp = "N/A (거래정지)"
                sizing_disp = f"보유 {held_qty:,}주 / <b>주문상태: 비활성 (0주)</b> [상장적격성 실질심사 매매거래정지]"
                left_border = "#94A3B8"
            elif h.get("user_override_flag", False) or trade_mode == "USER_OVERRIDE":
                target_label = "익절 활성가:"
                target_disp = f"{format_krw(k_target)} (수동활성)" if isinstance(k_target, (int, float)) else f"{k_target} (수동활성)"
                stop_disp = "HOLD (주문대기)"
                trail_disp = f"{format_krw(trail_delta)} (수동추적)" if isinstance(trail_delta, (int, float)) else f"{trail_delta} (수동추적)"
                atr_disp = "수동 관리"
                sizing_disp = f"보유 {held_qty:,}주 | <b>수동주문: {rec_qty:,}주 미체결</b> (DART미확정 신규주문금지)"
                left_border = "#F59E0B"
            elif trade_mode == "CONCENTRATION_RISK":
                if profit_act_status == "ACTIVE":
                    target_label = "익절 추적선:"
                    trail_val = profit_trail_p if (isinstance(profit_trail_p, (int, float)) and profit_trail_p > 0) else k_target
                    target_disp = f"{format_krw(trail_val)} (ACTIVE)"
                else:
                    target_label = "익절 활성가:"
                    act_val = profit_act_p if (isinstance(profit_act_p, (int, float)) and profit_act_p > 0) else k_target
                    target_disp = f"{format_krw(act_val)} (대기)"
                stop_disp = f"{format_krw(k_stop)} (침범)" if is_stop_breached else (format_krw(k_stop) if isinstance(k_stop, (int, float)) and k_stop > 0 else str(k_stop))
                trail_disp = f"최고가 대비 {format_krw(abs(trail_delta))} 하락 시" if isinstance(trail_delta, (int, float)) and trail_delta != 0 else "HOLD (비활성)"
                atr_disp = f"{format_krw(atr_v)} ({atr_pct:.1f}%)" if isinstance(atr_v, (int, float)) and atr_v > 0 else "-"
                sizing_disp = f"위험목표 {risk_target_qty:,}주 | 20%초과 {excess_qty:,}주 | <b>권고: {order_dir} ({rec_qty:,}주)</b>"
                left_border = "#EF4444"
            elif trade_mode == "RECOVERY":
                if profit_act_status == "ACTIVE":
                    target_label = "익절 추적선:"
                    trail_val = profit_trail_p if (isinstance(profit_trail_p, (int, float)) and profit_trail_p > 0) else k_target
                    target_disp = f"{format_krw(trail_val)} (ACTIVE)"
                else:
                    target_label = "익절 활성가:"
                    act_val = profit_act_p if (isinstance(profit_act_p, (int, float)) and profit_act_p > 0) else k_target
                    target_disp = f"{format_krw(act_val)} (대기)"
                stop_disp = f"{format_krw(k_stop)} (침범)" if is_stop_breached else (format_krw(k_stop) if isinstance(k_stop, (int, float)) and k_stop > 0 else str(k_stop))
                trail_disp = f"최고가 대비 {format_krw(abs(trail_delta))} 하락 시" if isinstance(trail_delta, (int, float)) and trail_delta != 0 else "HOLD (비활성)"
                atr_disp = f"{format_krw(atr_v)} ({atr_pct:.1f}%)" if isinstance(atr_v, (int, float)) and atr_v > 0 else "-"
                sizing_disp = f"위험목표 {risk_target_qty:,}주 | 보유 {held_qty:,}주 | <b>권고: {order_dir} (손실축소 {rec_qty:,}주)</b>"
                left_border = "#3B82F6"
            else:  # NORMAL
                if profit_act_status == "ACTIVE":
                    target_label = "익절 추적선:"
                    trail_val = profit_trail_p if (isinstance(profit_trail_p, (int, float)) and profit_trail_p > 0) else k_target
                    target_disp = f"{format_krw(trail_val)} (ACTIVE)"
                else:
                    target_label = "익절 활성가:"
                    act_val = profit_act_p if (isinstance(profit_act_p, (int, float)) and profit_act_p > 0) else k_target
                    target_disp = f"{format_krw(act_val)} (대기)"
                stop_disp = f"{format_krw(k_stop)} (침범)" if is_stop_breached else (format_krw(k_stop) if isinstance(k_stop, (int, float)) and k_stop > 0 else str(k_stop))
                trail_disp = f"최고가 대비 {format_krw(abs(trail_delta))} 하락 시" if isinstance(trail_delta, (int, float)) and trail_delta != 0 else "HOLD (비활성)"
                atr_disp = f"{format_krw(atr_v)} ({atr_pct:.1f}%)" if isinstance(atr_v, (int, float)) and atr_v > 0 else "-"
                sizing_disp = f"위험목표 {risk_target_qty:,}주 | 보유 {held_qty:,}주 | <b>권고: {order_dir} ({rec_qty:,}주)</b>"
                left_border = "#10B981"

            # 스마트폰 설정 조치 박스 색상 서식
            if is_stop_breached or "침범" in smartphone_action:
                sp_bg = "#FEF2F2"
                sp_border = "#FECACA"
                sp_color = "#DC2626"
            elif "상향" in smartphone_action or "손절선" in smartphone_action:
                sp_bg = "#EFF6FF"
                sp_border = "#BFDBFE"
                sp_color = "#1D4ED8"
            elif "신규" in smartphone_action:
                sp_bg = "#F0FDF4"
                sp_border = "#DCFCE7"
                sp_color = "#166534"
            elif "수동" in smartphone_action:
                sp_bg = "#FFFBEB"
                sp_border = "#FDE68A"
                sp_color = "#D97706"
            elif "HOLD" in smartphone_action:
                sp_bg = "#F1F5F9"
                sp_border = "#E2E8F0"
                sp_color = "#64748B"
            else:
                sp_bg = "#F8FAFC"
                sp_border = "#E2E8F0"
                sp_color = "#475569"

            # 45M ADD ADVISORY 수급 지표 포맷 (절대수치 미표시 및 4컬럼 요약 규칙 적용)
            adv_entry = get_latest_add_advisory_entry(
                code,
                target_date=target_date,
                max_bar_timestamp=max_bar_ts,
                db_manager=db_manager
            )
            adv_state = adv_entry.get("add_advisory_state", "UNKNOWN")
            adv_badge, adv_action = format_add_advisory_badge_and_action(adv_state)
            adv_evidence = format_advisory_evidence_korean(adv_entry)

            cur_price_disp = format_krw(cur_price)
            pnl_amt_disp = format_krw(pnl_amt)
            pnl_amt_str = f"+{pnl_amt_disp}" if (isinstance(pnl_amt, (int, float)) and pnl_amt > 0) else pnl_amt_disp

            actionable_cards_html += f"""
            <div data-stock-code="{code}" data-trade-mode="{trade_mode}" style="background:#FFFFFF; border:1px solid #E2E8F0; border-left:4px solid {left_border}; border-radius:8px; padding:12px; margin-bottom:12px; box-shadow:0 1px 4px rgba(0,0,0,0.03); box-sizing:border-box; width:100%; min-width:0; word-break:break-word; overflow-wrap:anywhere;">
                <!-- 1. 카드 헤더 (종목명/코드 + 전략배지 + 현재가/등락률) -->
                <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px; border-bottom:1px solid #F1F5F9; padding-bottom:6px; box-sizing:border-box; width:100%; min-width:0;">
                    <div style="max-width:62%; min-width:0; word-break:break-word; overflow-wrap:anywhere;">
                        <div style="font-size:14px; font-weight:bold; color:#0F172A; word-break:break-word; overflow-wrap:anywhere; line-height:1.25;">
                            {name} <span style="font-size:11px; color:#64748B; font-weight:normal;">({code})</span>
                        </div>
                        <div style="margin-top:3px; overflow-wrap:anywhere; word-break:break-word;">
                            {strategy_badge}
                        </div>
                    </div>
                    <div style="text-align:right; min-width:0; flex-shrink:0;">
                        <div style="font-size:14px; font-weight:bold; color:{price_color};">
                            {cur_price_disp}
                        </div>
                        <div style="font-size:11px; font-weight:bold; color:{price_color};">
                            ({price_sign}{daily_chg:.2f}%)
                        </div>
                    </div>
                </div>

                <!-- 2. 평가손익 & 종합점수 바 -->
                <div style="background:#F8FAFC; border:1px solid #EEF2F6; border-radius:6px; padding:6px 8px; margin-bottom:8px; font-size:11px; display:flex; justify-content:space-between; align-items:center; box-sizing:border-box; width:100%; min-width:0;">
                    <div style="min-width:0; overflow-wrap:anywhere;">
                        <span style="color:#64748B;">종합:</span> {score_display}
                    </div>
                    <div style="min-width:0; flex-shrink:0; text-align:right;">
                        <span style="color:#64748B;">손익:</span> <b style="color:{pnl_color};">{pnl_sign}{pnl_pct:.2f}%</b> <span style="font-size:10px; color:{pnl_color};">({pnl_amt_str})</span>
                    </div>
                </div>

                <!-- 3. 스마트폰 설정 조치 바 -->
                <div style="background:{sp_bg}; border:1px solid {sp_border}; border-radius:6px; padding:6px 8px; margin-bottom:8px; font-size:11px; color:#1E293B; display:flex; justify-content:space-between; align-items:center; box-sizing:border-box; width:100%; word-break:break-word; overflow-wrap:anywhere;">
                    <span>📱 <b>스마트폰 조치:</b></span>
                    <b style="color:{sp_color};">{smartphone_action}</b>
                </div>

                <!-- 4. 핵심 가격/수량 2x2 그리드 테이블 (표 가로스크롤 없는 100% 폭) -->
                <table style="width:100%; table-layout:fixed; border-collapse:collapse; font-size:11px; margin-bottom:8px; background:#FAFAFA; border-radius:6px; border:1px solid #F1F5F9; box-sizing:border-box;">
                    <tr>
                        <td style="padding:5px 6px; width:50%; border-right:1px solid #F1F5F9; border-bottom:1px solid #F1F5F9; overflow-wrap:anywhere; word-break:break-word; box-sizing:border-box;">
                            <span style="color:#64748B; font-size:10px;">{target_label}</span> <b style="color:#059669;">{target_disp}</b>
                        </td>
                        <td style="padding:5px 6px; width:50%; border-bottom:1px solid #F1F5F9; overflow-wrap:anywhere; word-break:break-word; box-sizing:border-box;">
                            <span style="color:#64748B; font-size:10px;">손절선:</span> <b style="color:#DC2626;">{stop_disp}</b>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:5px 6px; border-right:1px solid #F1F5F9; overflow-wrap:anywhere; word-break:break-word; box-sizing:border-box;">
                            <span style="color:#64748B; font-size:10px;">트레일링:</span> <span style="color:#334155; font-size:10.5px;">{trail_disp}</span>
                        </td>
                        <td style="padding:5px 6px; overflow-wrap:anywhere; word-break:break-word; box-sizing:border-box;">
                            <span style="color:#64748B; font-size:10px;">14일 ATR:</span> <span style="color:#D97706; font-weight:bold; font-size:10.5px;">{atr_disp}</span>
                        </td>
                    </tr>
                </table>

                <!-- 4. 포지션 사이징 및 권고 주문 수량 -->
                <div style="background:#F0FDF4; border:1px solid #DCFCE7; border-radius:5px; padding:6px 8px; margin-bottom:8px; font-size:11px; color:#166534; line-height:1.4; box-sizing:border-box; width:100%; word-break:break-word; overflow-wrap:anywhere;">
                    {sizing_disp}
                </div>

                <!-- 5. 45M ADD ADVISORY 요약 칩 바 -->
                <div style="font-size:10.5px; color:#475569; line-height:1.45; background:#F8FAFC; padding:5px 8px; border-radius:5px; box-sizing:border-box; width:100%; word-break:break-word; overflow-wrap:anywhere;">
                    <div>• <b>45분 ADD:</b> {adv_badge} ({adv_action}) | <b>근거:</b> {adv_evidence}</div>
                </div>
            </div>
            """
    else:
        actionable_cards_html = """
        <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:14px; text-align:center; font-size:12px; color:#64748B; margin-bottom:12px; box-sizing:border-box; width:100%;">
            오늘 특별 대응이 필요한 보유종목 없음 (전 종목 정상 감시 유지)
        </div>
        """

    # 45M ADD ADVISORY 요약 현황 섹션 생성 (Freshness Guard 적용)
    add_advisory_section_html = build_45m_add_advisory_table_section_html(
        held_portfolio,
        target_date=target_date,
        max_bar_timestamp=max_bar_ts,
        db_manager=db_manager
    )

    # 📊 시총 리더십 레이더 요약 섹션 생성 (Sidecar)
    marketcap_radar_section_html = build_marketcap_radar_section_html(
        marketcap_radar_items,
        date_str=date_str
    )

    # 4. 📱 전체 모바일 반응형 HTML 컨테이너 래퍼 조립
    mobile_html_template = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>V4-PILOT-C 주요 대응 및 공시 리포트</title>
    <style>
        *, *:before, *:after {{
            box-sizing: border-box;
            -webkit-box-sizing: border-box;
        }}
        html, body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
            background-color: #F1F5F9;
            margin: 0;
            padding: 0;
            color: #0F172A;
            -webkit-text-size-adjust: 100%;
            width: 100%;
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
            box-sizing: border-box;
            width: 100%;
        }}
        .content-body {{
            padding: 10px 8px;
            box-sizing: border-box;
            width: 100%;
        }}
        table {{
            table-layout: fixed;
            width: 100%;
            box-sizing: border-box;
        }}
        td, th {{
            overflow-wrap: anywhere;
            word-break: break-word;
            box-sizing: border-box;
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
    <div class="mobile-wrapper" data-render-version="V2" data-held-stock-codes="{all_held_codes_str}">
        <!-- 상단 헤더 -->
        <div class="header-bar">
            <div style="font-size:10px; letter-spacing:1px; color:#93C5FD; font-weight:bold; margin-bottom:2px;">KIWOOM REST & DART INTEGRATED V4</div>
            <div style="font-size:16px; font-weight:bold; color:#FFFFFF; margin-bottom:2px;">📋 V4-PILOT-C 주요 대응 및 보유종목 공시</div>
            <div style="font-size:11px; color:#94A3B8;">기준일시: {date_str}</div>
        </div>

        <div class="content-body">
            <!-- 1) 📌 오늘의 핵심 조치 -->
            {today_action_summary_html}

            <!-- 2) 📊 전체 보유종목 현황 -->
            {all_holdings_strategy_table_html}

            <!-- 3) 📈 45M ADD ADVISORY 요약 현황 -->
            {add_advisory_section_html}

            <!-- 4) 📢 DART 주요 공시 & 브리핑 -->
            {disclosure_section_html}

            <!-- 짧은 고지문 -->
            <div style="text-align:center; font-size:10px; color:#94A3B8; padding:12px 0 16px 0;">
                ※ 본 리포트는 V4-PILOT-C 위험관리 엔진 기준값이며 실제 주문은 사용자의 확인 하에 집행됩니다.
            </div>
        </div>
    </div>
</body>
</html>
"""
    return mobile_html_template
