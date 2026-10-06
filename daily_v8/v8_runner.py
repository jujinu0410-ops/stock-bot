"""
v8_runner.py — V8 BriefingBundle (Phase 2.5 — Full Data Expansion)

ScanResultDTO의 원자료를 최대한 활용하여 두꺼운 번들을 만든다.
존재하지 않는 field는 만들지 않는다.
"""

from __future__ import annotations
import importlib
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class V8BriefingBundle:
    # ── 기본 ────────────────────────────────────────────────────
    stock_code:   str
    stock_name:   str
    generated_at: str

    # ── 재무 원수치 ──────────────────────────────────────────────
    financial_summary: Dict[str, Any]       # F-Score 세부
    quarterly_8q_table: str                 # 8분기 마크다운 표
    prev_vs_current_summary: str            # 전기 대비 핵심 변화

    # ── OCF / 부채 ──────────────────────────────────────────────
    ocf_annual:   Optional[float]           # 연간 OCF (원)
    debt_ratio:   Optional[float]           # 현재 부채비율 %
    prev_debt_ratio: Optional[float]        # 전기 부채비율 %

    # ── 수주/성장 ────────────────────────────────────────────────
    order_backlog_summary: str
    new_orders_summary:    str
    forward_state:         str
    capa_summary:          str
    industry_gate:         str
    industry_score:        Optional[float]
    industry_profile:      str

    # ── DART 위험 ────────────────────────────────────────────────
    dart_risks: str

    # ── 기술적 분석 (일봉) ───────────────────────────────────────
    technical_summary: str          # action_strategy 전체 원문
    technical_action:  str          # BUY_ALLOWED_CONDITIONAL 등
    daily_adx_di:      str
    t_score:           Optional[float]
    t_raw:             Optional[float]

    # ── 45분봉 ──────────────────────────────────────────────────
    intraday_status:      str
    intraday_adx_di:      str
    obv_45m_trend:        str
    intraday_cho_recent2: Any
    intraday_data_quality: str

    # ── 시장 데이터 ──────────────────────────────────────────────
    current_price:    Optional[float]
    daily_change_pct: Optional[float]
    atr14:            Optional[float]
    atr_pct:          Optional[float]
    candidate_buy_price:    Optional[float]
    candidate_stop_price:   Optional[float]
    candidate_target_price: Optional[float]
    buy_rebound_delta:      Optional[float]
    sell_drop_delta:        Optional[float]

    # ── 업체 분류 ────────────────────────────────────────────────
    turnaround_label: str
    exposure_type:    str

    # ── 품질 경고 ────────────────────────────────────────────────
    quality_warnings: List[str]
    source_files:     List[str]
    intraday_last_timestamp: str = ""
    market_type: str = ""
    valuation_summary: str = "INSUFFICIENT_DATA (V8 source has no valuation)"
    research_summary: str = "INSUFFICIENT_DATA (V8 source has no research)"
    fundamental_evidence: List[str] = field(default_factory=list)
    bull_evidence: List[str] = field(default_factory=list)
    bear_evidence: List[str] = field(default_factory=list)


def resolve_v8_engine_path() -> Path:
    configured = os.environ.get("V8_ENGINE_PATH")
    path = Path(configured).resolve() if configured else Path(__file__).resolve().parents[1] / "stock_analysis_system"
    if not (path / "scan_stock_for_gems.py").is_file():
        raise FileNotFoundError(f"V8 engine is unavailable at {path}")
    return path


