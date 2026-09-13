import requests
import zipfile
import io
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List, Tuple
from config.settings import DART_API_KEY
from src.utils.logger import logger
from src.api.dart_account_utils import is_revenue_account


KST = ZoneInfo("Asia/Seoul")
KNOWN_ETF_CODES = {"088500", "161510", "371460", "484730", "490590"}


class DartAPIClient:
    """
    DART Open API를 연동하여 실제 기업 재무제표(매출, 영업이익, 순이익, 영업현금흐름,
    부채비율 및 YoY 성장률)를 수집하는 클라이언트입니다.

    데이터 무결성 원칙:
    - 알 수 없는 종목을 임의의 다른 회사 corp_code로 대체하지 않는다.
    - 최신 제출 정기보고서를 우선한다.
    - 매출 계정은 정확 계정/계정명으로만 식별한다.
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

        # Fail-closed: 종목을 삼성전자 등 임의의 다른 회사 corp_code로 대체하지 않는다.
        return self.dynamic_map.get(stock_code, "")

    @staticmethod
    def _build_latest_query_targets(asof: Optional[datetime] = None) -> List[Tuple[int, str, bool]]:
        """DART에 실제 제출된 최신 정기보고서를 찾기 위한 최신순 후보 목록."""
        now = asof or datetime.now(KST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=KST)
        else:
            now = now.astimezone(KST)
        year = now.year

        # 아직 제출 전인 보고서는 OpenDART가 status!=000으로 반환하므로 안전하게 다음 후보로 진행한다.
        # 동일 연도 내 시간 순서: Q1(11013) -> Q2/반기(11012) -> Q3(11014) -> 연간(11011).
        return [
            (year, "11014", True),
            (year, "11012", True),
            (year, "11013", True),
            (year - 1, "11011", True),
            (year - 1, "11014", True),
            (year - 1, "11012", True),
            (year - 1, "11013", True),
            (year - 2, "11011", False),
        ]

    def _fetch_financial_target(
        self,
        stock_code: str,
        corp_code: str,
        year: int,
        reprt_code: str,
        is_recent: bool,
    ) -> Optional[Dict[str, Any]]:
        """특정 사업연도/보고서코드를 CFS 우선으로 조회한다."""
        if not corp_code:
            return None

        for fs_div in ["CFS", "OFS"]:
            url = f"{self.BASE_URL}/fnlttSinglAcntAll.json"
            params = {
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            }
            try:
                response = requests.get(url, params=params, timeout=10)
                if response.status_code != 200:
                    continue
                res_json = response.json()
                if res_json.get("status") != "000":
                    continue
                items = res_json.get("list", [])
                if not items:
                    continue
                parsed = self._parse_all_dart_statement_strict(items, fs_div, year, reprt_code, is_recent)
                if parsed and (parsed.get("revenue", 0) != 0 or parsed.get("operating_profit", 0) != 0):
                    parsed["stock_code"] = stock_code
                    parsed["corp_code"] = corp_code
                    return parsed
            except Exception as e:
                logger.error(f"[DART API] {stock_code} {year}년 {reprt_code} {fs_div} 수집 예외: {e}")
        return None

    def get_financial_statement(self, stock_code: str, fiscal_year: int = 2025, reprt_code: str = "11011") -> Optional[Dict[str, Any]]:
        """
        DART 단일회사 전체 재무제표 API 연동.

        ``fiscal_year``/``reprt_code`` 인자는 기존 호출부 호환을 위해 유지하지만, 운영 F점수용
        이 메서드는 항상 현재 시점에 제출 완료된 최신 정기보고서를 선택한다. 특정 보고서가
        필요한 공시 브리핑은 ``_fetch_financial_target``을 사용한다.
        """
        if not self.is_valid_key():
            logger.warning("[DART API] API Key가 없어 재무 확정값을 만들 수 없습니다.")
            return self._get_fallback_data(stock_code, "DART_API_KEY_MISSING")

        # ETF/펀드 상품은 기업 재무제표 대상에서 원천 제외
        if stock_code in KNOWN_ETF_CODES:
            return {
                "stock_code": stock_code,
                "is_etf": True,
                "revenue": 0, "operating_profit": 0, "net_income": 0,
                "operating_cash_flow": 0, "debt_ratio": 0.0,
                "revenue_yoy": 0.0, "op_profit_yoy": 0.0,
                "audit_opinion": "적정", "disclosure_risk_flag": False,
                "f_score_confirmed": True, "sanity_pass": True,
                "data_completeness": 100.0,
            }

        corp_code = self.get_corp_code(stock_code)
        if not corp_code:
            logger.error(f"[DART API] {stock_code} corp_code 확인 실패 — 타사 재무 대체 금지")
            return self._get_fallback_data(stock_code, "CORP_CODE_UNRESOLVED")

        for yr, r_code, is_recent in self._build_latest_query_targets():
            parsed = self._fetch_financial_target(stock_code, corp_code, yr, r_code, is_recent)
            if parsed:
                logger.info(
                    f"[DART API] {stock_code} 최신 재무수집 ({yr}년 {r_code} {parsed.get('fs_div')} / "
                    f"매출:{parsed.get('revenue'):,.0f}원, 영업이익:{parsed.get('operating_profit'):,.0f}원, "
                    f"OCF:{parsed.get('operating_cash_flow'):,.0f}원, 검증성공:{parsed.get('sanity_pass')})"
                )
                return parsed

        logger.error(f"[DART API] {stock_code} 최신 정기보고서 재무를 확인하지 못했습니다.")
        return self._get_fallback_data(stock_code, "LATEST_FINANCIAL_NOT_FOUND")

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
                # 매출액: exact taxonomy/local-id 또는 정확 계정명만 허용
                if is_revenue_account(acc_id, acc_nm):
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
                if acc_id == "ifrs-full_Assets" or acc_nm in ["자산총계", "자산"]:
                    if "total_assets" not in thstrm:
                        thstrm["total_assets"] = th_val; frmtrm["total_assets"] = fr_val
                elif acc_id == "ifrs-full_Liabilities" or acc_nm in ["부채총계", "부채"]:
                    if "total_liabilities" not in thstrm:
                        thstrm["total_liabilities"] = th_val; frmtrm["total_liabilities"] = fr_val
                elif acc_id in ["ifrs-full_Equity", "ifrs-full_EquityAttributableToOwnersOfParent"] or acc_nm in ["자본총계", "자본"]:
                    if "total_equity" not in thstrm:
                        thstrm["total_equity"] = th_val; frmtrm["total_equity"] = fr_val

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

        if eq > 0 and liab > 0:
            debt_ratio = round((liab / eq) * 100.0, 2)
        elif eq > 0 and assets >= eq:
            debt_ratio = round(((assets / eq) - 1.0) * 100.0, 2)
        else:
            debt_ratio = 0.0

        if eq_fr > 0 and liab_fr > 0:
            prev_debt_ratio = round((liab_fr / eq_fr) * 100.0, 2)
        elif eq_fr > 0 and assets_fr >= eq_fr:
            prev_debt_ratio = round(((assets_fr / eq_fr) - 1.0) * 100.0, 2)
        else:
            prev_debt_ratio = debt_ratio

        rev_yoy = calc_yoy(rev_val, rev_fr)
        op_yoy = calc_yoy(op_val, op_fr)
        net_yoy = calc_yoy(net_val, net_fr)

        # 4. 재무 데이터 Sanity Check
        sanity_pass = True
        sanity_reason = []

        if rev_val > 0 and abs(op_val) > rev_val:
            sanity_pass = False
            sanity_reason.append("당기 영업이익이 매출액 초과 (계정오매핑)")
        if rev_fr > 0 and abs(op_fr) > rev_fr:
            sanity_pass = False
            sanity_reason.append("전기 영업이익이 매출액 초과 (계정오매핑)")
        if rev_val == 0 and op_val != 0:
            sanity_pass = False
            sanity_reason.append("당기 매출액 0원이나 영업이익 존재")
        if rev_fr == 0 and op_fr != 0:
            sanity_pass = False
            sanity_reason.append("전기 매출액 0원이나 영업이익 존재")
        if ocf_val == 0.0:
            sanity_pass = False
            sanity_reason.append("영업현금흐름(OCF) 미수집/NULL")
        if rev_fr == 0.0:
            sanity_pass = False
            sanity_reason.append("전기 매출액 미수집 (성장률 왜곡위험)")
        if rev_val > 0 and rev_fr > 0 and (rev_val / rev_fr > 2.5 or rev_val / rev_fr < 0.4):
            sanity_pass = False
            sanity_reason.append(f"전기 대비 매출 왜곡 변동 ({rev_yoy:+.1f}%)")
        if debt_ratio <= 0.0 or debt_ratio > 400.0:
            sanity_pass = False
            sanity_reason.append(f"부채비율 이상치 ({debt_ratio:.1f}%)")
        if not is_recent:
            sanity_pass = False
            sanity_reason.append("과거 재무자료 경과")

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
        if not corp_code:
            return self._get_fallback_data(stock_code, "CORP_CODE_UNRESOLVED")
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
                    parsed = self._parse_dart_statement(res_json.get("list", []), fiscal_year, reprt_code)
                    parsed["stock_code"] = stock_code
                    parsed["corp_code"] = corp_code
                    return parsed
        except Exception as e:
            logger.error(f"[DART API Fallback] {stock_code} 주요계정 API 오류: {e}")

        return self._get_fallback_data(stock_code, "SINGLE_ACCOUNT_FALLBACK_FAILED")

    def _parse_dart_statement(self, items: list, fiscal_year: int = 2024, reprt_code: str = "11011") -> Dict[str, Any]:
        """DART API 주요계정 파싱"""
        thstrm = {}
        frmtrm = {}

        for item in items:
            nm = item.get("account_nm", "").strip()
            th_str = str(item.get("thstrm_amount", "0") or "0").replace(",", "")
            fr_str = str(item.get("frmtrm_amount", "0") or "0").replace(",", "")

            try: th_val = float(th_str) if th_str not in ["-", ""] else 0.0
            except ValueError: th_val = 0.0

            try: fr_val = float(fr_str) if fr_str not in ["-", ""] else 0.0
            except ValueError: fr_val = 0.0

            compact_nm = nm.replace(" ", "")
            if compact_nm in ["매출액", "수익(매출액)", "매출", "영업수익"]:
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

        def calc_yoy(th, fr):
            if fr != 0:
                return round(((th - fr) / abs(fr)) * 100.0, 2)
            return 0.0

        rev_yoy = calc_yoy(thstrm.get("revenue", 0), frmtrm.get("revenue", 0))
        op_yoy = calc_yoy(thstrm.get("operating_profit", 0), frmtrm.get("operating_profit", 0))
        net_yoy = calc_yoy(thstrm.get("net_income", 0), frmtrm.get("net_income", 0))

        liab = thstrm.get("total_liabilities", 0.0)
        eq = thstrm.get("total_equity", 1.0)
        debt_ratio = round((liab / eq) * 100.0, 2) if eq > 0 else 0.0

        return {
            "fiscal_year": fiscal_year,
            "quarter_code": reprt_code,
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
            "disclosure_risk_flag": False,
            "f_score_confirmed": False,
            "sanity_pass": False,
            "data_completeness": 75.0,
            "sanity_reason": "주요계정 Fallback 사용 — 정밀 재무 미확정",
        }

    def _get_fallback_data(self, stock_code: str, reason: str = "DART_DATA_UNAVAILABLE") -> Dict[str, Any]:
        """API 오류 시 다른 회사 데이터 대신 명시적 미확정 상태를 반환한다."""
        return {
            "stock_code": stock_code,
            "fiscal_year": 0,
            "quarter_code": "UNKNOWN",
            "fs_div": "UNKNOWN",
            "revenue": 0.0, "revenue_yoy": 0.0,
            "operating_profit": 0.0, "op_profit_yoy": 0.0,
            "net_income": 0.0, "net_income_yoy": 0.0,
            "operating_cash_flow": 0.0, "debt_ratio": 0.0,
            "per": 0.0, "pbr": 0.0,
            "audit_opinion": "미확인", "disclosure_risk_flag": False,
            "f_score_confirmed": False,
            "sanity_pass": False,
            "data_completeness": 0.0,
            "sanity_reason": reason,
        }

    def get_recent_disclosures_briefing(self, stock_list: List[Dict[str, Any]], target_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        보유 종목(stock_list)에 대하여 당일(또는 지정일) 발생한 주요 DART 공시를 수집하고,
        공시 요약, 시장 의미 및 투자 대응 가이드(1~3줄) 브리핑 데이터를 생성합니다.
        """
        if not self.is_valid_key() or not stock_list:
            return []

        if not target_date:
            target_date = datetime.now(KST).strftime("%Y%m%d")

        disclosures = []
        seen_rcept_nos = set()
        logger.info(f"[DART API] {len(stock_list)}개 보유 종목의 {target_date} DART 신규 공시 검색 시작...")

        for stock in stock_list:
            code = str(stock.get("stock_code", "")).zfill(6)
            name = str(stock.get("stock_name", code))

            is_etf = stock.get("is_etf", False) or any(k in name for k in ["ETF", "PLUS", "RISE", "KODEX", "TIGER", "ACE", "SOL", "고배당주", "밸류체인"]) or code in KNOWN_ETF_CODES
            if is_etf:
                continue

            corp_code = self.get_corp_code(code)
            if not corp_code:
                logger.warning(f"[DART API] {name}({code}) corp_code 미확인 — 공시 검색 건너뜀")
                continue

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

                            if any(ign in r_nm for ign in ["지급수단별", "임원ㆍ주요주주", "약식"]):
                                continue
                            if r_no in seen_rcept_nos:
                                continue
                            seen_rcept_nos.add(r_no)

                            briefing = self._generate_disclosure_briefing(name, code, r_nm, r_no, r_dt, stock_info=stock)
                            disclosures.append(briefing)
            except Exception as e:
                logger.warning(f"[DART API] {name}({code}) 공시 검색 중 오류: {e}")

        logger.info(f"[DART API] {target_date} 보유 종목 주요 DART 공시 {len(disclosures)}건 추출 완료")
        return disclosures

    def _generate_disclosure_briefing(self, name: str, code: str, report_nm: str, rcept_no: str, rcept_dt: str, stock_info: Dict[str, Any] = None) -> Dict[str, Any]:
        """개별 공시 항목에 대한 맞춤형 실적 수치 기반 호재/중립/악재 브리핑 생성"""
        link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

        if any(k in report_nm for k in ["반기보고서", "분기보고서", "사업보고서"]):
            # 현재 운영 시점(2026) 브리핑의 기존 문구는 유지하되 F점수 기준 문구는 최신 보고서 기준으로 교정한다.
            if "사업보고서" in report_nm:
                rep_title = "사업보고서 (연간 결산)"
                summary = "연간 사업보고서 확정 재무제표 공시 접수."
                q_yr = 2025
                q_code = "11011"
            elif "반기보고서" in report_nm:
                rep_title = "2026년 반기보고서 (상반기 누적)"
                summary = "2026년 반기보고서(11012) 공시 접수. 최신 제출 정기보고서 기준으로 F점수를 갱신합니다."
                q_yr = 2026
                q_code = "11012"
            else:
                rep_title = "2026년 1분기보고서"
                summary = "2026년 분기보고서 공시 접수. 최신 제출 정기보고서 기준으로 F점수를 갱신합니다."
                q_yr = 2026
                q_code = "11013"

            corp_code = self.get_corp_code(code)
            try:
                fin = self._fetch_financial_target(code, corp_code, q_yr, q_code, is_recent=True)
                if not fin or fin.get("revenue", 0) == 0:
                    fin = self.get_financial_statement(code)
            except Exception:
                fin = None

            if fin and fin.get("fiscal_year") == 2026 and fin.get("quarter_code") == "11012" and fin.get("revenue", 0) > 0:
                rev_eok = fin.get("revenue", 0) / 100000000.0
                rev_yoy = fin.get("revenue_yoy", 0.0)
                op = fin.get("operating_profit", 0)
                op_eok = op / 100000000.0
                op_yoy = fin.get("op_profit_yoy", 0.0)
                if op > 0 and op_yoy >= 15.0:
                    impact = f"<span style='color:#059669; font-weight:bold;'>🟢 [호재 / 반기 실적 호전]</span> [2026년 상반기 누적] 매출 {rev_eok:,.0f}억원(YoY {rev_yoy:+.1f}%), 영업이익 {op_eok:,.0f}억원(YoY {op_yoy:+.1f}%)으로 어닝 서프라이즈 달성."
                elif op > 0:
                    impact = f"<span style='color:#059669; font-weight:bold;'>🟢 [호재 / 반기 흑자]</span> [2026년 상반기 누적] 매출 {rev_eok:,.0f}억원(YoY {rev_yoy:+.1f}%), 영업이익 {op_eok:,.0f}억원으로 견조한 흑자 기조 유지."
                else:
                    impact = f"<span style='color:#DC2626; font-weight:bold;'>🔴 [악재 / 반기 적자]</span> [2026년 상반기 누적] 매출 {rev_eok:,.0f}억원, 영업손실 {op_eok:,.0f}억원으로 적자 지속."
            elif "사업보고서" in report_nm and fin and fin.get("revenue", 0) > 0:
                rev_eok = fin.get("revenue", 0) / 100000000.0
                rev_yoy = fin.get("revenue_yoy", 0.0)
                op = fin.get("operating_profit", 0)
                op_eok = op / 100000000.0
                op_yoy = fin.get("op_profit_yoy", 0.0)
                if op > 0 and op_yoy >= 15.0:
                    impact = f"<span style='color:#059669; font-weight:bold;'>🟢 [호재 / 실적 호전]</span> 매출 {rev_eok:,.0f}억원(YoY {rev_yoy:+.1f}%), 영업이익 {op_eok:,.0f}억원(YoY {op_yoy:+.1f}%)으로 견조한 실적 확인."
                elif op > 0:
                    impact = f"<span style='color:#059669; font-weight:bold;'>🟢 [호재 / 견조한 흑자]</span> 매출 {rev_eok:,.0f}억원, 영업이익 {op_eok:,.0f}억원으로 흑자 결산 확인."
                else:
                    impact = f"<span style='color:#DC2626; font-weight:bold;'>🔴 [악재 / 적자 지속]</span> 매출 {rev_eok:,.0f}억원, 영업손실 {op_eok:,.0f}억원으로 적자 결산 확인."
            else:
                impact = f"<span style='color:#D97706; font-weight:bold;'>🟡 [중립 / 정기보고서 접수]</span> {rep_title} 접수 완료. 세부 실적 원천 검증이 끝날 때까지 미확정으로 취급."

        elif any(k in report_nm for k in ["단일판매", "공급계약"]):
            summary = "신규 단일판매 및 대규모 공급계약 체결 공시 접수."
            impact = "<span style='color:#059669; font-weight:bold;'>🟢 [호재 / 수주 모멘텀]</span> 대규모 공급계약 체결로 수주잔고 및 향후 매출 인식 가시성 확대. 단기 주가 상승 모멘텀."
        elif any(k in report_nm for k in ["유상증자", "전환사채", "신주인수권부사채"]):
            summary = "자금 조달 및 신주 발행(증자/사채) 주요사항보고서 접수."
            impact = "<span style='color:#DC2626; font-weight:bold;'>🔴 [경계 / 지분 희석 부담]</span> 신주 발행에 따른 주당가치 희석 및 단기 오버행(잠재 매물) 우려 공존."
        elif "무상증자" in report_nm:
            summary = "주주가치 제고를 위한 무상증자 결정 공시 접수."
            impact = "<span style='color:#059669; font-weight:bold;'>🟢 [호재 / 주주환원]</span> 유통 주식수 확대 및 주주 친화 정책으로 단기 투자심리 개선 호재."
        elif "계열회사와의상품" in report_nm:
            summary = "동일인 등 출자계열회사와의 상품·용역 거래내역 변경 공시."
            impact = "<span style='color:#64748B; font-weight:bold;'>🟡 [중립 / 내부거래 조정]</span> 그룹사 내부거래 규모 조정으로 기업 펀더멘털에 미치는 즉각적인 영향은 제한적."
        else:
            summary = f"주요 경영 사항 및 공시({report_nm}) 접수."
            impact = "<span style='color:#64748B; font-weight:bold;'>🟡 [중립 / 일반 공시]</span> 통상적 공시 사항으로 세부 내용 확인 필요."

        trade_mode = (stock_info.get("trade_mode") if stock_info else "NORMAL") or "NORMAL"
        rec_qty = stock_info.get("recommended_order_qty", 0) if stock_info else 0

        if code == "234920" or trade_mode == "SUSPENDED_HOLD":
            guide = "매매재개 전 가격·ATR 대응 금지. 상장적격성 심사, 개선기간, 거래재개 또는 상장폐지 관련 후속 공시만 감시."
        elif trade_mode == "USER_OVERRIDE":
            guide = "DART 재무 미확정에 따른 사용자 수동 감시주문 유지 및 실시간 호가 감시."
        elif trade_mode == "CONCENTRATION_RISK":
            guide = f"단일 비중 20% 초과 집중위험 종목으로, 공시 호악재와 무관하게 20% 초과 수량({rec_qty:,}주) 분할축소(33%)를 최우선 집행."
        elif trade_mode == "RECOVERY":
            guide = f"누적 손실 포지션 회복(RECOVERY) 모드로, 펀더멘털 공시와 별개로 기술적 반등 시 30%({rec_qty:,}주) 손실축소 분할매도 가이드를 최우선 준수."
        elif trade_mode == "EMERGENCY":
            guide = f"고위험 긴급축소(EMERGENCY) 모드로, 단기 반등 시 50%({rec_qty:,}주) 비중 축소 가이드 우선 준수."
        else:
            guide = "실적 및 펀더멘털 안정성에 기반하여 기존 V4 목표가 및 트레일링 손절선 기준 안정적 보유 지속."

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
