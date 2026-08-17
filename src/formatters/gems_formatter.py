import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from src.core.dto import ScanResultDTO

def render_gems_markdown(dto: ScanResultDTO) -> str:
    """
    단일 종목 ScanResultDTO를 Gemini Gems 복사-붙여넣기용 표준 마크다운 리포트로 렌더링합니다.
    - Phase 3.1: Data Lineage & Intraday Reliability 및 Technical Action 기반 프롬프트 분기
    """
    is_buy_on = "🔵 ON" in dto.buy_approval

    # 1. 실행 가능 주문 상태 및 Section 3 렌더링
    if dto.technical_action == "BUY_ALLOWED":
        order_exec_status = "🟢 1차 진입(50%) 주문 승인 (Technical Gate: BUY_ALLOWED 통과)"
    elif dto.technical_action == "BUY_ALLOWED_CONDITIONAL":
        order_exec_status = "🟡 1차 진입(50%) 조건부 승인 (Technical Gate: BUY_ALLOWED_CONDITIONAL)"
    elif dto.technical_action == "BUY_WAIT":
        order_exec_status = "⏸️ 1차 진입 보류/대기 (Technical Gate: BUY_WAIT - 45m 수급 약세)"
    elif dto.technical_action == "BUY_WAIT_DATA":
        order_exec_status = "🔴 NONE (45분봉 데이터 확인 필요 / 현재 주문 금지 - BUY_WAIT_DATA)"
    elif dto.technical_action == "BUY_BLOCKED":
        order_exec_status = "🔴 NONE (현재 매수 금지 / 주문 입력 불가 - BUY_BLOCKED)"
    else:
        order_exec_status = "🔴 NONE (현재 매수 금지 / 주문 입력 불가)"

    if is_buy_on and dto.technical_action in ("BUY_ALLOWED", "BUY_ALLOWED_CONDITIONAL", "BUY_WAIT"):
        section_3 = f"""3. ⚙️ ATR V4 트레일링 및 3단계 분할매매 시뮬레이션 파라미터 (PREVIEW ONLY)
   • 현재 실행 가능 주문: {order_exec_status}
   ⚠️ 본 수치는 매수 전 시뮬레이션(PREVIEW)이며, 실제 P0/A0/S0 및 2·3차 주문선은 1차 체결 후 확정됩니다.
   🔹 [3단계 분할매수 가이드 (50% / 30% / 20%)]:
      - 1차 최초 진입 (50%): 매수 승인 시 진입 (현재가 {dto.candidate_reference_price:,}원 부근)
      - 2차 추가 매수 (30%): -1.5 ATR 눌림목({dto.candidate_buy_price:,}원) 도달 후 최저가 대비 +{dto.buy_rebound_delta:,}원 반등 시 발동
      - 3차 상승확인 추매 (20%): P0 또는 주요 고점 회복·돌파 확인 시 발동
   🔹 [트레일링 익절 설정 (NORMAL 모드 추세추적)]:
      - 트레일링 활성 기준가: {dto.candidate_target_price:,}원 (+3.0 ATR 도달 시 활성화, 지정가 매도 아님)
      - 추락 청산 발동 조건: 활성화 후 최고가 대비 -{dto.sell_drop_delta:,}원 하락 이탈 시 전량 트레일링 청산
      - (참고: 부분 선익절 25~30%는 RISK_LOCK 수동 예외 설정 시에만 적용)
   🔹 [스탑로스 손절 설정 (초기손절 S0 = Entry - 1.5×A0)]:
      - 시뮬레이션 초기손절가: {dto.candidate_stop_price:,}원 (1.5 ATR 손절선 이탈 시)
      - 손절 발동 조건: {dto.candidate_stop_price:,}원 이하로 하락/이탈 시 즉시 발동 (보유 수량 100% 전량 손절)"""
    else:
        section_3 = f"""3. ⚙️ ATR V4 트레일링 및 3단계 분할매매 시뮬레이션 파라미터 (REFERENCE ONLY / 실제 주문 입력 금지)
   • 현재 실행 가능 주문: {order_exec_status}
   ⚠️ 본 종목은 매수 비승인 또는 진입 대기 상태입니다. 아래 수치는 단순 참고용(REFERENCE ONLY)이며 실제 주문을 설정하지 마십시오.
   🔹 [3단계 분할매수 가이드 (참고용)]:
      - 1차 최초 진입 (50%): [실행 불가 / 진입 대기] (참고 기준가: {dto.candidate_reference_price:,}원)
      - 2차 추가 매수 (30%): [참고선] -1.5 ATR 눌림목({dto.candidate_buy_price:,}원) / 반등폭 +{dto.buy_rebound_delta:,}원
      - 3차 상승확인 추매 (20%): [참고선] P0 또는 주요 고점 회복·돌파 시
   🔹 [트레일링 익절 설정 (참고용)]:
      - 트레일링 활성 기준가: [참고선] {dto.candidate_target_price:,}원 (+3.0 ATR 도달 시 활성화)
      - 추락 청산 발동 조건: [참고선] 최고가 대비 -{dto.sell_drop_delta:,}원 하락 이탈 시
   🔹 [스탑로스 손절 설정 (참고용)]:
      - 시뮬레이션 손절가: [참고선] {dto.candidate_stop_price:,}원 (1.5 ATR 손절선)
      - 손절 발동 조건: [참고선] {dto.candidate_stop_price:,}원 이하로 하락/이탈 시"""

    # 2. Technical Action 기반 Gemini 질문 프롬프트 자동 분기
    if dto.technical_action == "BUY_ALLOWED":
        prompt_instruction = """💡 Gemini Gems 사용 방법:
위 데이터 블록 전체를 복사하여 Gemini Gems 챗봇 질문창에 붙여넣으신 후
"이 종목의 키움 트레일링 매수/매도 설정가와 수량 비중 가이드를 요약해 줘" 라고 질문하세요!"""
    elif dto.technical_action == "BUY_ALLOWED_CONDITIONAL":
        prompt_instruction = """💡 Gemini Gems 사용 방법:
위 데이터 블록 전체를 복사하여 Gemini Gems 챗봇 질문창에 붙여넣으신 후
"이 종목의 조건부 매수 승인 조건과 키움 분할매수/트레일링 설정 가이드를 요약해 줘" 라고 질문하세요!"""
    elif dto.technical_action in ("BUY_WAIT", "BUY_WAIT_DATA"):
        prompt_instruction = """💡 Gemini Gems 사용 방법:
위 데이터 블록 전체를 복사하여 Gemini Gems 챗봇 질문창에 붙여넣으신 후
"이 종목의 1차 진입 대기(BUY_WAIT) 사유와 향후 진입 승인 전환을 위한 분봉/수급 재확인 조건을 분석해 줘" 라고 질문하세요!"""
    else:  # BUY_BLOCKED 및 기타
        prompt_instruction = """💡 Gemini Gems 사용 방법:
위 데이터 블록 전체를 복사하여 Gemini Gems 챗봇 질문창에 붙여넣으신 후
"이 종목이 매수 차단(BUY_BLOCKED)된 구체적 사유와 향후 매수 승인 전환을 위한 재평가 조건을 분석해 줘" 라고 질문하세요!"""

    tech_summary_str = f" ({dto.technical_gate_summary})" if dto.technical_gate_summary else ""
    daily_cho_str = str(dto.daily_cho_recent2) if dto.daily_cho_recent2 is not None else "N/A"
    intraday_cho_str = str(dto.intraday_cho_recent2) if dto.intraday_cho_recent2 is not None else "N/A"
    daily_adx_str = dto.daily_adx_di_dominance if dto.daily_adx_di_dominance and dto.daily_adx_di_dominance != "-" else dto.adx_di_dominance

    provenance_info = f" [출처: {dto.intraday_source} | {dto.intraday_row_count}봉]" if dto.intraday_source != "NONE" else ""
    evidence_bullets_str = "\n".join([f"     - {b}" for b in dto.fundamental_evidence_bullets]) if dto.fundamental_evidence_bullets else "     - 분기 실적 근거 데이터 수집 완료"

    # Phase 5: 최근 중요 공시 포맷팅
    if dto.recent_key_disclosures:
        recent_disc_str = "\n".join([f"     - {d}" for d in dto.recent_key_disclosures])
    else:
        recent_disc_str = "     - 최근 특이 수시공시 없음"

    report = f"""================================================================================
🤖 [Gemini Gems 전용 정밀 진단 프롬프트 데이터] 
종목명: {dto.stock_name} ({dto.stock_code}) | 수집시각: {dto.collected_at}
================================================================================

0. 🛰️ Weekly Industry Radar & Integrated Shadow Matrix (Phase 7 Shadow Journal & Outcome Attribution)
   • 소속 산업 Profile: {dto.industry_profile} (Score: {dto.industry_score:.1f}점 | Bucket: {dto.industry_bucket} | Gate: {dto.industry_gate} | Confidence: {dto.industry_confidence})
   • Evidence Provenance QA: LiveFetched {dto.live_fetched_pct:.1f}% | RefVerified {dto.reference_verified_pct:.1f}% | InternalDerived {dto.internal_derived_pct:.1f}% | Manual {dto.manual_evidence_pct:.1f}% | Synthetic {dto.synthetic_evidence_pct:.1f}% | ReplayVerified {dto.replay_verified_pct:.1f}% | Drivers: {dto.driver_count}개 (QA: {dto.qa_status})
   • 기업 Exposure Type: {dto.exposure_type} (Mapping Ver: {dto.mapping_version})
   • Industry vs Forward 교차검증: {dto.cross_validation_state}
   • 🌐 Multi-Layer Shadow Integrated State: [{dto.shadow_integrated_state}] (Shadow Mode - 기존 매수판정 불변)
     └ Primary Blocker: {dto.primary_blocker} | All Blockers: {", ".join(dto.all_blockers) if dto.all_blockers else "NONE"}
   • 📜 Shadow Scan Journal: {dto.journal_id} (Canonical Status: {dto.canonical_consistency_status})

1. 📈 키움 REST & 실시간 시세 (Technical Analysis)
   • 현재가: {dto.current_price:,}원 (당일 등락률: {dto.daily_change_pct:+.2f}%)
   • 14일 ATR (변동폭): {dto.atr_14:,.0f}원 ({dto.atr_pct:.2f}%)
   • 기술 T점수 (100점 만점): {dto.t_score:.1f}점

2. ⏱️ 5단계 매매 대응전략 원자값 연동 지표 (5-Tier Priority Strategy Rules)
   • 일봉 OBV (데드발생일자): {dto.obv_dead_date}
   • 일봉 Chaikin(13,26) 최근 2봉: {daily_cho_str}
   • 일봉 ADX +DI/-DI 우세방향: {daily_adx_str}
   • 45분봉 OBV 추세: {dto.obv_45m_trend}
   • 45m Chaikin(13,26) 최근 2봉: {intraday_cho_str}
   • 45m ADX +DI/-DI: {dto.intraday_adx_di_dominance}
   • 45분봉 데이터 품질: {dto.intraday_data_quality}{provenance_info}
   • 45분봉 Technical State: {dto.technical_state}{tech_summary_str}
   • 45분봉 Technical Gate: {dto.technical_action}

{section_3}

4. 🏢 OpenDART 2025년 공시 재무 & 8분기 Fundamental Evidence Layer
   • 공시 보고서 종류: 2025년 사업보고서 ({dto.fs_div} 연결/별도)
   • 2025년 당기 매출액: {dto.revenue/1e8:,.1f}억 원 (전기: {dto.prev_revenue/1e8:,.1f}억 원)
   • 2025년 당기 영업이익: {dto.operating_profit/1e8:,.1f}억 원 (전기: {dto.prev_operating_profit/1e8:,.1f}억 원)
   • 2025년 당기 영업현금흐름(OCF): {dto.operating_cash_flow/1e8:,.1f}억 원 (전기: {dto.prev_operating_cash_flow/1e8:,.1f}억 원)
   • 당기 부채비율(%) vs 전기 부채비율(%): {dto.debt_ratio:.2f}% vs {dto.prev_debt_ratio:.2f}% (BS 원천 실산출)
   • DART Sanity 세부 검증: {dto.sanity_flag}
   • 기본 F점수 세부 5대 항목 (100점 만점): {dto.f_score:.1f}점
     └ ① 성장성(25점): {dto.growth_pts:.1f}점 | ② 현금흐름(20점): {dto.cf_pts:.1f}점
     └ ③ 모멘텀(20점): {dto.cat_pts:.1f}점 | ④ 재무안정(20점): {dto.debt_pts:.1f}점 | ⑤ 밸류경영(15점): {dto.gov_pts:.1f}점

   📊 [Phase 4 & 4.1: 최근 8개 분기 정밀 구조화 재무 시계열 및 턴어라운드 진단]
   • 8분기 재무 기준일 (Period End): {dto.fiscal_period_end} ({dto.fiscal_period_age_days}일 경과) | 공시접수일 (Filing Date): {dto.filing_received_date} ({dto.filing_age_days}일 경과)
   • 최신 분기 및 데이터 품질: {dto.latest_fiscal_quarter} | {dto.quarterly_data_quality}
   • Fundamental Evidence State: {dto.fundamental_state}
   • Turnaround 분류: {dto.turnaround_type} ({dto.turnaround_label})
   • 실적 품질 경고 (Warnings): {", ".join(dto.fundamental_warnings) if dto.fundamental_warnings else "NONE (경고 없음)"}
   • 핵심 실적 근거 (Evidence):
{evidence_bullets_str}

   📋 [최근 8분기 분기별 실적 추이 요약표 (De-cumulated Discrete Quarters)]:
{dto.quarterly_summary_table if dto.quarterly_summary_table else '분기 데이터 수집 대기'}

5. 🔭 Forward Order / Disclosure Evidence (Forward 실적 가시성 및 수주·공시 계층)
   • 산업 Profile: {dto.industry_profile}
   • Forward Opportunity State (기회/성장): {dto.forward_opportunity_state} (Confidence: {dto.opportunity_confidence})
   • Forward Risk State (위험/희석): {dto.forward_risk_state} (Override: {dto.forward_risk_override_tag})
   • 최근 중요 공시 (Recent Disclosures - 최신 정정본 기준):
{recent_disc_str}
   • 수주잔고 (Order Backlog): {dto.order_backlog_summary}
   • 신규수주 (New Orders): {dto.new_orders_summary}
   • Book-to-Bill (수주배율): {dto.book_to_bill_summary}
   • CAPA / 설비투자 가시성: {dto.capa_summary}
   • 계약 진행단계 (Progression): {dto.progression_stage_summary}
   • 부정적 이벤트 내역 (Negative Events): {dto.negative_events_summary}

6. ⚖️ 100점 만점 가중 종합점수 & 5단계 매수 승인 최종 판정
   • 가중 종합점수: {dto.final_score:.1f}점 = (F점수 {dto.f_score:.1f} × 0.4) + (T점수 {dto.t_score:.1f} × 0.6)
   • 신규/추가 매수 승인 여부: {dto.buy_approval}
   • 5단계 Decision Matrix 최종 대응 전략: [{dto.action_strategy}]

================================================================================
{prompt_instruction}
================================================================================
"""
    return report

