import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Tuple, Dict, Any, Optional

# 프로젝트 루트 디렉토리 추가
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.database.db_manager import DatabaseManager
from src.api.kiwoom_api import KiwoomAPIClient
from src.api.real_market_api import RealMarketAPIClient
from src.api.dart_api import DartAPIClient
from src.engine.trading_engine import TradingEngine
from src.core.dto import ScanResultDTO
from src.formatters.gems_formatter import render_gems_markdown
from src.utils.logger import logger

def resolve_stock_code(stock_input: str) -> Tuple[str, str]:
    """종목명 또는 종목코드를 6자리 코드 및 정식 종목명으로 변환"""
    s = stock_input.strip()
    if len(s) == 6 and s.isdigit():
        return s, s

    stock_dict = {
        "삼성전자": "005930", "SK하이닉스": "000660", "현대차": "005380",
        "삼성바이오로직스": "207940", "삼바": "207940", "셀트리온": "068270", "알테오젠": "196170",
        "카카오": "035720", "NAVER": "035420", "네이버": "035420",
        "한화에어로스페이스": "012450", "대동": "000490", "한신공영": "004960",
        "한신기계": "011700", "하이록코리아": "013030", "두산에너빌리티": "034020",
        "포스코인터내셔널": "047050", "코데즈컴바인": "047770", "테이팩스": "055490",
        "알에스오토메이션": "140670", "유바이오로직스": "206650", "자이글": "234920", "디와이디": "219550",
        "DSC인베스트먼트": "241520", "HD현대일렉트릭": "267260", "뉴로메카": "348340",
        "PLUS 고배당주": "161510", "PLUS고배당주": "161510",
        "TIGER 차이나전기차": "371460", "TIGER차이나전기차": "371460",
        "RISE 미국AI밸류체인": "490590", "RISE미국AI밸류체인": "490590"
    }
    if s in stock_dict:
        return stock_dict[s], s

    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing('KRX')
        matched = df[df['Name'].str.replace(' ', '').str.lower() == s.replace(' ', '').lower()]
        if len(matched) > 0:
            return str(matched['Code'].iloc[0]).zfill(6), str(matched['Name'].iloc[0])
        
        contains = df[df['Name'].str.contains(s, case=False)]
        if len(contains) > 0:
            return str(contains['Code'].iloc[0]).zfill(6), str(contains['Name'].iloc[0])
    except Exception as e:
        logger.error(f"KRX 종목 자동검색 실패 ({s}): {e}")

    return s, s

