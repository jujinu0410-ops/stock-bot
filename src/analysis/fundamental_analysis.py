from typing import Dict, Any, Optional
from src.utils.logger import logger

class FundamentalAnalysis:
    """
    DART API 실시간 재무제표 데이터를 활용해 실질적인 기본적 분석(F 점수, 0~100점)을 수행합니다.
    적자 기업, 현금흐름 부실 기업, 부채비율과 성장률에 따라 점수를 엄격히 차등 부여하며,
    재무제표 항목 누락 시 데이터완성도를 감점하고 F점수 확정을 보류합니다.
    """
    def __init__(self, financial_data: Dict[str, Any]):
        self.data = financial_data or {}

    def evaluate(self) -> Dict[str, Any]:
        stock_name = str(self.data.get('stock_name', ''))
        stock_code = str(self.data.get('stock_code', ''))
        etf_keywords = ['ETF', 'TIGER', 'RISE', 'PLUS', 'KODEX', 'ACE', 'SOL', 'KBSTAR', 'ARIRANG', 'HANARO', '커버드콜', 'SOLACTIVE']
        is_etf = (
            self.data.get("is_etf", False) or
            any(k in stock_name.upper() for k in etf_keywords) or
            stock_code in ['371460', '484730', '490590', '161510', '088500']
        )

        # 1. ETF/ETN 상품 예외 처리 (기업 DART F점수 대상 제외)
        if is_etf:
            return {
                "is_etf": True,
                "f_score": 50.0,
                "data_completeness": 100.0,
                "f_score_confirmed": True,
                "is_eligible_stage1": False,
                "eval_details": "ETF 상품 (DART 기업 재무 F점수 평가 대상 제외 / ETF 전용 평가 적용)"
            }

        earned_f_score = 0.0
        eval_details = []
        missing_count = 0
        total_fields = 5

        rev = float(self.data.get('revenue') or 0.0)
        op = float(self.data.get('operating_profit') or 0.0)
        net = float(self.data.get('net_income') or 0.0)
        raw_ocf = self.data.get('operating_cash_flow')
        rev_yoy = float(self.data.get('revenue_yoy') or 0.0)
        op_yoy = float(self.data.get('op_profit_yoy') or 0.0)
        debt_ratio = float(self.data.get('debt_ratio') or 100.0)

        # 1. 실적 성장성 (25점 만점)
        if rev == 0.0 and op == 0.0:
            missing_count += 1
            growth_pts = 5.0
            eval_details.append("성장성 5등급(5점: 매출/영업이익 데이터 미수집)")
        elif op > 0:
            if op_yoy >= 20.0 and rev_yoy >= 10.0:
                growth_pts = 25.0
                eval_details.append("성장성 1등급(25점: 영업이익YoY >= 20% & 매출YoY >= 10%)")
            elif op_yoy >= 10.0:
                growth_pts = 20.0
                eval_details.append("성장성 2등급(20점: 영업이익YoY 10~20%)")
            elif op_yoy >= 0.0:
                growth_pts = 15.0
                eval_details.append("성장성 3등급(15점: 영업이익YoY 0~10%)")
            else:
                growth_pts = 10.0
                eval_details.append("성장성 4등급(10점: 영업이익 역성장 0~-10%)")
        else:
            growth_pts = 5.0
            eval_details.append("성장성 5등급(5점: 영업적자 발생)")

        earned_f_score += growth_pts

        # 2. 현금흐름 건전성 (20점 만점) - OCF <= 0 인 경우 반드시 4점 고정!
        ocf = float(raw_ocf) if raw_ocf is not None else 0.0
        if ocf <= 0.0:
            missing_count += (1 if ocf == 0.0 else 0)
            cf_pts = 4.0
            eval_details.append(f"현금흐름 5등급(4점: OCF 유출/부진 {ocf/1e8:.1f}억 원)")
        else:
            if ocf >= net and net > 0:
                cf_pts = 20.0
                eval_details.append(f"현금흐름 1등급(20점: OCF 흑자 {ocf/1e8:.1f}억 원 및 순이익 초과)")
            elif ocf > 0:
                cf_pts = 16.0
                eval_details.append(f"현금흐름 2등급(16점: OCF 흑자 {ocf/1e8:.1f}억 원)")
            else:
                cf_pts = 4.0
                eval_details.append("현금흐름 5등급(4점: 영업현금 유출)")

        earned_f_score += cf_pts

        # 3. 수주/사업 촉매 (20점 만점)
        cat_pts = 4.0
        if op > 0 and rev_yoy >= 10.0:
            cat_pts = 20.0
            eval_details.append("사업촉매 1등급(20점: 외형성장+영업흑자 수주확대)")
        elif op > 0 and rev_yoy >= 0.0:
            cat_pts = 16.0
            eval_details.append("사업촉매 2등급(16점: 영업흑자 및 외형 유지)")
        elif op > 0:
            cat_pts = 12.0
            eval_details.append("사업촉매 3등급(12점: 영업흑자 달성)")
        elif rev_yoy >= 0.0:
            cat_pts = 8.0
            eval_details.append("사업촉매 4등급(8점: 매출 증가 턴어라운드 시도)")
        else:
            cat_pts = 4.0
            eval_details.append("사업촉매 5등급(4점: 실적 악화 정체)")

        earned_f_score += cat_pts

        # 4. 재무 안정성 - 부채비율 (20점 만점)
        if debt_ratio <= 0.0:
            missing_count += 1
            stab_pts = 4.0
            eval_details.append("재무안정 5등급(4점: 부채비율 미수집)")
        elif debt_ratio < 50.0:
            stab_pts = 20.0
            eval_details.append(f"재무안정 1등급(20점: 부채비율 {debt_ratio:.1f}% < 50%)")
        elif debt_ratio < 100.0:
            stab_pts = 16.0
            eval_details.append(f"재무안정 2등급(16점: 부채비율 {debt_ratio:.1f}% < 100%)")
        elif debt_ratio < 150.0:
            stab_pts = 12.0
            eval_details.append(f"재무안정 3등급(12점: 부채비율 {debt_ratio:.1f}% < 150%)")
        elif debt_ratio < 200.0:
            stab_pts = 8.0
            eval_details.append(f"재무안정 4등급(8점: 부채비율 {debt_ratio:.1f}% < 200%)")
        else:
            stab_pts = 4.0
            eval_details.append(f"재무안정 5등급(4점: 부채비율 {debt_ratio:.1f}% >= 200%)")

        earned_f_score += stab_pts

        # 5. 밸류에이션 및 지배구조 (15점 만점)
        audit_op = self.data.get('audit_opinion', '적정')
        disc_risk = self.data.get('disclosure_risk_flag', False)

        val_pts = 3.0
        if audit_op == '적정' and not disc_risk:
            if op > 0 and net > 0:
                val_pts = 15.0
                eval_details.append("밸류/경영 1등급(15점: 영업/순이익 쌍쌍 흑자 및 감사의견 적정)")
            elif op > 0:
                val_pts = 12.0
                eval_details.append("밸류/경영 2등급(12점: 영업이익 흑자 및 감사의견 적정)")
            elif net > 0:
                val_pts = 9.0
                eval_details.append("밸류/경영 3등급(9점: 순이익 흑자 및 감사의견 적정)")
            else:
                val_pts = 6.0
                eval_details.append("밸류/경영 4등급(6점: 적자기업이나 감사의견 적정)")
        else:
            val_pts = 3.0
            eval_details.append("밸류/경영 5등급(3점: 공시 위험 또는 감사의견 비적정)")

        earned_f_score += val_pts

        # 사후 F점수 검증: F점수는 반드시 5개 항목 합계와 일치해야 함!
        calculated_sum = growth_pts + cf_pts + cat_pts + stab_pts + val_pts
        f_score = round(calculated_sum, 1)
        data_completeness = round(((total_fields - missing_count) / total_fields) * 100.0, 1)
        
        # DART 산출 sanity_pass 검증 및 미완성/과거자료 시 f_score_confirmed = False 처리
        dart_sanity = self.data.get('sanity_pass', True)
        dart_confirmed = self.data.get('f_score_confirmed', True)
        
        f_score_confirmed = (data_completeness >= 90.0) and dart_sanity and dart_confirmed
        is_eligible_stage1 = f_score >= 65.0 and f_score_confirmed

        return {
            "is_etf": False,
            "f_score": f_score,
            "growth_pts": growth_pts,
            "cf_pts": cf_pts,
            "cat_pts": cat_pts,
            "stab_pts": stab_pts,
            "val_pts": val_pts,
            "data_completeness": data_completeness if f_score_confirmed else min(data_completeness, 75.0),
            "f_score_confirmed": f_score_confirmed,
            "is_eligible_stage1": is_eligible_stage1,
            "sanity_reason": self.data.get('sanity_reason', '정상'),
            "eval_details": " / ".join(eval_details)
        }
