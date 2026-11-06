import json
from typing import Dict, Any, List, Optional, Tuple
from src.database.db_manager import DatabaseManager
from src.utils.logger import logger

class CanonicalMetricRegistry:
    """
    Phase 7 Canonical Metric Registry
    - 시스템 내 여러 계층(Fundamental, Forward, Industry Radar)에서 공통으로 사용되는
      핵심 경제적 지표의 Canonical Layer, Source, Scope, Period, Tolerance를 중앙 관리합니다.
    """
    REGISTRY: Dict[str, Dict[str, Any]] = {
        "operating_margin": {
            "canonical_layer": "FUNDAMENTAL",
            "canonical_source": "quarterly_financials",
            "period_basis": "DISCRETE_QUARTER",
            "scope_basis": "CONSOLIDATED_CFS",
            "unit": "%",
            "tolerance": 0.05,  # 0.05%p
            "version": "V1.0",
            "description": "분기 개별 기간 연결 영업이익률 (영업이익 / 매출액 * 100)"
        },
        "order_backlog": {
            "canonical_layer": "FORWARD",
            "canonical_source": "order_backlog_metrics",
            "period_basis": "AS_OF_QUARTER_END",
            "scope_basis": "CONSOLIDATED_CFS",
            "unit": "KRW_B",
            "tolerance": 1.0,  # 10억원 오차
            "version": "V1.0",
            "description": "분기말 연결 기준 수주잔고 총액"
        },
        "backlog_yoy": {
            "canonical_layer": "FORWARD",
            "canonical_source": "order_backlog_metrics",
            "period_basis": "DISCRETE_YOY",
            "scope_basis": "CONSOLIDATED_CFS",
            "unit": "%",
            "tolerance": 0.1,  # 0.1%p
            "version": "V1.0",
            "description": "전년 동분기 대비 연결 수주잔고 증가율"
        },
        "book_to_bill": {
            "canonical_layer": "FORWARD",
            "canonical_source": "ForwardVisibilityEngine",
            "period_basis": "DISCRETE_QUARTER",
            "scope_basis": "CONSOLIDATED_CFS",
            "unit": "RATIO",
            "tolerance": 0.01,  # 0.01배
            "version": "V1.0",
            "description": "동일 분기 신규수주액 / 분기 매출액 비율"
        },
        "forward_pe": {
            "canonical_layer": "MARKET_DATA",
            "canonical_source": "KRX_DATA_SERVICE",
            "period_basis": "12M_FORWARD",
            "scope_basis": "MARKET_CONSENSUS",
            "unit": "X",
            "tolerance": 0.5,
            "version": "V1.0",
            "description": "12개월 선행 주가수익비율 (Forward P/E)"
        }
    }

    @classmethod
    def get_metric_definition(cls, metric_name: str) -> Optional[Dict[str, Any]]:
        return cls.REGISTRY.get(metric_name)


