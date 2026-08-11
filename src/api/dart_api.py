import requests
import zipfile
import io
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional
from config.settings import DART_API_KEY
from src.utils.logger import logger

class DartAPIClient:
    """
    DART Open API를 연동하여 실제 기업 재무제표(매출, 영업이익, 순이익, 영업현금흐름, 부채비율 및 YoY 성장률)를 수집하는 클라이언트입니다.
    """
    BASE_URL = "https://opendart.fss.or.kr/api"

    # 주요 종목 6자리 종목코드 -> DART 8자리 고유코드 매핑
    CORP_CODE_MAP = {
        "011700": "00162072", # 한신기계
        "013030": "00165060", # 하이록코리아
        "034020": "00159616", # 두산에너빌리티
        "047050": "00124504", # 포스코인터내셔널
        "047770": "00329093", # 코데즈컴바인
        "055490": "00256955", # 테이팩스
        "140670": "00858124", # 알에스오토메이션
        "206650": "00927558", # 유바이오로직스
        "219550": "01089378", # 자이글
        "241520": "01109715", # DSC인베스트먼트
        "267260": "01205851", # HD현대일렉트릭
        "348340": "01336461", # 뉴로메카
        "000490": "00109286", # 대동
        "004960": "00162063", # 한신공영
    }

    def __init__(self, api_key: str = DART_API_KEY):
        self.api_key = api_key
        self.dynamic_map = {}

    def is_valid_key(self) -> bool:
        return bool(self.api_key and self.api_key != "YOUR_DART_API_KEY_HERE")

    def get_corp_code(self, stock_code: str) -> str:
        if stock_code in self.CORP_CODE_MAP:
            return self.CORP_CODE_MAP[stock_code]
        if stock_code in self.dynamic_map:
            return self.dynamic_map[stock_code]
        return "00126380"  # 기본값

    def get_financial_statement(self, stock_code: str, fiscal_year: int = 2025, reprt_code: str = "11011") -> Optional[Dict[str, Any]]:
        """
        DART 단일회사 전체 재무제표 API (fnlttSinglAcntAll.json) 연동.
        sj_div (IS/BS/CF) 구별 및 계정ID 1순위 정밀 파싱, Sanity Check(이상치 검증)를 수행합니다.
        """
        if not self.is_valid_key():
            logger.info(f"[DART API] API Key가 설정되지 않아 샘플 데이터로 대체합니다.")
            return self._get_fallback_data(stock_code)

        # ETF/펀드 상품 예외 처리 (DART 재무제표 대상에서 원천 제외)
        if stock_code in ["088500", "371460", "484730"]:
            return {
                "stock_code": stock_code,
                "is_etf": True,
                "revenue": 0, "operating_profit": 0, "net_income": 0,
                "operating_cash_flow": 0, "debt_ratio": 0.0,
                "revenue_yoy": 0.0, "op_profit_yoy": 0.0,
                "audit_opinion": "적정", "disclosure_risk_flag": False,
                "f_score_confirmed": True, "sanity_pass": True
            }

        corp_code = self.get_corp_code(stock_code)
        
        # 2025년 사업보고서 -> 2026년 1분기 -> 2024년 사업보고서 순서로 최신 공시 자동 조회
        query_targets = [
            (2025, "11011", True),   # 2025 연간 사업보고서 (최신)
            (2026, "11013", True),   # 2026 1분기보고서 (최신)
            (2024, "11011", False)   # 2024 연간 사업보고서 (과거자료-경고대상)
        ]
        
        for yr, r_code, is_recent in query_targets:
            for fs_div in ["CFS", "OFS"]:
                url = f"{self.BASE_URL}/fnlttSinglAcntAll.json"
                params = {
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bsns_year": str(yr),
                    "reprt_code": r_code,
                    "fs_div": fs_div
                }
                try:
                    response = requests.get(url, params=params, timeout=10)
                    if response.status_code == 200:
                        res_json = response.json()
                        if res_json.get("status") == "000":
                            items = res_json.get("list", [])
                            parsed = self._parse_all_dart_statement_strict(items, fs_div, yr, r_code, is_recent)
                            if parsed and (parsed.get("revenue", 0) != 0 or parsed.get("operating_profit", 0) != 0):
                                parsed["stock_code"] = stock_code
                                parsed["corp_code"] = corp_code
                                logger.info(
                                    f"[DART API] {stock_code} 정밀 재무수집 ({yr}년 {r_code} {fs_div} / "
                                    f"매출:{parsed.get('revenue'):,.0f}원, 영업이익:{parsed.get('operating_profit'):,.0f}원, "
                                    f"OCF:{parsed.get('operating_cash_flow'):,.0f}원, 검증성공:{parsed.get('sanity_pass')})"
                                )
                                return parsed
                except Exception as e:
                    logger.error(f"[DART API] {stock_code} {yr}년 {fs_div} 수집 예외: {e}")

        return self._get_fallback_single_account(stock_code, corp_code, 2024, "11011")

    def _parse_all_dart_statement_strict(self, items: list, fs_div: str, year: int, reprt_code: str, is_recent: bool) -> Dict[str, Any]:
        """sj_div (IS/BS/CF) 및 표준 account_id 엄격 파싱 및 Sanity Check 수행"""
        thstrm = {}
        frmtrm = {}

        for item in items:
            sj_div = item.get("sj_div", "").strip().upper()
            acc_id = item.get("account_id", "").strip()
            acc_nm = item.get("account_nm", "").strip().replace(" ", "")

            th_str = item.get("thstrm_amount") or item.get("thstrm_add_amount") or "0"
            fr_str = item.get("frmtrm_amount") or item.get("frmtrm_add_amount") or "0"

            th_str = str(th_str).replace(",", "").strip()
            fr_str = str(fr_str).replace(",", "").strip()

            try: th_val = float(th_str) if th_str not in ["-", "", "None"] else 0.0
            except ValueError: th_val = 0.0

            try: fr_val = float(fr_str) if fr_str not in ["-", "", "None"] else 0.0
            except ValueError: fr_val = 0.0

            # 1. 손익계산서 (IS/CIS) 계정 파싱
            if sj_div in ["IS", "CIS"]:
                # 매출액: 표준 account_id 우선, 단일 계정명 보조
                if any(x in acc_id for x in ["Revenue", "SalesRevenue"]) or acc_nm in ["매출액", "수익(매출액)", "매출"]:
                    if "revenue" not in thstrm:
                        thstrm["revenue"] = th_val; frmtrm["revenue"] = fr_val
                # 영업이익
                elif any(x in acc_id for x in ["OperatingIncomeLoss"]) or acc_nm in ["영업이익", "영업이익(손실)"]:
                    if "operating_profit" not in thstrm:
                        thstrm["operating_profit"] = th_val; frmtrm["operating_profit"] = fr_val
                # 당기순이익
                elif any(x in acc_id for x in ["ProfitLoss"]) or acc_nm in ["당기순이익", "당기순이익(손실)"]:
                    if "net_income" not in thstrm:
                        thstrm["net_income"] = th_val; frmtrm["net_income"] = fr_val

            # 2. 현금흐름표 (CF) 계정 파싱
            elif sj_div == "CF":
                if any(x in acc_id for x in ["CashFlowsFromUsedInOperatingActivities", "OperatingActivities"]) or "영업활동" in acc_nm:
                    if "operating_cash_flow" not in thstrm:
                        thstrm["operating_cash_flow"] = th_val; frmtrm["operating_cash_flow"] = fr_val

            # 3. 재무상태표 (BS) 계정 파싱
            elif sj_div == "BS":
                # 자산총계
                if acc_id == "ifrs-full_Assets" or acc_nm in ["자산총계", "자산"]:
                    if "total_assets" not in thstrm:
                        thstrm["total_assets"] = th_val; frmtrm["total_assets"] = fr_val
                # 부채총계
                elif acc_id == "ifrs-full_Liabilities" or acc_nm in ["부채총계", "부채"]:
                    if "total_liabilities" not in thstrm:
                        thstrm["total_liabilities"] = th_val; frmtrm["total_liabilities"] = fr_val
                # 자본총계
                elif acc_id in ["ifrs-full_Equity", "ifrs-full_EquityAttributableToOwnersOfParent"] or acc_nm in ["자본총계", "자본"]:
                    if "total_equity" not in thstrm:
                        thstrm["total_equity"] = th_val; frmtrm["total_equity"] = fr_val

        # YoY 계산 (전기 2024년 비교값은 2025년 최신 보고서 동일 계정 행의 frmtrm_amount 100% 사용)
        def calc_yoy(th, fr):
            if fr != 0:
                return round(((th - fr) / abs(fr)) * 100.0, 2)
            return 0.0

        rev_val = thstrm.get("revenue", 0.0)
        rev_fr = frmtrm.get("revenue", 0.0)
        
        op_val = thstrm.get("operating_profit", 0.0)
        op_fr = frmtrm.get("operating_profit", 0.0)
        
        net_val = thstrm.get("net_income", 0.0)
        net_fr = frmtrm.get("net_income", 0.0)
        
        ocf_val = thstrm.get("operating_cash_flow", 0.0)

        assets = thstrm.get("total_assets", 0.0)
        liab = thstrm.get("total_liabilities", 0.0)
        eq = thstrm.get("total_equity", 0.0)

        assets_fr = frmtrm.get("total_assets", 0.0)
        liab_fr = frmtrm.get("total_liabilities", 0.0)
        eq_fr = frmtrm.get("total_equity", 0.0)

        # 올바른 회계 부채비율 계산: (부채총계 / 자본총계 * 100)
        if eq > 0 and liab > 0:
            debt_ratio = round((liab / eq) * 100.0, 2)
        elif eq > 0 and assets >= eq:
            debt_ratio = round(((assets / eq) - 1.0) * 100.0, 2)
        else:
            debt_ratio = 0.0  # 자본잠식 또는 미수집 시 0으로 두고 검증 실패 처리

        if eq_fr > 0 and liab_fr > 0:
            prev_debt_ratio = round((liab_fr / eq_fr) * 100.0, 2)
        elif eq_fr > 0 and assets_fr >= eq_fr:
            prev_debt_ratio = round(((assets_fr / eq_fr) - 1.0) * 100.0, 2)
        else:
            prev_debt_ratio = debt_ratio

        rev_yoy = calc_yoy(rev_val, rev_fr)
        op_yoy = calc_yoy(op_val, op_fr)
        net_yoy = calc_yoy(net_val, net_fr)

        # 4. 재무 데이터 Sanity Check (당기 및 전기 2개 연도 종합 이상치 정밀 검증)
        sanity_pass = True
        sanity_reason = []

        if rev_val > 0 and abs(op_val) > rev_val:
            sanity_pass = False
            sanity_reason.append("당기 영업이익이 매출액 초과 (계정오매핑)")
        if rev_fr > 0 and abs(op_fr) > rev_fr:
            sanity_pass = False
            sanity_reason.append("전기(2024) 영업이익이 매출액 초과 (계정오매핑)")
        if rev_val == 0 and op_val != 0:
            sanity_pass = False
            sanity_reason.append("당기 매출액 0원이나 영업이익 존재")
        if rev_fr == 0 and op_fr != 0:
            sanity_pass = False
            sanity_reason.append("전기(2024) 매출액 0원이나 영업이익 존재")
        if ocf_val == 0.0:
            sanity_pass = False
            sanity_reason.append("영업현금흐름(OCF) 미수집/NULL")
        if rev_fr == 0.0:
            sanity_pass = False
            sanity_reason.append("전기(2024) 매출액 미수집 (성장률 왜곡위험)")
        if rev_val > 0 and rev_fr > 0 and (rev_val / rev_fr > 2.5 or rev_val / rev_fr < 0.4):
            sanity_pass = False
            sanity_reason.append(f"전기 대비 매출 왜곡 변동 ({rev_yoy:+.1f}%)")
        if debt_ratio <= 0.0 or debt_ratio > 400.0:
            sanity_pass = False
            sanity_reason.append(f"부채비율 이상치 ({debt_ratio:.1f}%)")
        if not is_recent:
            sanity_pass = False
            sanity_reason.append("과거 2024년 재무자료 경과")

        completeness = 100.0 if sanity_pass else 75.0
        status_str = "정상수집·검증통과" if sanity_pass else "수집성공·이상치검출"
        sanity_detail_flag = f"[계정: thstrm vs frmtrm | BS: 당기({liab:,.0f}/{eq:,.0f}={debt_ratio:.1f}%) vs 전기({liab_fr:,.0f}/{eq_fr:,.0f}={prev_debt_ratio:.1f}%) | 기간: {reprt_code} | CFS/OFS: {fs_div} | 검증: {'PASS' if sanity_pass else 'FAIL'}]"

        return {
            "fiscal_year": year,
            "quarter_code": reprt_code,
            "fs_div": fs_div,
            "revenue": rev_val,
            "prev_revenue": rev_fr,
            "revenue_yoy": rev_yoy,
            "operating_profit": op_val,
            "prev_operating_profit": op_fr,
            "op_profit_yoy": op_yoy,
            "net_income": net_val,
            "prev_net_income": net_fr,
            "net_income_yoy": net_yoy,
            "operating_cash_flow": ocf_val,
            "prev_operating_cash_flow": frmtrm.get("operating_cash_flow", 0.0),
            "total_liabilities": liab,
            "total_equity": eq,
            "prev_total_liabilities": liab_fr,
            "prev_total_equity": eq_fr,
            "debt_ratio": debt_ratio,
            "prev_debt_ratio": prev_debt_ratio,
            "sanity_pass": sanity_pass,
            "sanity_reason": " / ".join(sanity_reason) if sanity_reason else "정상",
            "sanity_detail_flag": sanity_detail_flag,
            "f_score_confirmed": sanity_pass,
            "data_completeness": completeness,
            "collection_status": status_str,
            "per": 15.0, "pbr": 1.2,
            "audit_opinion": "적정",
            "disclosure_risk_flag": False
        }

    def _get_fallback_single_account(self, stock_code: str, corp_code: str, fiscal_year: int, reprt_code: str) -> Optional[Dict[str, Any]]:
        """DART 단일회사 주요계정 API (fnlttSinglAcnt.json) Fallback 호출"""
        url = f"{self.BASE_URL}/fnlttSinglAcnt.json"
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": str(fiscal_year),
            "reprt_code": reprt_code
        }
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                res_json = response.json()
                if res_json.get("status") == "000":
                    parsed = self._parse_dart_statement(res_json.get("list", []))
                    parsed["stock_code"] = stock_code
                    parsed["corp_code"] = corp_code
                    return parsed
        except Exception as e:
            logger.error(f"[DART API Fallback] {stock_code} 주요계정 API 오류: {e}")

        return self._get_fallback_data(stock_code)

    def _parse_dart_statement(self, items: list) -> Dict[str, Any]:
        """DART API 주요계정 파싱"""
        thstrm = {}
        frmtrm = {}

        for item in items:
            nm = item.get("account_nm", "").strip()
            th_str = item.get("thstrm_amount", "0").replace(",", "")
            fr_str = item.get("frmtrm_amount", "0").replace(",", "")

            try: th_val = float(th_str) if th_str not in ["-", ""] else 0.0
            except ValueError: th_val = 0.0

            try: fr_val = float(fr_str) if fr_str not in ["-", ""] else 0.0
            except ValueError: fr_val = 0.0

            if "매출액" in nm or "수익(매출액)" in nm:
                thstrm["revenue"] = th_val; frmtrm["revenue"] = fr_val
            elif "영업이익" in nm:
                thstrm["operating_profit"] = th_val; frmtrm["operating_profit"] = fr_val
            elif "당기순이익" in nm or "당기순이익(손실)" in nm:
                thstrm["net_income"] = th_val; frmtrm["net_income"] = fr_val
            elif "영업활동" in nm and "현금흐름" in nm:
                thstrm["operating_cash_flow"] = th_val; frmtrm["operating_cash_flow"] = fr_val
            elif "부채총계" in nm:
                thstrm["total_liabilities"] = th_val
            elif "자본총계" in nm:
                thstrm["total_equity"] = th_val

        # 성장률 (YoY) 계산
        def calc_yoy(th, fr):
            if fr != 0:
                return round(((th - fr) / abs(fr)) * 100.0, 2)
            return 0.0

        rev_yoy = calc_yoy(thstrm.get("revenue", 0), frmtrm.get("revenue", 0))
        op_yoy = calc_yoy(thstrm.get("operating_profit", 0), frmtrm.get("operating_profit", 0))
        net_yoy = calc_yoy(thstrm.get("net_income", 0), frmtrm.get("net_income", 0))

        # 부채비율 계산
        liab = thstrm.get("total_liabilities", 0.0)
        eq = thstrm.get("total_equity", 1.0)
        debt_ratio = round((liab / eq) * 100.0, 2) if eq > 0 else 80.0

        return {
            "fiscal_year": 2024,
            "quarter_code": "11011",
            "fs_div": "CFS",
            "revenue": thstrm.get("revenue", 0.0),
            "revenue_yoy": rev_yoy,
            "operating_profit": thstrm.get("operating_profit", 0.0),
            "op_profit_yoy": op_yoy,
            "net_income": thstrm.get("net_income", 0.0),
            "net_income_yoy": net_yoy,
            "operating_cash_flow": thstrm.get("operating_cash_flow", 0.0),
            "debt_ratio": debt_ratio,
            "order_backlog": 10000000000.0,
            "per": 15.0, "pbr": 1.2,
            "audit_opinion": "적정",
            "disclosure_risk_flag": False
        }

    def _get_fallback_data(self, stock_code: str) -> Dict[str, Any]:
        """API 오류시 보수적 대체 데이터"""
        return {
            "stock_code": stock_code,
            "fiscal_year": 2024,
            "quarter_code": "11011",
            "fs_div": "CFS",
            "revenue": 10000000000, "revenue_yoy": -5.0,
            "operating_profit": -1000000000, "op_profit_yoy": -10.0,
            "net_income": -1500000000, "net_income_yoy": -15.0,
            "operating_cash_flow": -500000000, "debt_ratio": 120.0,
            "per": 0.0, "pbr": 0.8,
            "audit_opinion": "적정", "disclosure_risk_flag": False
        }