def run_v8_headless(stock_code: str) -> Optional[Any]:
    v8_path = resolve_v8_engine_path()
    if str(v8_path) not in sys.path:
        sys.path.insert(0, str(v8_path))

    try:
        if os.environ.get("V8_ENGINE_PATH"):
            scan_stock_dto = importlib.import_module("scan_stock_for_gems").scan_stock_dto
            settings = importlib.import_module("config.settings")
        else:
            scan_stock_dto = importlib.import_module("stock_analysis_system.scan_stock_for_gems").scan_stock_dto
            settings = importlib.import_module("stock_analysis_system.config.settings")

        if not settings.DART_API_KEY or settings.DART_API_KEY == "YOUR_DART_API_KEY_HERE":
            return "BLOCKED_MISSING_CREDENTIAL"
        if not settings.KIWOOM_APP_KEY or settings.KIWOOM_APP_KEY == "YOUR_KIWOOM_APP_KEY_HERE":
            return "BLOCKED_MISSING_CREDENTIAL"
        if not settings.KIWOOM_APP_SECRET or settings.KIWOOM_APP_SECRET == "YOUR_KIWOOM_APP_SECRET_HERE":
            return "BLOCKED_MISSING_CREDENTIAL"

        dto = scan_stock_dto(stock_code)

        # ── Quality warnings ─────────────────────────────────────
        warnings = []
        if getattr(dto, "intraday_error_code", None) and dto.intraday_error_code != "NONE":
            warnings.append(f"INTRADAY_ERROR: {dto.intraday_error_code}")
        if getattr(dto, "fundamental_warnings", None) and dto.fundamental_warnings not in ("NONE", None, ""):
            warnings.append(f"FUNDAMENTAL: {dto.fundamental_warnings}")
        if getattr(dto, "sanity_flag", None) and dto.sanity_flag not in ("OK", "", None):
            warnings.append(f"SANITY: {dto.sanity_flag}")
        if getattr(dto, "industry_gate", "") == "INDUSTRY_BLOCK":
            warnings.append("INDUSTRY_GATE: BLOCK — 산업 평균 하회")

        # ── Prev vs current ──────────────────────────────────────
        rev   = getattr(dto, "revenue", 0) or 0
        prev_rev = getattr(dto, "prev_revenue", 0) or 0
        op    = getattr(dto, "operating_profit", 0) or 0
        prev_op = getattr(dto, "prev_operating_profit", 0) or 0
        rev_yoy  = f"{(rev/prev_rev - 1)*100:+.1f}%" if prev_rev else "N/A"
        op_yoy   = f"{(op/prev_op   - 1)*100:+.1f}%" if prev_op  else "N/A"
        prev_summary = (
            f"매출 {int(rev/1e8):,}억 (YoY {rev_yoy}) | "
            f"영업이익 {int(op/1e8):,}억 (YoY {op_yoy}) | "
            f"OCF {int((getattr(dto, 'operating_cash_flow', 0) or 0)/1e8):,}억"
        )

        bundle = V8BriefingBundle(
            stock_code=dto.stock_code,
            stock_name=getattr(dto, "stock_name", stock_code),
            generated_at=getattr(dto, "collected_at", ""),
            financial_summary={
                "f_score":       getattr(dto, "f_score", 0),
                "cat_pts":       getattr(dto, "cat_pts", 0),
                "growth_pts":    getattr(dto, "growth_pts", 0),
                "cf_pts":        getattr(dto, "cf_pts", 0),
                "debt_pts":      getattr(dto, "debt_pts", 0),
                "gov_pts":       getattr(dto, "gov_pts", 0),
                "revenue":       rev,
                "operating_profit": op,
                "ocf":           getattr(dto, "operating_cash_flow", 0),
                "sanity_flag":   getattr(dto, "sanity_flag", ""),
                "fundamental_state": getattr(dto, "fundamental_state", ""),
            },
            quarterly_8q_table=getattr(dto, "quarterly_summary_table", ""),
            prev_vs_current_summary=prev_summary,
            ocf_annual=getattr(dto, "operating_cash_flow", None),
            debt_ratio=getattr(dto, "debt_ratio", None),
            prev_debt_ratio=getattr(dto, "prev_debt_ratio", None),
            order_backlog_summary=getattr(dto, "order_backlog_summary", "N/A"),
            new_orders_summary=getattr(dto, "new_orders_summary", "N/A"),
            forward_state=getattr(dto, "forward_state", "UNKNOWN"),
            capa_summary=getattr(dto, "capa_summary", "N/A"),
            industry_gate=getattr(dto, "industry_gate", "N/A"),
            industry_score=getattr(dto, "industry_score", None),
            industry_profile=getattr(dto, "industry_profile", "N/A"),
            dart_risks=getattr(dto, "negative_events_summary", "없음"),
            technical_summary=getattr(dto, "action_strategy", ""),
            technical_action=getattr(dto, "technical_action", ""),
            daily_adx_di=getattr(dto, "daily_adx_di_dominance", "N/A"),
            t_score=getattr(dto, "t_score", None),
            t_raw=getattr(dto, "t_raw", None),
            intraday_status=getattr(dto, "intraday_data_quality", "N/A"),
            intraday_adx_di=getattr(dto, "intraday_adx_di_dominance", "N/A"),
            obv_45m_trend=getattr(dto, "obv_45m_trend", "N/A"),
            intraday_cho_recent2=getattr(dto, "intraday_cho_recent2", None),
            intraday_data_quality=getattr(dto, "intraday_data_quality", "N/A"),
            current_price=getattr(dto, "current_price", None),
            daily_change_pct=getattr(dto, "daily_change_pct", None),
            atr14=getattr(dto, "atr_14", None),
            atr_pct=getattr(dto, "atr_pct", None),
            candidate_buy_price=getattr(dto, "candidate_buy_price", None),
            candidate_stop_price=getattr(dto, "candidate_stop_price", None),
            candidate_target_price=getattr(dto, "candidate_target_price", None),
            buy_rebound_delta=getattr(dto, "buy_rebound_delta", None),
            sell_drop_delta=getattr(dto, "sell_drop_delta", None),
            turnaround_label=getattr(dto, "turnaround_label", "N/A"),
            exposure_type=getattr(dto, "exposure_type", "N/A"),
            quality_warnings=warnings,
            source_files=["DART Open API", "Naver fchart", "yfinance (KRX)", "Kiwoom REST"],
            intraday_last_timestamp=getattr(dto, "intraday_last_timestamp", ""),
            market_type=getattr(dto, "market_type", ""),
            valuation_summary=getattr(dto, "valuation_summary", "INSUFFICIENT_DATA (V8 source has no valuation)"),
            research_summary=getattr(dto, "research_summary", "INSUFFICIENT_DATA (V8 source has no research)"),
            fundamental_evidence=getattr(dto, "fundamental_evidence_bullets", []),
            bull_evidence=getattr(dto, "bull_evidence", []),
            bear_evidence=getattr(dto, "bear_evidence", []),
        )
        return bundle

    except Exception as e:
        print(f"V8 Run Error: {type(e).__name__}")
        return None
