import requests
import zipfile
import io
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List
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
        "219550": "01089378", # 디와이디
        "234920": "01020524", # 자이글
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
        
        # 동적 DART 고유코드 XML 전체 다운로드 및 캐싱
        if not self.dynamic_map:
            try:
                url = f"{self.BASE_URL}/corpCode.xml"
                res = requests.get(url, params={"crtfc_key": self.api_key}, timeout=15)
                if res.status_code == 200:
                    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                        xml_bytes = z.read("CORPCODE.xml")
                        tree = ET.fromstring(xml_bytes)
                        for elem in tree.findall("list"):
                            stk_cd = elem.findtext("stock_code", "").strip()
                            crp_cd = elem.findtext("corp_code", "").strip()
                            if stk_cd:
                                self.dynamic_map[stk_cd] = crp_cd
                        logger.info(f"[DART API] DART 고유코드 {len(self.dynamic_map)}개 동적 캐싱 완료")
            except Exception as e:
                logger.error(f"[DART API] DART 고유코드 동적다운로드 실패: {e}")

        return self.dynamic_map.get(stock_code, "00126380")

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

    def get_recent_disclosures_briefing(self, stock_list: List[Dict[str, Any]], target_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        보유 종목(stock_list)에 대하여 당일(또는 지정일) 발생한 주요 DART 공시를 수집하고,
        공시 요약, 시장 의미 및 투자 대응 가이드(1~3줄) 브리핑 데이터를 생성합니다.
        """
        if not self.is_valid_key() or not stock_list:
            return []

        if not target_date:
            from datetime import datetime
            target_date = datetime.now().strftime("%Y%m%d")

        disclosures = []
        seen_rcept_nos = set()
        logger.info(f"[DART API] {len(stock_list)}개 보유 종목의 {target_date} DART 신규 공시 검색 시작...")

        for stock in stock_list:
            code = str(stock.get("stock_code", "")).zfill(6)
            name = str(stock.get("stock_name", code))
            
            # 🔥 ETF 종목은 개별 기업 공시가 없으므로 DART 검색에서 100% 원천 제외
            is_etf = stock.get("is_etf", False) or any(k in name for k in ["ETF", "PLUS", "RISE", "KODEX", "TIGER", "ACE", "SOL", "고배당주", "밸류체인"]) or code in ("161510", "490590")
            if is_etf:
                continue

            corp_code = self.get_corp_code(code)

            url = f"{self.BASE_URL}/list.json"
            params = {
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bgn_de": target_date,
                "end_de": target_date,
                "page_count": 10
            }
            try:
                res = requests.get(url, params=params, timeout=5)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status") == "000" and data.get("list"):
                        for item in data["list"]:
                            r_nm = str(item.get("report_nm", "")).strip()
                            r_no = str(item.get("rcept_no", "")).strip()
                            r_dt = item.get("rcept_dt", target_date)
                            
                            # 일상적/사소한 행정 보고 필터링
                            if any(ign in r_nm for ign in ["지급수단별", "임원ㆍ주요주주", "약식"]):
                                continue
                            
                            # 중복 접수번호 방지
                            if r_no in seen_rcept_nos:
                                continue
                            seen_rcept_nos.add(r_no)
                            
                            briefing = self._generate_disclosure_briefing(name, code, r_nm, r_no, r_dt)
                            disclosures.append(briefing)
            except Exception as e:
                logger.warning(f"[DART API] {name}({code}) 공시 검색 중 오류: {e}")

        logger.info(f"[DART API] {target_date} 보유 종목 주요 DART 공시 {len(disclosures)}건 추출 완료")
        return disclosures

    def _generate_disclosure_briefing(self, name: str, code: str, report_nm: str, rcept_no: str, rcept_dt: str) -> Dict[str, Any]:
        """개별 공시 항목에 대한 맞춤형 실적 수치 기반 호재/중립/악재 브리핑 생성"""
        link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

        if any(k in report_nm for k in ["반기보고서", "분기보고서", "사업보고서"]):
            if "사업보고서" in report_nm:
                rep_title = "2025년 사업보고서 (연간 결산)"
                summary = "2025년 연간 사업보고서(11011) 확정 재무제표 공시 접수."
                q_yr = 2025
                q_code = "11011"
            elif "반기보고서" in report_nm:
                rep_title = "2026년 반기보고서 (상반기 누적)"
                summary = "2026년 상반기 반기보고서(11012) 확정 재무제표 공시 접수."
                q_yr = 2026
                q_code = "11012"
            else:
                rep_title = "2026년 1분기보고서"
                summary = "2026년 1분기보고서(11013) 확정 재무제표 공시 접수."
                q_yr = 2026
                q_code = "11013"
            
            # 실제 DART 재무제표 데이터 조회
            try:
                fin = self.get_financial_statement(code, fiscal_year=q_yr, reprt_code=q_code)
                if not fin or fin.get("revenue", 0) == 0:
                    fin = self.get_financial_statement(code, fiscal_year=2025, reprt_code="11011")
            except Exception:
                fin = None

            if fin and fin.get("revenue", 0) > 0:
                rev_eok = fin.get("revenue", 0) / 100000000.0
                rev_yoy = fin.get("revenue_yoy", 0.0)
                op = fin.get("operating_profit", 0)
                op_eok = op / 100000000.0
                op_yoy = fin.get("op_profit_yoy", 0.0)
                
                f_yr = fin.get("fiscal_year", 2025)
                f_code = fin.get("quarter_code", "11011")
                period_tag = f"{f_yr}년 연간 결산" if f_code == "11011" else (f"{f_yr}년 상반기 누적" if f_code == "11012" else f"{f_yr}년 1분기")

                if op > 0 and op_yoy >= 15.0:
                    impact = f"<span style='color:#059669; font-weight:bold;'>🟢 [호재 / 실적 호전]</span> [{period_tag} 기준] 매출 {rev_eok:,.0f}억원(YoY {rev_yoy:+.1f}%), 영업이익 {op_eok:,.0f}억원(YoY {op_yoy:+.1f}%)으로 어닝 서프라이즈 달성. 주가 상승 모멘텀 강력."
                    guide = "호실적에 따른 펀더멘털 신뢰도 상승으로 기존 목표가 도달 시까지 안정적 보유 지속."
                elif op > 0 and op_yoy >= 0.0:
                    impact = f"<span style='color:#059669; font-weight:bold;'>🟢 [호재 / 견조한 흑자]</span> [{period_tag} 기준] 매출 {rev_eok:,.0f}억원(YoY {rev_yoy:+.1f}%), 영업이익 {op_eok:,.0f}억원(YoY {op_yoy:+.1f}%)으로 견조한 흑자 기조 유지. 주가 하방 지지력 확보."
                    guide = "이익 성장세가 유지되고 있으므로 트레일링 손절가를 상향 관리하며 홀딩."
                elif op > 0 and op_yoy < 0.0:
                    impact = f"<span style='color:#D97706; font-weight:bold;'>🟡 [중립 / 영업익 둔화]</span> [{period_tag} 기준] 매출 {rev_eok:,.0f}억원(YoY {rev_yoy:+.1f}%), 영업이익 {op_eok:,.0f}억원(YoY {op_yoy:+.1f}%)으로 흑자는 유지했으나 이익률 감소."
                    guide = "실적 둔화에 따른 차익 매물 출회 가능성에 대비하여 단기 지지선 이탈 여부 모니터링."
                elif op < 0 and op_yoy > 0.0:
                    impact = f"<span style='color:#D97706; font-weight:bold;'>🟡 [중립 / 적자 축소]</span> [{period_tag} 기준] 매출 {rev_eok:,.0f}억원, 영업손실 {op_eok:,.0f}억원으로 적자 지속이나 전년 대비 손실폭 축소."
                    guide = "하반기 흑자전환 가시성을 점검하며 단기 반등 시 비중 조절 검토."
                else:
                    impact = f"<span style='color:#DC2626; font-weight:bold;'>🔴 [악재 / 적자 지속·확대]</span> [{period_tag} 기준] 매출 {rev_eok:,.0f}억원, 영업손실 {op_eok:,.0f}억원으로 적자 지속 및 수익성 부진."
                    guide = "펀더멘털 불확실성이 지속되므로 트레일링 손절가 및 사용자 우선 설정가를 엄격히 준수."
            else:
                impact = f"<span style='color:#D97706; font-weight:bold;'>🟡 [중립 / 데이터 파싱 중]</span> {rep_title} 접수 완료 (상세 재무제표 동기화 진행 중)."
                guide = "확정 실적 발표에 따른 시장 수급 반응을 주시하며 기존 전략 유지."

        elif any(k in report_nm for k in ["단일판매", "공급계약"]):
            summary = "신규 단일판매 및 대규모 공급계약 체결 공시 접수."
            impact = "<span style='color:#059669; font-weight:bold;'>🟢 [호재 / 수주 모멘텀]</span> 대규모 공급계약 체결로 수주잔고 및 향후 매출 인식 가시성 확대. 단기 주가 상승 모멘텀."
            guide = "계약 금액의 최근 매출액 대비 비중을 확인하고 단기 수급 유입 시 1차 목표가 도달 여부 주시."
        elif any(k in report_nm for k in ["유상증자", "전환사채", "신주인수권부사채"]):
            summary = "자금 조달 및 신주 발행(증자/사채) 주요사항보고서 접수."
            impact = "<span style='color:#DC2626; font-weight:bold;'>🔴 [경계 / 지분 희석 부담]</span> 신주 발행에 따른 주당가치 희석 및 단기 오버행(잠재 매물) 우려 공존."
            guide = "신주 발행가액, 증자 방식(제3자 배정 vs 일반공모) 및 납입 일정을 면밀히 모니터링하여 대응."
        elif "무상증자" in report_nm:
            summary = "주주가치 제고를 위한 무상증자 결정 공시 접수."
            impact = "<span style='color:#059669; font-weight:bold;'>🟢 [호재 / 주주환원]</span> 유통 주식수 확대 및 주주 친화 정책으로 단기 투자심리 개선 호재."
            guide = "권리락 일정 및 신주 배정 기준일을 체크하여 보유 포지션 유지."
        elif "계열회사와의상품" in report_nm:
            summary = "동일인 등 출자계열회사와의 상품·용역 거래내역 변경 공시."
            impact = "<span style='color:#64748B; font-weight:bold;'>🟡 [중립 / 내부거래 조정]</span> 그룹사 내부거래 규모 조정으로 기업 펀더멘털에 미치는 즉각적인 영향은 제한적."
            guide = "통상적인 영업 거래 공시이므로 기존 보유 및 매매 전략을 그대로 유지."
        else:
            summary = f"주요 경영 사항 및 공시({report_nm}) 접수."
            impact = "<span style='color:#64748B; font-weight:bold;'>🟡 [중립 / 일반 공시]</span> 통상적 공시 사항으로 세부 내용 확인 필요."
            guide = "직접 링크를 통해 세부 계약/결정 사항을 확인하시기 바랍니다."

        return {
            "stock_name": name,
            "stock_code": code,
            "report_nm": report_nm,
            "rcept_no": rcept_no,
            "rcept_dt": rcept_dt,
            "link": link,
            "summary": summary,
            "impact": impact,
            "guide": guide
        }

