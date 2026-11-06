import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from src.utils.logger import logger

class FundamentalEvidenceScanner:
    """
    Phase 4.1: Fundamental Evidence Scanner (Freshness & Evidence Calibration)
    - 최근 8분기 구조화 재무 시계열 분석
    - Flow/Stock 파생지표 (YoY, QoQ, OPM delta, OCF trend, 재고/채권 비중)
    - OCF 다각도 평가 (latest_Q, rolling_2Q, TTM OCF, OCF/OP 비율) 및 State 강등 분리
    - OPERATING_LEVERAGE 최소조건 (prior_year_OP > 0, Rev YoY > 0, OP YoY > Rev YoY * 1.5, OPM delta > 0)
    - 턴어라운드 유형 분류 (A: 흑자전환, B: 바닥회복, C: 매출+마진 동반확장, D_CANDIDATE, E: 기저효과, F: 근거부족, G: 피크아웃 위험)
    - 데이터 신선도 경고 (FUNDAMENTAL_DATA_STALE)
    """

    def __init__(self):
        pass

    def evaluate_evidence(self, quarterly_records: List[Dict[str, Any]], stock_code: str, data_age_days: int = 0, full_time_series: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        8개 분기 레코드를 기반으로 펀더멘탈 증거 및 턴어라운드 평가 수행
        (full_time_series가 제공되면 YoY 계산 시 이전 연도 데이터를 활용)
        """
        records_to_use = full_time_series if full_time_series and len(full_time_series) >= len(quarterly_records) else quarterly_records

        if not records_to_use or len(records_to_use) < 2:
            return {
                "stock_code": stock_code,
                "fundamental_state": "UNKNOWN",
                "turnaround_type": "F",
                "turnaround_label": "INSUFFICIENT_EVIDENCE",
                "high_quality_improvement": False,
                "operating_leverage": False,
                "warnings": ["DATA_PARTIAL (분기 데이터 부족)"],
                "evidence_bullets": ["분기 시계열 데이터가 2개 분기 미만으로 판정 불가"],
                "quarterly_summary_table": "분기 데이터 부족",
                "has_disagreement": False
            }

        df_full = pd.DataFrame(records_to_use).sort_values(["fiscal_year", "fiscal_quarter"]).reset_index(drop=True)
        
        # 필수 컬럼 기본값 보장
        for col in ["revenue", "operating_income", "net_income", "operating_cash_flow", "inventory", "accounts_receivable", "total_assets", "total_liabilities", "total_equity", "net_debt", "debt_ratio"]:
            if col not in df_full.columns:
                df_full[col] = 0.0

        n_full = len(df_full)

        # 1. 분기별 파생지표 연산 (YoY, QoQ, OPM, Debt/NetDebt)
        rev = df_full["revenue"].fillna(0.0).astype(float)
        op = df_full["operating_income"].fillna(0.0).astype(float)
        net = df_full["net_income"].fillna(0.0).astype(float)
        ocf = df_full["operating_cash_flow"].fillna(0.0).astype(float)
        inv = df_full["inventory"].fillna(0.0).astype(float)
        rec = df_full["accounts_receivable"].fillna(0.0).astype(float)
        assets = df_full["total_assets"].fillna(0.0).astype(float)
        liab = df_full["total_liabilities"].fillna(0.0).astype(float)
        equity = df_full["total_equity"].fillna(0.0).astype(float)
        net_debt = df_full["net_debt"].fillna(0.0).astype(float)

        opm = np.where(rev > 0, (op / rev) * 100.0, 0.0)
        df_full["opm"] = opm
        df_full["operating_margin"] = opm

        # YoY (4개 분기 전 비교)
        rev_yoy = pd.Series(index=df_full.index, dtype=float)
        op_yoy = pd.Series(index=df_full.index, dtype=float)
        opm_yoy_delta = pd.Series(index=df_full.index, dtype=float)
        inv_yoy = pd.Series(index=df_full.index, dtype=float)
        rec_yoy = pd.Series(index=df_full.index, dtype=float)

        # QoQ (직전 분기 비교)
        rev_qoq = pd.Series(index=df_full.index, dtype=float)
        op_qoq = pd.Series(index=df_full.index, dtype=float)
        opm_qoq_delta = pd.Series(index=df_full.index, dtype=float)

        for i in range(n_full):
            # QoQ
            if i >= 1 and rev.iloc[i-1] > 0:
                rev_qoq.iloc[i] = ((rev.iloc[i] - rev.iloc[i-1]) / abs(rev.iloc[i-1])) * 100.0
                op_qoq.iloc[i] = ((op.iloc[i] - op.iloc[i-1]) / abs(op.iloc[i-1])) * 100.0 if op.iloc[i-1] != 0 else 0.0
                opm_qoq_delta.iloc[i] = opm[i] - opm[i-1]

            # YoY
            if i >= 4 and rev.iloc[i-4] > 0:
                rev_yoy.iloc[i] = ((rev.iloc[i] - rev.iloc[i-4]) / abs(rev.iloc[i-4])) * 100.0
                op_yoy.iloc[i] = ((op.iloc[i] - op.iloc[i-4]) / abs(op.iloc[i-4])) * 100.0 if op.iloc[i-4] != 0 else 0.0
                opm_yoy_delta.iloc[i] = opm[i] - opm[i-4]
                if inv.iloc[i-4] > 0:
                    inv_yoy.iloc[i] = ((inv.iloc[i] - inv.iloc[i-4]) / abs(inv.iloc[i-4])) * 100.0
                if rec.iloc[i-4] > 0:
                    rec_yoy.iloc[i] = ((rec.iloc[i] - rec.iloc[i-4]) / abs(rec.iloc[i-4])) * 100.0

        latest_idx = n_full - 1
        latest_row = df_full.iloc[latest_idx]
        prev_row = df_full.iloc[latest_idx - 1] if latest_idx >= 1 else latest_row
        yoy_row = df_full.iloc[latest_idx - 4] if latest_idx >= 4 else None

        latest_rev_yoy = rev_yoy.iloc[latest_idx] if not pd.isna(rev_yoy.iloc[latest_idx]) else 0.0
        latest_op_yoy = op_yoy.iloc[latest_idx] if not pd.isna(op_yoy.iloc[latest_idx]) else 0.0
        latest_opm_delta = opm_yoy_delta.iloc[latest_idx] if not pd.isna(opm_yoy_delta.iloc[latest_idx]) else 0.0
        latest_inv_yoy = inv_yoy.iloc[latest_idx] if not pd.isna(inv_yoy.iloc[latest_idx]) else 0.0
        latest_rec_yoy = rec_yoy.iloc[latest_idx] if not pd.isna(rec_yoy.iloc[latest_idx]) else 0.0

        prev_rev_yoy = rev_yoy.iloc[latest_idx - 1] if latest_idx >= 1 and not pd.isna(rev_yoy.iloc[latest_idx - 1]) else 0.0
        prev_opm_delta = opm_yoy_delta.iloc[latest_idx - 1] if latest_idx >= 1 and not pd.isna(opm_yoy_delta.iloc[latest_idx - 1]) else 0.0

        # 2. OCF 다각도 복합 평가 (latest_Q, rolling_2Q, TTM OCF, OCF/OP 비율)
        latest_q_ocf = latest_row["operating_cash_flow"]
        prev_q_ocf = prev_row["operating_cash_flow"]
        rolling_2q_ocf = latest_q_ocf + prev_q_ocf

        ttm_start_idx = max(0, n_full - 4)
        ttm_ocf = df_full.iloc[ttm_start_idx:]["operating_cash_flow"].sum()
        ttm_op = df_full.iloc[ttm_start_idx:]["operating_income"].sum()
        ttm_rev = df_full.iloc[ttm_start_idx:]["revenue"].sum()
        ocf_to_op_ratio = (ttm_ocf / ttm_op) if ttm_op > 0 else 0.0

        # 3. 실적 품질 경고 (Earnings Quality Warnings)
        warnings = []

        # 1) OP_UP_OCF_DOWN (관측 경고: 당기 OP 흑자이나 당기 OCF 적자 또는 급감)
        has_ocf_warning = False
        if latest_row["operating_income"] > 0:
            if latest_q_ocf < 0:
                has_ocf_warning = True
                warnings.append("OP_UP_OCF_DOWN (당기 영업이익 흑자이나 당기 OCF 적자)")
            elif yoy_row is not None and latest_q_ocf < yoy_row["operating_cash_flow"] * 0.5:
                has_ocf_warning = True
                warnings.append("OP_UP_OCF_DOWN (영업이익 대비 OCF 전년동기 대비 현저한 둔화)")

        # 2) 강한 품질 악화 판정 (State 강등용 플래그)
        # OCF 2개 분기 연속 악화 OR (TTM OCF 급감 + 재고/채권 악화)
        is_severe_ocf_deterioration = False
        if rolling_2q_ocf < 0 or (latest_q_ocf < 0 and prev_q_ocf < 0):
            is_severe_ocf_deterioration = True
        elif ttm_op > 0 and ttm_ocf < (ttm_op * 0.3) and (latest_inv_yoy > (latest_rev_yoy + 15.0) or latest_rec_yoy > (latest_rev_yoy + 15.0)):
            is_severe_ocf_deterioration = True

        # 3) INVENTORY_GROWTH_EXCEEDS_REVENUE
        if latest_inv_yoy > (latest_rev_yoy + 20.0) and latest_inv_yoy > 15.0:
            warnings.append(f"INVENTORY_GROWTH_EXCEEDS_REVENUE (재고 YoY +{latest_inv_yoy:.1f}% > 매출 YoY +{latest_rev_yoy:.1f}%)")

        # 4) RECEIVABLE_GROWTH_EXCEEDS_REVENUE
        if latest_rec_yoy > (latest_rev_yoy + 20.0) and latest_rec_yoy > 15.0:
            warnings.append(f"RECEIVABLE_GROWTH_EXCEEDS_REVENUE (매출채권 YoY +{latest_rec_yoy:.1f}% > 매출 YoY +{latest_rev_yoy:.1f}%)")

        # 5) DEBT_RATIO_SPIKE
        if latest_row["debt_ratio"] > 200.0:
            warnings.append(f"DEBT_RATIO_SPIKE (부채비율 {latest_row['debt_ratio']:.1f}% 고위험)")
        elif yoy_row is not None and (latest_row["debt_ratio"] - yoy_row["debt_ratio"]) >= 30.0:
            warnings.append(f"DEBT_RATIO_SPIKE (부채비율 1년 전 대비 +{latest_row['debt_ratio'] - yoy_row['debt_ratio']:.1f}%p 급증)")

        # 6) NET_DEBT_RISING (최근 3분기 연속 순차입금 증가)
        if n_full >= 3:
            nd3 = [df_full.iloc[i]["net_debt"] for i in range(n_full-3, n_full)]
            if nd3[2] > nd3[1] > nd3[0] and nd3[2] > 0:
                warnings.append("NET_DEBT_RISING (3분기 연속 순차입금 증가)")

        # 7) FUNDAMENTAL_DATA_STALE (재무데이터 경과일수 초과)
        if data_age_days > 180:
            warnings.append(f"FUNDAMENTAL_DATA_STALE (재무데이터 경과일수 {data_age_days}일, 최신 정기공시 미반영)")

        if n_full < 4:
            warnings.append("DATA_PARTIAL (분기 수집 데이터 부족)")

        # 4. 8분기 추세 및 패턴 인식
        # 1) OPERATING_LEVERAGE:
        # 조건: prior_year_OP > 0 and Revenue_YoY > 0 and OP_YoY > Revenue_YoY * 1.5 and (OP_YoY - Revenue_YoY) >= 15.0 and OPM_YoY_delta > 0
        prior_year_op = yoy_row["operating_income"] if yoy_row is not None else 0.0
        is_op_leverage = bool(
            prior_year_op > 0 and
            latest_row["operating_income"] > 0 and
            latest_rev_yoy > 0 and
            latest_op_yoy > (latest_rev_yoy * 1.5) and
            (latest_op_yoy - latest_rev_yoy) >= 15.0 and
            latest_opm_delta > 0
        )

        # 2) HIGH_QUALITY_IMPROVEMENT: 최소 2개 분기 지속성 요구 (1개 분기는 CANDIDATE)
        latest_hq = bool(
            latest_row["operating_income"] > 0 and
            latest_rev_yoy > 0 and
            latest_opm_delta > 0 and
            (latest_q_ocf > 0 or ttm_ocf > 0)
        )
        prev_hq = bool(
            prev_row["operating_income"] > 0 and
            prev_rev_yoy > 0 and
            prev_opm_delta > 0
        )
        is_high_quality = bool(latest_hq and prev_hq)
        is_high_quality_candidate = bool(latest_hq and not prev_hq)

        # 5. Turnaround Classification
        turnaround_type = "F"
        turnaround_label = "INSUFFICIENT_EVIDENCE"

        # A: LOSS_TO_PROFIT (적자 지속 후 흑자 전환)
        recent_2q_op = [df_full.iloc[i]["operating_income"] for i in range(max(0, n_full-2), n_full)]
        past_4q_op = [df_full.iloc[i]["operating_income"] for i in range(max(0, n_full-6), max(0, n_full-2))]
        if len(recent_2q_op) >= 1 and recent_2q_op[-1] > 0:
            if (prior_year_op <= 0 and latest_row["operating_income"] > 0) or (len(past_4q_op) >= 2 and sum(1 for x in past_4q_op if x <= 0) >= 2):
                turnaround_type = "A"
                turnaround_label = "LOSS_TO_PROFIT (흑자전환 턴어라운드)"

        # B: TROUGH_RECOVERY (바닥 통과 후 연속 회복)
        if turnaround_type == "F" and n_full >= 4:
            if op.iloc[-1] > op.iloc[-2] > op.iloc[-3] and op.iloc[-3] == op.iloc[-5:].min():
                turnaround_type = "B"
                turnaround_label = "TROUGH_RECOVERY (바닥 통과 후 연속 반등)"

        # G: PEAK_OUT_RISK (피크아웃 위험: 과거 정점 후 연속 마진 축소 및 성장 둔화)
        if turnaround_type == "F" and n_full >= 4:
            if opm[-1] < opm[-2] < opm[-3] and (latest_opm_delta <= 0 or latest_rev_yoy < prev_rev_yoy):
                turnaround_type = "G"
                turnaround_label = "PEAK_OUT_RISK (실적 정점 통과 후 둔화)"

        # C: SALES_AND_MARGIN_EXPANSION (매출 + 마진 동반 확장)
        if turnaround_type == "F":
            if latest_rev_yoy > 0 and latest_opm_delta > 0:
                turnaround_type = "C"
                turnaround_label = "SALES_AND_MARGIN_EXPANSION (매출·마진 동반 확장)"

        # D_CANDIDATE: 구조적 촉매 대기 후보
        if turnaround_type == "F":
            if latest_rev_yoy >= 5.0 and latest_row["operating_income"] > 0:
                turnaround_type = "D_CANDIDATE"
                turnaround_label = "FORWARD_IMPROVEMENT_NEEDS_EVENT_DATA (수주/CAPA 확인 대기)"

        # 6. Fundamental Evidence State 판정 (단일 분기 OCF 적자로 자동 강등하지 않음)
        evidence_bullets = []

        if latest_rev_yoy != 0:
            evidence_bullets.append(f"Revenue YoY: {latest_rev_yoy:+.1f}% (당기 {latest_row['revenue']/1e8:,.0f}억 원)")
        if latest_op_yoy != 0:
            evidence_bullets.append(f"Operating Income YoY: {latest_op_yoy:+.1f}% (당기 {latest_row['operating_income']/1e8:,.0f}억 원)")
        evidence_bullets.append(f"Operating Margin: {latest_row['operating_margin']:.1f}% (전년동기대비 {latest_opm_delta:+.1f}%p)")
        evidence_bullets.append(f"OCF 현황: 당기 {latest_q_ocf/1e8:,.0f}억 원 | Rolling 2Q: {rolling_2q_ocf/1e8:,.0f}억 원 | TTM OCF: {ttm_ocf/1e8:,.0f}억 원 (TTM OCF/OP: {ocf_to_op_ratio:.1f}배)")
        evidence_bullets.append(f"Debt Ratio: {latest_row['debt_ratio']:.1f}% | Net Debt: {latest_row['net_debt']/1e8:,.0f}억 원")

        if is_high_quality:
            evidence_bullets.append("🌟 HIGH_QUALITY_IMPROVEMENT (2분기 연속 매출↑ + OPM↑ + OCF양호 지속)")
        elif is_high_quality_candidate:
            evidence_bullets.append("✨ HIGH_QUALITY_IMPROVEMENT_CANDIDATE (당기 실적 대폭 개선, 2개 분기 지속성 검증 대기)")
        if is_op_leverage:
            evidence_bullets.append(f"🚀 OPERATING_LEVERAGE (매출성장 +{latest_rev_yoy:.1f}% 대비 영업이익 +{latest_op_yoy:.1f}% 폭발적 레버리지)")

        # State 결정
        if turnaround_type in ["A", "C"] and not is_severe_ocf_deterioration and latest_row["debt_ratio"] < 200:
            fundamental_state = "STRONG" if ((is_high_quality or is_high_quality_candidate) and ttm_ocf > 0) else "IMPROVING"
        elif turnaround_type == "B" and not is_severe_ocf_deterioration:
            fundamental_state = "IMPROVING"
        elif turnaround_type == "G":
            fundamental_state = "WEAKENING"
        elif latest_row["operating_income"] < 0 or latest_row["debt_ratio"] > 250:
            fundamental_state = "DISTRESSED"
        elif latest_rev_yoy >= 0 and latest_row["operating_income"] > 0:
            fundamental_state = "STABLE"
        else:
            fundamental_state = "UNKNOWN"

        # 7. 최근 8분기 요약 마크다운 테이블 생성 (8분기 Window 대상)
        target_df = df_full.iloc[-8:].reset_index(drop=True)
        table_lines = [
            "| 분기 | 매출액(억) | YoY(%) | 영업이익(억) | YoY(%) | OPM(%) | OCF(억) | 부채비율(%) |",
            "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
        ]
        
        # full dataframe 인덱스 매핑
        offset = len(df_full) - len(target_df)
        for j in range(len(target_df)):
            orig_i = offset + j
            r = target_df.iloc[j]
            q_label = f"{r['fiscal_year']} {r['fiscal_quarter']}"
            r_rev = f"{r['revenue']/1e8:,.0f}"
            r_rev_yoy = f"{rev_yoy.iloc[orig_i]:+.1f}%" if not pd.isna(rev_yoy.iloc[orig_i]) else "-"
            r_op = f"{r['operating_income']/1e8:,.0f}"
            r_op_yoy = f"{op_yoy.iloc[orig_i]:+.1f}%" if not pd.isna(op_yoy.iloc[orig_i]) else "-"
            r_opm = f"{r['opm']:.1f}%"
            r_ocf = f"{r['operating_cash_flow']/1e8:,.0f}"
            r_dr = f"{r['debt_ratio']:.1f}%"
            table_lines.append(f"| {q_label} | {r_rev} | {r_rev_yoy} | {r_op} | {r_op_yoy} | {r_opm} | {r_ocf} | {r_dr} |")

        summary_table = "\n".join(table_lines)

        return {
            "stock_code": stock_code,
            "fundamental_state": fundamental_state,
            "turnaround_type": turnaround_type,
            "turnaround_label": turnaround_label,
            "high_quality_improvement": is_high_quality,
            "operating_leverage": is_op_leverage,
            "warnings": warnings if warnings else ["NONE (실적품질 경고 없음)"],
            "evidence_bullets": evidence_bullets,
            "quarterly_summary_table": summary_table,
            "latest_rev_yoy": latest_rev_yoy,
            "latest_op_yoy": latest_op_yoy,
            "latest_opm_delta": latest_opm_delta,
            "ttm_ocf": ttm_ocf,
            "rolling_2q_ocf": rolling_2q_ocf,
            "has_disagreement": False
        }