def scan_stock_dto(stock_code_or_name: str) -> ScanResultDTO:
    """
    관심 종목 1개(또는 종목코드)에 대해 시세, DART 재무, 기술적 지표를 수집·분석하여
    정형화된 ScanResultDTO 인스턴스를 반환합니다.
    (Watchlist 매수 전 감시 기준이므로 candidate_reference_price / candidate_reference_atr 로 명명)
    """
    db = DatabaseManager()
    kiwoom = KiwoomAPIClient()
    market_api = RealMarketAPIClient()
    dart_api = DartAPIClient()
    engine = TradingEngine(db)

    code, resolved_name = resolve_stock_code(stock_code_or_name)
    db.upsert_stock_info({"stock_code": code, "stock_name": resolved_name, "market_type": "KOSPI/KOSDAQ"})

    logger.info(f"=== [Gemini Gems 전용] {resolved_name} ({code}) Kiwoom REST + DART 정밀 분석 시작 ===")

    # 1. 키움 REST API / RealMarket 시세 수집
    candles = market_api.get_real_daily_candles(code, count=60)
    if candles:
        db.insert_kiwoom_daily_batch(candles)
    
    # 2. OpenDART 정식 2025년 공시 수집 및 DB 저장
    dart_info = dart_api.get_financial_statement(code)
    if dart_info:
        db.upsert_dart_financials(dart_info)
    else:
        dart_info = {}

    # 3. TradingEngine 통합 분석
    res = engine.analyze_stock(code)
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S KST")

    if not res:
        return ScanResultDTO(
            stock_code=code,
            stock_name=resolved_name,
            collected_at=time_str,
            action_strategy=f"❌ 종목 코드 ({code})의 데이터를 조회할 수 없습니다."
        )

    # 데이터 추출
    name = res.get("stock_name", resolved_name)
    cur_p = int(res.get("latest_close", 0))
    daily_chg = float(res.get("daily_change_pct", 0.0))
    f_sc = float(res.get("f_score", 0.0))
    t_sc = float(res.get("t_score_converted", 0.0))
    t_raw = float(res.get("t_score_raw", t_sc))
    final_sc = float(res.get("final_score", 0.0))
    is_etf = bool(res.get("is_etf", False))
    
    # F점수 5대 세부 항목
    growth_pts = float(res.get("growth_pts", 0.0))
    ocf_pts = float(res.get("cf_pts", 0.0))
    momentum_pts = float(res.get("cat_pts", 0.0))
    debt_pts = float(res.get("stab_pts", 0.0))
    gov_pts = float(res.get("val_pts", 0.0))
    
    # DART 수치
    rev_val = float(dart_info.get("revenue", 0.0))
    prev_rev = float(dart_info.get("prev_revenue", 0.0))
    op_val = float(dart_info.get("operating_profit", 0.0))
    prev_op = float(dart_info.get("prev_operating_profit", 0.0))
    ocf_val = float(dart_info.get("operating_cash_flow", 0.0))
    prev_ocf = float(dart_info.get("prev_operating_cash_flow", 0.0))
    debt_ratio = float(dart_info.get("debt_ratio", 0.0))
    prev_debt_ratio = float(dart_info.get("prev_debt_ratio", 0.0))
    fs_div = str(dart_info.get("fs_div", "CFS"))
    sanity_flag = str(dart_info.get("sanity_detail_flag", "PASS"))

    # ATR 및 가격 가이드 (매수 전 감시 후보선 - ATRRiskEngine Single Source of Truth)
    atr_v = float(res.get("atr_14", 0.0))
    atr_pct = float(res.get("atr_pct", 0.0))
    t_buy = int(res.get("kiwoom_buy_tick_price", 0))
    t_stop = int(res.get("kiwoom_stop_tick_price", 0))
    t_target = int(res.get("kiwoom_target_tick_price", 0))

    cand_ref_p = int(res.get("candidate_reference_price", cur_p))
    cand_ref_atr = float(res.get("candidate_reference_atr", atr_v))
    rebound_delta = int(res.get("buy_rebound_delta") or (round(atr_v * 0.5) if atr_v > 0 else 0))
    drop_delta = int(res.get("sell_drop_delta") or (round(atr_v * 0.8) if atr_v > 0 else 0))
    # 일봉 및 45분봉 원자값 추출
    daily_cho = res.get("daily_cho_recent2", [0, 0])
    daily_adx_dom = str(res.get("daily_adx_di_dominance", "-"))
    obv_dead_d = str(res.get("obv_dead_date", "N/A"))

    intraday_quality = str(res.get("intraday_quality", "🔴 INVALID (분봉 데이터 미수집)"))
    intraday_source = str(res.get("intraday_source", "NONE"))
    intraday_row_cnt = int(res.get("intraday_row_count", 0))
    intraday_last_ts = str(res.get("intraday_last_timestamp", "N/A"))
    intraday_err_cd = str(res.get("intraday_error_code", "NO_INTRADAY_DATA"))

    intraday_cho = res.get("intraday_cho_recent2")
    intra_adx_dom = str(res.get("adx_di_dominance_45m", "-"))
    obv_trend = str(res.get("obv_45m_trend", "N/A"))

    # 매수 승인 판정 및 상태
    act_st = str(res.get("reason", "보유"))
    if final_sc >= 70.0 and f_sc >= 65.0 and t_sc >= 60.0 and daily_chg < 5.0:
        buy_approval = "🔵 ON (트레일링/눌림목 분할매수 승인)"
        act_st = f"우수한 펀더멘탈({f_sc:.1f}점)과 기술추세({t_sc:.1f}점)를 갖춘 우량 종목으로, 당일 과열 폭등 없는 안정 구간({daily_chg:+.2f}%)입니다. 1차 최초 진입(50%) 및 2차 -1.5 ATR 눌림목 반등(30%) 분할 매수 진입이 매우 합리적입니다."
    elif "제한적 분할추매 고려" in act_st:
        buy_approval = "🔵 ON (제한적 매수 승인)"
    else:
        buy_approval = "🔴 OFF (매수 금지/관망)"

    # Phase 3: 45분봉 Technical State 및 Gate 판정
    from src.analysis.technical_gate import TechnicalGate
    is_ft_approved = ("🔵 ON" in buy_approval)

    adx_val = float(res.get("adx_14_45m", 0.0) or 0.0) if res.get("adx_14_45m") is not None else 0.0
    plus_di = float(res.get("plus_di_45m", 0.0) or 0.0) if res.get("plus_di_45m") is not None else 0.0
    minus_di = float(res.get("minus_di_45m", 0.0) or 0.0) if res.get("minus_di_45m") is not None else 0.0
    is_obv_dead = bool(res.get("is_obv_dead", False))
    chaikin_val = float(res.get("chaikin_osc_45m", 0.0) or 0.0) if res.get("chaikin_osc_45m") is not None else 0.0
    is_cho_outflow = bool(res.get("is_cho_outflow", False))
    is_cloud_breakdown = bool(res.get("is_45m_breakdown", False))

    tech_state_res = TechnicalGate.evaluate_technical_state(
        data_quality=intraday_quality,
        adx_14=adx_val,
        plus_di=plus_di,
        minus_di=minus_di,
        obv_trend_str=obv_trend,
        is_obv_dead=is_obv_dead,
        chaikin_val=chaikin_val,
        intraday_cho_recent2=intraday_cho if intraday_cho else [0, 0],
        is_cho_outflow=is_cho_outflow,
        is_cloud_breakdown=is_cloud_breakdown
    )

    tech_gate_res = TechnicalGate.evaluate_new_buy_gate(
        is_ft_approved=is_ft_approved,
        data_quality=intraday_quality,
        tech_state=tech_state_res["technical_state"],
        reasons_list=tech_state_res["reasons"]
    )

    # 4. Phase 4 & 4.1: 최근 8개 분기 동적 탐색 및 턴어라운드 Evidence 분석
    from src.api.quarterly_dart_collector import QuarterlyDartCollector
    from src.analysis.fundamental_evidence_scanner import FundamentalEvidenceScanner
    
    q_collector = QuarterlyDartCollector(db, dart_api)
    q_res = q_collector.collect_8quarters_financials(code)
    q_records = q_res.get("records", [])
    
    q_scanner = FundamentalEvidenceScanner()
    fund_evidence = q_scanner.evaluate_evidence(
        quarterly_records=q_records,
        stock_code=code,
        data_age_days=q_res.get("fiscal_period_age_days", 0),
        full_time_series=q_res.get("all_records")
    )

    # 5. Phase 5 & 5.2: Disclosure & Order Visibility Layer (Forward Evidence Integrity)
    from src.api.disclosure_collector import DisclosureCollector
    from src.analysis.forward_visibility_engine import ForwardVisibilityEngine

    disc_collector = DisclosureCollector(db, dart_api)
    disc_collector.collect_disclosures(code)

    latest_q = q_records[-1] if q_records else {}
    q_rev = latest_q.get("revenue", 0.0)
    q_lbl = f"{latest_q.get('fiscal_year', 2026)} {latest_q.get('fiscal_quarter', 'Q2')}"

    fwd_engine = ForwardVisibilityEngine(db)
    fwd_res = fwd_engine.evaluate_forward_visibility(
        stock_code=code,
        stock_name=name,
        quarterly_revenue=q_rev,
        annual_revenue=rev_val,
        quarter_label=q_lbl,
        period_start="2026-04-01",
        period_end="2026-06-30"
    )

    # 6. Phase 6: Weekly Industry Radar & Integrated Shadow Matrix Layer
    from src.analysis.industry_radar_engine import IndustryRadarEngine

    ind_engine = IndustryRadarEngine(db)
    ind_profile = ind_engine.get_industry_profile_for_stock(code, name)

    cross_val_state = ind_engine.cross_validate_industry_forward(
        industry_gate=ind_profile["industry_gate"],
        forward_opp_state=fwd_res.get("forward_opportunity_state", "UNKNOWN")
    )

    shadow_res = ind_engine.synthesize_shadow_state(
        industry_gate=ind_profile["industry_gate"],
        industry_score=ind_profile["total_score"],
        exposure_type=ind_profile["exposure_type"],
        fundamental_state=fund_evidence["fundamental_state"],
        f_score=f_sc,
        forward_opp_state=fwd_res.get("forward_opportunity_state", "UNKNOWN"),
        forward_risk_state=fwd_res.get("forward_risk_state", "NONE"),
        technical_action=tech_gate_res["technical_action"],
        atr_mode=act_st
    )
    shadow_state = shadow_res["shadow_integrated_state"]
    primary_blocker = shadow_res["primary_blocker"]
    all_blockers = shadow_res["all_blockers"]

    shadow_lineage = {
        "industry": f"{ind_profile['industry_gate']} / {ind_profile['total_score']:.1f}점 / {ind_profile['industry_confidence']}",
        "exposure_type": ind_profile["exposure_type"],
        "mapping_version": ind_profile.get("mapping_version", "V1.0"),
        "fundamental": fund_evidence["fundamental_state"],
        "forward_opportunity": f"{fwd_res.get('forward_opportunity_state', 'UNKNOWN')} / {fwd_res.get('opportunity_confidence', 'UNKNOWN')}",
        "forward_risk": fwd_res.get("forward_risk_state", "NONE"),
        "technical": f"{tech_gate_res['technical_action']} (State: {tech_state_res['technical_state']})",
        "atr_risk": act_st,
        "cross_validation": cross_val_state,
        "shadow_integrated_state": shadow_state,
        "primary_blocker": primary_blocker,
        "all_blockers": all_blockers
    }

    # 기존 F 점수와 신규 Evidence Layer 불일치 태깅 (점수 변경 금지, 검증용 태그만 부여)
    has_disagreement = (f_sc >= 70.0 and fund_evidence["fundamental_state"] in ["WEAKENING", "DISTRESSED"]) or \
                       (f_sc <= 55.0 and fund_evidence["fundamental_state"] in ["STRONG", "IMPROVING"])

    # Phase 7: Canonical Metric Consistency Audit
    from src.analysis.canonical_registry import CanonicalConsistencyChecker
    from src.analysis.outcome_evaluator import OutcomeEvaluator

    consistency_checker = CanonicalConsistencyChecker(db)
    consistency_res = consistency_checker.audit_stock_consistency(code)
    has_mismatch = any(c.get("consistency_status") == "MISMATCH" for c in consistency_res)
    consistency_status = "MISMATCH" if has_mismatch else ("SCOPE_DIFFERENTIATED" if any("SCOPE" in c.get("consistency_status", "") for c in consistency_res) else "CONSISTENT")

    # Generate deterministic immutable journal_id
    ts_clean = time_str.replace("-", "").replace(":", "").replace(" ", "_").replace("KST", "").strip()
    trading_d = time_str.split(" ")[0] if " " in time_str else time_str[:10]
    journal_id = f"JRN_{ts_clean}_{code}"

    # Build and insert immutable journal snapshot
    journal_entry = {
        "journal_id": journal_id,
        "scan_timestamp": time_str,
        "trading_date": trading_d,
        "snapshot_reason": "DAILY_FIRST",
        "stock_code": code,
        "stock_name": name,
        "market_price": cur_p,
        "industry_run_id": ind_profile.get("run_id", "PROD_2026_W33_001"),
        "industry_score": ind_profile["total_score"],
        "industry_gate": ind_profile["industry_gate"],
        "industry_confidence": ind_profile["industry_confidence"],
        "exposure_type": ind_profile["exposure_type"],
        "mapping_version": ind_profile.get("mapping_version", "V1.0"),
        "fundamental_state": fund_evidence["fundamental_state"],
        "turnaround_type": fund_evidence["turnaround_type"],
        "turnaround_label": fund_evidence["turnaround_label"],
        "forward_opportunity": fwd_res.get("forward_opportunity_state", "UNKNOWN"),
        "forward_confidence": fwd_res.get("opportunity_confidence", "UNKNOWN"),
        "forward_risk": fwd_res.get("forward_risk_state", "NONE"),
        "forward_risk_override_tag": fwd_res.get("forward_risk_override_tag", "NONE"),
        "book_to_bill_summary": fwd_res.get("book_to_bill_summary", "NOT_APPLICABLE"),
        "t_score": t_sc,
        "technical_state": tech_state_res["technical_state"],
        "technical_action": tech_gate_res["technical_action"],
        "intraday_data_quality": intraday_quality,
        "atr14": atr_v,
        "natr": atr_pct,
        "candidate_ref_price": cand_ref_p,
        "candidate_stop_price": t_stop,
        "candidate_target_price": t_target,
        "atr_mode": act_st,
        "shadow_integrated_state": shadow_state,
        "primary_blocker": primary_blocker,
        "all_blockers": all_blockers,
        "existing_f_score": f_sc,
        "existing_final_score": final_sc,
        "buy_approval": buy_approval,
        "p0_status": "NONE",
        "position_cycle_id": "NONE",
        "financial_data_asof": q_res.get("financial_data_asof", "N/A"),
        "forward_data_asof": fwd_res.get("lineage", {}).get("as_of_date", "2026-08-17"),
        "intraday_last_timestamp": intraday_last_ts,
        "scoring_versions": {
            "industry": ind_profile.get("mapping_version", "V1.0"),
            "fundamental": "V1.0",
            "forward": "V1.0",
            "technical": "V4.0",
            "journal": "V1.0"
        }
    }
    db.insert_scan_journal(journal_entry)
    OutcomeEvaluator(db).evaluate_journal_outcome(journal_id)

    return ScanResultDTO(
        stock_code=code,
        stock_name=name,
        collected_at=time_str,
        is_etf=is_etf,
        current_price=cur_p,
        daily_change_pct=daily_chg,
        atr_14=atr_v,
        atr_pct=atr_pct,
        t_score=t_sc,
        t_raw=t_raw,
        candidate_reference_price=cand_ref_p,
        candidate_reference_atr=cand_ref_atr,
        candidate_buy_price=t_buy,
        candidate_target_price=t_target,
        candidate_stop_price=t_stop,
        buy_rebound_delta=rebound_delta,
        sell_drop_delta=drop_delta,
        obv_dead_date=obv_dead_d,
        obv_45m_trend=obv_trend,
        daily_cho_recent2=daily_cho,
        intraday_cho_recent2=intraday_cho,
        daily_adx_di_dominance=daily_adx_dom,
        intraday_adx_di_dominance=intra_adx_dom,
        adx_di_dominance=daily_adx_dom,
        intraday_data_quality=intraday_quality,
        intraday_source=intraday_source,
        intraday_row_count=intraday_row_cnt,
        intraday_last_timestamp=intraday_last_ts,
        intraday_error_code=intraday_err_cd,
        technical_state=tech_state_res["technical_state"],
        technical_action=tech_gate_res["technical_action"],
        technical_gate_summary=tech_state_res["state_summary"],
        technical_reason=str(res.get("reason", "")),
        fiscal_year=int(dart_info.get("fiscal_year", 2025)),
        fs_div=fs_div,
        revenue=rev_val,
        prev_revenue=prev_rev,
        operating_profit=op_val,
        prev_operating_profit=prev_op,
        operating_cash_flow=ocf_val,
        prev_operating_cash_flow=prev_ocf,
        debt_ratio=debt_ratio,
        prev_debt_ratio=prev_debt_ratio,
        sanity_flag=sanity_flag,
        f_score=f_sc,
        growth_pts=growth_pts,
        cf_pts=ocf_pts,
        cat_pts=momentum_pts,
        debt_pts=debt_pts,
        gov_pts=gov_pts,
        fiscal_period_end=q_res.get("fiscal_period_end", "N/A"),
        fiscal_period_age_days=q_res.get("fiscal_period_age_days", 0),
        filing_received_date=q_res.get("filing_received_date", "N/A"),
        filing_age_days=q_res.get("filing_age_days", 0),
        financial_data_asof=q_res.get("financial_data_asof", "N/A"),
        latest_fiscal_quarter=q_res.get("latest_fiscal_quarter", "N/A"),
        quarterly_data_age_days=q_res.get("quarterly_data_age_days", 0),
        quarterly_data_quality=q_res.get("quarterly_data_quality", "VALID"),
        fundamental_state=fund_evidence["fundamental_state"],
        turnaround_type=fund_evidence["turnaround_type"],
        turnaround_label=fund_evidence["turnaround_label"],
        high_quality_improvement=fund_evidence["high_quality_improvement"],
        operating_leverage=fund_evidence["operating_leverage"],
        fundamental_warnings=fund_evidence["warnings"],
        fundamental_evidence_bullets=fund_evidence["evidence_bullets"],
        quarterly_summary_table=fund_evidence["quarterly_summary_table"],
        fundamental_disagreement=has_disagreement,
        order_backlog_status=fwd_res.get("order_backlog_summary", "NOT_COLLECTED"),
        industry_profile=ind_profile.get("industry_id", "GENERAL_MANUFACTURING"),
        forward_opportunity_state=fwd_res.get("forward_opportunity_state", "UNKNOWN"),
        opportunity_confidence=fwd_res.get("opportunity_confidence", "UNKNOWN"),
        forward_risk_state=fwd_res.get("forward_risk_state", "NONE"),
        forward_state=fwd_res.get("forward_opportunity_state", "UNKNOWN"),
        has_forward_risk_override=fwd_res.get("has_forward_risk_override", False),
        forward_risk_override_tag=fwd_res.get("forward_risk_override_tag", "NONE"),
        recent_key_disclosures=fwd_res.get("recent_key_disclosures", []),
        order_backlog_summary=fwd_res.get("order_backlog_summary", "NOT_DISCLOSED"),
        new_orders_summary=fwd_res.get("new_orders_summary", "NOT_DISCLOSED"),
        provisional_new_orders=fwd_res.get("provisional_new_orders"),
        is_unadjusted_bridge=fwd_res.get("is_unadjusted_bridge", False),
        book_to_bill_summary=fwd_res.get("book_to_bill_summary", "NOT_APPLICABLE"),
        book_to_bill_period=fwd_res.get("book_to_bill_period", "N/A"),
        book_to_bill_status=fwd_res.get("book_to_bill_status", "NOT_APPLICABLE"),
        order_backlog_source_type=fwd_res.get("order_backlog_source_type", "UNKNOWN"),
        new_orders_source_type=fwd_res.get("new_orders_source_type", "UNKNOWN"),
        confidence_level=fwd_res.get("confidence_level", "UNKNOWN"),
        new_orders_confidence=fwd_res.get("new_orders_confidence", "UNKNOWN"),
        capa_summary=fwd_res.get("capa_summary", "특이 설비투자 공시 없음"),
        capa_stage=fwd_res.get("capa_stage", "N/A"),
        progression_stage_summary=fwd_res.get("progression_stage_summary", "N/A"),
        negative_events_summary=fwd_res.get("negative_events_summary", "NONE (위험 공시 없음)"),
        forward_lineage=fwd_res.get("lineage", {}),
        industry_score=ind_profile["total_score"],
        industry_gate=ind_profile["industry_gate"],
        industry_bucket=ind_profile["industry_bucket"],
        industry_confidence=ind_profile["industry_confidence"],
        exposure_type=ind_profile["exposure_type"],
        mapping_version=ind_profile.get("mapping_version", "V1.0"),
        cross_validation_state=cross_val_state,
        shadow_integrated_state=shadow_state,
        primary_blocker=primary_blocker,
        all_blockers=all_blockers,
        shadow_matrix_lineage=shadow_lineage,
        verified_evidence_pct=ind_profile.get("verified_evidence_pct", 100.0),
        live_fetched_pct=ind_profile.get("live_fetched_pct", 0.0),
        reference_verified_pct=ind_profile.get("reference_verified_pct", 0.0),
        internal_derived_pct=ind_profile.get("internal_derived_pct", 0.0),
        manual_evidence_pct=ind_profile.get("manual_evidence_pct", 0.0),
        synthetic_evidence_pct=ind_profile.get("synthetic_evidence_pct", 0.0),
        fresh_evidence_pct=ind_profile.get("fresh_evidence_pct", 100.0),
        replay_verified_pct=ind_profile.get("replay_verified_pct", 100.0),
        replay_failed_pct=ind_profile.get("replay_failed_pct", 0.0),
        driver_count=ind_profile.get("driver_count", 0),
        evidence_count=ind_profile.get("evidence_count", 0),
        qa_status=ind_profile.get("qa_status", "QA_PASSED"),
        journal_id=journal_id,
        canonical_consistency_status=consistency_status,
        final_score=final_sc,
        buy_approval=buy_approval,
        action_strategy=act_st
    )

def scan_stock_for_gems(stock_code_or_name: str) -> str:
    """
    관심 종목 1개(또는 종목코드)에 대해 DTO를 생성하고 Gemini Gems 마크다운 리포트로 렌더링합니다.
    (기존 호출부 완벽 하위 호환)
    """
    dto = scan_stock_dto(stock_code_or_name)
    return render_gems_markdown(dto)

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "대동"
    print(scan_stock_for_gems(target))
