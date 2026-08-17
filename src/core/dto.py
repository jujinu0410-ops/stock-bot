from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
import json

@dataclass
class ScanResultDTO:
    """
    관심종목 진단 및 스캔 결과의 정형화된 데이터 전송 객체 (DTO).
    - Watchlist 감시 기준은 실제 매수 체결 전이므로 P0/A0가 아닌 candidate_reference_price / candidate_reference_atr로 명명
    - DTO -> JSON -> Markdown Formatter -> Clipboard/TXT 파이프라인의 데이터 원천
    """
    # 1. 종목 기본 정보
    stock_code: str
    stock_name: str
    collected_at: str
    is_etf: bool = False

    # 2. 시장 시세 및 기술적 지표 (Technical Analysis)
    current_price: int = 0
    daily_change_pct: float = 0.0
    atr_14: float = 0.0
    atr_pct: float = 0.0
    t_score: float = 0.0
    t_raw: float = 0.0

    # 3. 매수 전 감시 기준 및 후보 가격선 (Pre-entry Watchlist Candidate References)
    candidate_reference_price: int = 0
    candidate_reference_atr: float = 0.0
    candidate_buy_price: int = 0
    candidate_target_price: int = 0
    candidate_stop_price: int = 0
    buy_rebound_delta: int = 0
    sell_drop_delta: int = 0

    # 4. 5단계 대응전략 및 전술 지표 (Tactical / 45m Indicators & Technical Gate)
    obv_dead_date: str = "N/A"
    obv_45m_trend: str = "N/A"
    daily_cho_recent2: Optional[List[int]] = field(default_factory=lambda: [0, 0])
    intraday_cho_recent2: Optional[List[int]] = None
    daily_adx_di_dominance: str = "-"
    intraday_adx_di_dominance: str = "-"
    adx_di_dominance: str = "-"  # 하위호환용
    intraday_data_quality: str = "🔴 INVALID (분봉 데이터 미수집)"
    intraday_source: str = "NONE"
    intraday_row_count: int = 0
    intraday_last_timestamp: str = "N/A"
    intraday_error_code: str = "NO_INTRADAY_DATA"
    technical_state: str = "UNKNOWN"
    technical_action: str = "BUY_BLOCKED"
    technical_gate_summary: str = ""
    technical_reason: str = ""

    # 5. DART 재무 및 기본적 지표 (Fundamental Analysis & Evidence Layer)
    fiscal_year: int = 2025
    fs_div: str = "CFS"
    revenue: float = 0.0
    prev_revenue: float = 0.0
    operating_profit: float = 0.0
    prev_operating_profit: float = 0.0
    operating_cash_flow: float = 0.0
    prev_operating_cash_flow: float = 0.0
    debt_ratio: float = 0.0
    prev_debt_ratio: float = 0.0
    sanity_flag: str = "PASS"
    f_score: float = 0.0
    growth_pts: float = 0.0
    cf_pts: float = 0.0
    cat_pts: float = 0.0
    debt_pts: float = 0.0
    gov_pts: float = 0.0

    # Phase 4 & 4.1: 8분기 구조화 재무 시계열 및 턴어라운드 Evidence Layer & Freshness
    fiscal_period_end: str = "N/A"
    fiscal_period_age_days: int = 0
    filing_received_date: str = "N/A"
    filing_age_days: int = 0
    financial_data_asof: str = "N/A"
    latest_fiscal_quarter: str = "N/A"
    quarterly_data_age_days: int = 0
    quarterly_data_quality: str = "VALID"
    fundamental_state: str = "UNKNOWN"
    turnaround_type: str = "F"
    turnaround_label: str = "INSUFFICIENT_EVIDENCE"
    high_quality_improvement: bool = False
    operating_leverage: bool = False
    fundamental_warnings: List[str] = field(default_factory=lambda: ["NONE (실적품질 경고 없음)"])
    fundamental_evidence_bullets: List[str] = field(default_factory=list)
    quarterly_summary_table: str = ""
    fundamental_disagreement: bool = False
    order_backlog_status: str = "NOT_COLLECTED"

    # Phase 5 & 5.1: Disclosure & Order Visibility Layer (Forward Evidence Calibration)
    industry_profile: str = "GENERAL_MANUFACTURING"
    forward_opportunity_state: str = "UNKNOWN"
    opportunity_confidence: str = "UNKNOWN"
    forward_risk_state: str = "NONE"
    forward_state: str = "UNKNOWN"
    has_forward_risk_override: bool = False
    forward_risk_override_tag: str = "NONE"
    recent_key_disclosures: List[str] = field(default_factory=list)
    order_backlog_summary: str = "NOT_DISCLOSED"
    new_orders_summary: str = "NOT_DISCLOSED"
    provisional_new_orders: Optional[float] = None
    is_unadjusted_bridge: bool = False
    book_to_bill_summary: str = "NOT_APPLICABLE"
    book_to_bill_period: str = "N/A"
    book_to_bill_status: str = "NOT_APPLICABLE"
    order_backlog_source_type: str = "UNKNOWN"
    new_orders_source_type: str = "UNKNOWN"
    confidence_level: str = "UNKNOWN"
    new_orders_confidence: str = "UNKNOWN"
    capa_summary: str = "특이 설비투자 공시 없음"
    capa_stage: str = "N/A"
    progression_stage_summary: str = "N/A"
    negative_events_summary: str = "NONE (위험 공시 없음)"
    forward_lineage: Dict[str, Any] = field(default_factory=dict)

    # Phase 6, 6.1, 6.2 & 6.3: Weekly Industry Radar & Integrated Shadow Matrix Layer
    industry_score: float = 0.0
    industry_gate: str = "INDUSTRY_WAIT"
    industry_bucket: str = "WATCH"
    industry_confidence: str = "UNKNOWN"
    exposure_type: str = "UNKNOWN"
    mapping_version: str = "V1.0"
    cross_validation_state: str = "UNKNOWN"
    shadow_integrated_state: str = "DATA_REVIEW"
    primary_blocker: str = "NONE"
    all_blockers: List[str] = field(default_factory=list)
    shadow_matrix_lineage: Dict[str, Any] = field(default_factory=dict)
    verified_evidence_pct: float = 100.0
    live_fetched_pct: float = 0.0
    reference_verified_pct: float = 0.0
    internal_derived_pct: float = 0.0
    manual_evidence_pct: float = 0.0
    synthetic_evidence_pct: float = 0.0
    fresh_evidence_pct: float = 100.0
    replay_verified_pct: float = 100.0
    replay_failed_pct: float = 0.0
    driver_count: int = 0
    evidence_count: int = 0
    qa_status: str = "QA_PASSED"

    # Phase 7: Shadow Scan Journal & Outcome Attribution Layer
    journal_id: str = ""
    canonical_consistency_status: str = "CONSISTENT"

    # 6. 종합 점수 및 의사결정 판정 (Decision & Action)
    final_score: float = 0.0
    buy_approval: str = "🔴 OFF (매수 금지/관망)"
    action_strategy: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """DTO를 Python dictionary로 직렬화"""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """DTO를 JSON 문자열로 직렬화"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScanResultDTO":
        """Dictionary로부터 ScanResultDTO 인스턴스 역직렬화"""
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_json(cls, json_str: str) -> "ScanResultDTO":
        """JSON 문자열로부터 ScanResultDTO 인스턴스 역직렬화"""
        data = json.loads(json_str)
        return cls.from_dict(data)