def render_multi_gems_markdown(dtos: List[ScanResultDTO], collected_at: str = None) -> str:
    """
    여러 종목의 ScanResultDTO 리스트를 하나의 통합 마크다운 문자열로 결합합니다.
    """
    if not collected_at:
        collected_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S KST')

    results = []
    header = f"""================================================================================
🤖 [Gemini Gems 전용] 관심 종목 {len(dtos)}개 정밀 진단 통합 리포트
수집 시각: {collected_at}
================================================================================
"""
    results.append(header)
    for idx, dto in enumerate(dtos, 1):
        stock_label = f"{dto.stock_name} ({dto.stock_code})" if dto.stock_name != dto.stock_code else dto.stock_code
        results.append(f"\n--- [종목 {idx}/{len(dtos)}: {stock_label}] ---\n" + render_gems_markdown(dto))

    footer = """================================================================================
💡 사용 방법:
이 파일 내용 전체를 복사하여 구글 Gemini Gems 챗봇 질문창에 붙여넣으신 후
"위 종목들의 매수 승인 여부와 6대 안전조건 충족 평가를 각각 요약해 줘" 라고 질문하세요!
================================================================================
"""
    results.append(footer)
    return "\n".join(results)

def render_multi_gems_json(dtos: List[ScanResultDTO], indent: int = 2) -> str:
    """
    여러 종목의 ScanResultDTO 리스트를 JSON 배열 문자열로 직렬화합니다.
    """
    data = [dto.to_dict() for dto in dtos]
    return json.dumps(data, ensure_ascii=False, indent=indent)