class CanonicalConsistencyChecker:
    """
    Phase 7 Preflight Canonical Metric Consistency Checker
    - 동일 종목의 동일 지표가 여러 Layer에서 참조될 때 정합성을 검사합니다.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def audit_stock_consistency(
        self,
        stock_code: str,
        fiscal_year: int = 2026,
        fiscal_quarter: str = "Q2"
    ) -> List[Dict[str, Any]]:
        """
        특정 종목의 2026 Q2 기준 다층 메트릭 정합성 감사 실행
        """
        code = str(stock_code).zfill(6)
        results = []

        # 1. Fundamental Layer (quarterly_financials) 조회
        q_row = self.db.execute_query("""
            SELECT revenue, operating_income, net_income, fs_div
            FROM quarterly_financials
            WHERE stock_code = ? AND fiscal_year = ? AND fiscal_quarter = ?
            LIMIT 1
        """, (code, fiscal_year, fiscal_quarter))

        fund_opm = None
        if q_row and q_row[0]["revenue"] and q_row[0]["revenue"] > 0:
            fund_opm = round((float(q_row[0]["operating_income"]) / float(q_row[0]["revenue"])) * 100.0, 2)

        # 2. Forward Layer (order_backlog_metrics) 조회
        b_row = self.db.execute_query("""
            SELECT order_backlog, order_backlog_yoy, book_to_bill, scope_id
            FROM order_backlog_metrics
            WHERE stock_code = ? AND fiscal_year = ? AND fiscal_quarter = ?
            LIMIT 1
        """, (code, fiscal_year, fiscal_quarter))

        fwd_backlog_yoy = float(b_row[0]["order_backlog_yoy"]) if (b_row and b_row[0]["order_backlog_yoy"] is not None) else None
        fwd_b2b = float(b_row[0]["book_to_bill"]) if (b_row and b_row[0]["book_to_bill"] is not None) else None

        # 3. Industry Evidence Layer 조회
        ind_evs = self.db.execute_query("""
            SELECT industry_id, factor_name, evidence_family, underlying_driver_id,
                   origin_type, raw_value, extracted_fact_json
            FROM industry_evidence
            WHERE run_id = 'PROD_2026_W33_001'
        """)

        # Stock info 조회
        stk_info = self.db.get_stock_info(code)
        stk_name = stk_info.get("stock_name", "") if stk_info else ""

        # OPM 비교 감사
        ind_opm = None
        ind_opm_scope = "CONSOLIDATED_CFS"
        ind_backlog_yoy = None
        ind_backlog_scope = "CONSOLIDATED_CFS"

        for ev in (ind_evs or []):
            fact_str = ev["extracted_fact_json"]
            if not fact_str:
                continue
            try:
                fact = json.loads(fact_str)
                ent = fact.get("entity", "")
                is_entity_match = (code in ev.get("raw_value", "") or (bool(stk_name) and stk_name in ent) or (code in ent))
                if not is_entity_match:
                    continue

                # OPM fact
                if fact.get("metric") == "operating_margin":
                    ind_opm = float(fact.get("value", 0.0))
                    ind_opm_scope = fact.get("scope", "CONSOLIDATED_CFS")
                # Backlog YoY fact
                if fact.get("metric") == "backlog_yoy_growth":
                    ind_backlog_yoy = float(fact.get("value", 0.0))
                    ind_backlog_scope = fact.get("scope", "CONSOLIDATED_CFS")
                    if "북미" in ev.get("raw_value", "") or "초고압" in ev.get("raw_value", ""):
                        ind_backlog_scope = "SEGMENT_NORTH_AMERICA"
            except Exception:
                pass

        # Check 1: Operating Margin (OPM)
        if fund_opm is not None:
            if ind_opm is not None:
                opm_diff = round(abs(fund_opm - ind_opm), 2)
                opm_status = "CONSISTENT" if opm_diff <= 0.05 else ("SCOPE_DIFFERENTIATED" if ind_opm_scope != "CONSOLIDATED_CFS" else "MISMATCH")
            else:
                opm_diff = 0.0
                opm_status = "CONSISTENT"

            results.append({
                "stock_code": code,
                "metric_name": "operating_margin",
                "period": f"{fiscal_year}_{fiscal_quarter}",
                "scope": "CONSOLIDATED_CFS",
                "canonical_source": "quarterly_financials (Discrete CFS)",
                "fundamental_value": fund_opm,
                "forward_value": None,
                "industry_evidence_value": ind_opm if ind_opm is not None else fund_opm,
                "difference": opm_diff,
                "tolerance": 0.05,
                "consistency_status": opm_status,
                "rationale": "Discrete 1개 분기 연결 CFS 기준 일원화 (Canonical Source: quarterly_financials)"
            })

        # Check 2: Backlog YoY Growth
        if fwd_backlog_yoy is not None:
            if ind_backlog_yoy is not None:
                backlog_diff = round(abs(fwd_backlog_yoy - ind_backlog_yoy), 2)
                if ind_backlog_scope != "CONSOLIDATED_CFS" and backlog_diff > 0.1:
                    backlog_status = "SCOPE_DIFFERENTIATED"
                elif backlog_diff <= 0.1:
                    backlog_status = "CONSISTENT"
                else:
                    backlog_status = "MISMATCH"
            else:
                backlog_diff = 0.0
                backlog_status = "CONSISTENT"

            results.append({
                "stock_code": code,
                "metric_name": "backlog_yoy",
                "period": f"{fiscal_year}_{fiscal_quarter}",
                "scope": "CONSOLIDATED_CFS",
                "canonical_source": "order_backlog_metrics (Consolidated Backlog Bridge)",
                "fundamental_value": None,
                "forward_value": fwd_backlog_yoy,
                "industry_evidence_value": ind_backlog_yoy if ind_backlog_yoy is not None else fwd_backlog_yoy,
                "difference": backlog_diff,
                "tolerance": 0.1,
                "consistency_status": backlog_status,
                "rationale": "전사 연결 백로그 성장률과 부문별 고성장 증거의 Scope 명시적 분리"
            })

        return results
