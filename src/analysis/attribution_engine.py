import statistics
from typing import Dict, Any, List, Optional
from src.database.db_manager import DatabaseManager
from src.utils.logger import logger

class AttributionEngine:
    """
    Phase 7 Performance Attribution & Blocker Effectiveness Engine
    - 축적된 Scan Journal과 Signal Outcomes를 기반으로 각 Gate, State, Action, Blocker의 사후 통계를 집계합니다.
    - NO AUTO TUNING 정책: 표본 수 N < 30 (권장 50) 미만일 경우 최적화/수정을 엄격히 금지하고 관찰용 지표만 산출합니다.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def aggregate_by_dimension(self, dimension_key: str) -> List[Dict[str, Any]]:
        """
        특정 축(dimension_key)을 기준으로 성과 통계 집계
        - 지원 축: industry_gate, fundamental_state, forward_opportunity, forward_risk,
                  technical_action, shadow_integrated_state, primary_blocker
        """
        records = self.db.get_all_signal_outcomes_with_journal()
        if not records:
            return []

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for r in records:
            val = r.get(dimension_key, "UNKNOWN") or "UNKNOWN"
            if val not in grouped:
                grouped[val] = []
            grouped[val].append(r)

        results = []
        for grp_name, rows in grouped.items():
            sample_count = len(rows)

            ret_5d_list = [r["return_5d"] for r in rows if r.get("return_5d") is not None]
            ret_20d_list = [r["return_20d"] for r in rows if r.get("return_20d") is not None]
            mfe_20d_list = [r["mfe_20d"] for r in rows if r.get("mfe_20d") is not None]
            mae_20d_list = [r["mae_20d"] for r in rows if r.get("mae_20d") is not None]

            avg_ret_5d = round(statistics.mean(ret_5d_list), 2) if ret_5d_list else None
            avg_ret_20d = round(statistics.mean(ret_20d_list), 2) if ret_20d_list else None
            med_ret_20d = round(statistics.median(ret_20d_list), 2) if ret_20d_list else None
            win_rate_20d = round((sum(1 for x in ret_20d_list if x > 0) / len(ret_20d_list)) * 100.0, 1) if ret_20d_list else None

            avg_mfe_20d = round(statistics.mean(mfe_20d_list), 2) if mfe_20d_list else None
            avg_mae_20d = round(statistics.mean(mae_20d_list), 2) if mae_20d_list else None

            stop_count = sum(1 for r in rows if r.get("stop_hit") == 1)
            stop_rate = round((stop_count / sample_count) * 100.0, 1) if sample_count > 0 else 0.0

            p3_count = sum(1 for r in rows if r.get("hit_plus_3atr") == 1)
            p3_rate = round((p3_count / sample_count) * 100.0, 1) if sample_count > 0 else 0.0

            is_significant = (sample_count >= 30)
            sample_guidance = "STATISTICALLY_VALID (N>=30)" if is_significant else f"OBSERVATIONAL_ONLY (N={sample_count} < 30 표본부족 - NO_AUTO_TUNING)"

            results.append({
                "dimension": dimension_key,
                "group_name": grp_name,
                "sample_count": sample_count,
                "avg_return_5d": avg_ret_5d,
                "avg_return_20d": avg_ret_20d,
                "median_return_20d": med_ret_20d,
                "win_rate_20d": win_rate_20d,
                "avg_mfe_20d": avg_mfe_20d,
                "avg_mae_20d": avg_mae_20d,
                "stop_rate": stop_rate,
                "plus_3atr_hit_rate": p3_rate,
                "is_statistically_significant": is_significant,
                "sample_guidance": sample_guidance
            })

        # 정렬: 표본 수 내림차순
        results.sort(key=lambda x: x["sample_count"], reverse=True)
        return results

    def analyze_blocker_effectiveness(self) -> List[Dict[str, Any]]:
        """
        차단 신호(Blocker)의 사후 손실 방어 효과 분석
        - BLOCKED_BY_TECHNICAL, BLOCKED_BY_INDUSTRY, BLOCKED_BY_FORWARD_RISK 등의 20D MAE 및 Stop Hit Rate 검증
        """
        return self.aggregate_by_dimension("primary_blocker")

    @staticmethod
    def audit_raw_vs_effective_contribution(
        raw_val: float,
        cap: float,
        origin_type: str
    ) -> Dict[str, Any]:
        """
        증거의 Raw 기여도 vs Effective 기여도 및 Cap 적용 사유 분리 계산
        """
        effective = min(raw_val, cap)
        is_capped = (raw_val > cap)
        cap_reason = "NONE"
        if is_capped:
            if origin_type == "MANUAL_QUALITATIVE":
                cap_reason = "MANUAL_QUALITATIVE_35PCT_LIMIT"
            else:
                cap_reason = "DRIVER_FAMILY_CONTRIBUTION_CAP"

        return {
            "raw_contribution": round(raw_val, 2),
            "effective_contribution": round(effective, 2),
            "contribution_cap": round(cap, 2),
            "is_capped": is_capped,
            "cap_reason": cap_reason
        }
