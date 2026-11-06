import requests
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional, Tuple
from config.settings import DART_API_KEY
from src.database.db_manager import DatabaseManager
from src.api.dart_api import DartAPIClient
from src.api.dart_account_utils import is_revenue_account
from src.utils.logger import logger


KST = ZoneInfo("Asia/Seoul")


class QuarterlyDartCollector:
    """
    Phase 4.1: OpenDART 동적 최신 정기보고서 자동 탐색 및 최근 8개 완료 분기 수집기
    - 현재 KST 기준으로 최근 3개 사업연도의 제출 가능 분기를 동적 구성
    - latest_available_fiscal_period 기준 역순으로 정확히 8개 완료 분기 구성
    - FLOW 계정 역산 (De-cumulation) & STOCK 분기말 잔액 보존
    - CFS 우선 수집, 필요 시 해당 보고서만 OFS fallback
    - 데이터 신선도(Freshness), 경과일수(age_days), 품질(quality) 추적
    """
    def __init__(self, db_manager: DatabaseManager, dart_client: Optional[DartAPIClient] = None):
        self.db = db_manager
        self.dart_client = dart_client or DartAPIClient()
        self.base_url = "https://opendart.fss.or.kr/api"
        self.api_key = self.dart_client.api_key

    def _parse_amount(self, val: Any) -> float:
        if val is None:
            return 0.0
        s = str(val).replace(",", "").strip()
        if s in ["-", "", "None", "null"]:
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    @staticmethod
    def _build_potential_quarters(current_year: int) -> List[Tuple[int, str, str, str]]:
        quarters: List[Tuple[int, str, str, str]] = []
        for yr in range(current_year - 2, current_year + 1):
            quarters.extend([
                (yr, "11013", "Q1", f"{yr}-03-31"),
                (yr, "11012", "Q2", f"{yr}-06-30"),
                (yr, "11014", "Q3", f"{yr}-09-30"),
                (yr, "11011", "Q4", f"{yr}-12-31"),
            ])
        return quarters

    def _fetch_single_report_items(self, corp_code: str, year: int, reprt_code: str, fs_div: str) -> Tuple[List[Dict[str, Any]], str, str]:
        """DART fnlttSinglAcntAll.json 호출 및 (items, rcept_no, status) 반환"""
        if not corp_code:
            return [], "", "CORP_CODE_UNRESOLVED"
        url = f"{self.base_url}/fnlttSinglAcntAll.json"
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
            "fs_div": fs_div
        }
        try:
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json()
                status = data.get("status")
                items = data.get("list", [])
                rcept_no = items[0].get("rcept_no", "") if items else ""
                return items, rcept_no, status
        except Exception as e:
            logger.error(f"[QuarterlyDart] {corp_code} {year}년 {reprt_code} {fs_div} 수집 실패: {e}")
        return [], "", "ERROR"

    def _extract_raw_accounts_from_items(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """단일 보고서 items로부터 FLOW 누적/개별 및 STOCK 분기말 잔액 추출"""
        data = {
            "rev_discrete": None, "rev_cum": None,
            "op_discrete": None, "op_cum": None,
            "net_discrete": None, "net_cum": None,
            "ocf_cum": None,
            "total_assets": None, "total_liabilities": None, "total_equity": None,
            "inventory": None, "accounts_receivable": None, "cash_and_equivalents": None,
            "short_term_debt": 0.0, "long_term_debt": 0.0, "bonds": 0.0, "lease_debt": 0.0,
            "capex": None, "r_and_d": None
        }

        for item in items:
            sj = item.get("sj_div", "").strip().upper()
            acc_id = item.get("account_id", "").strip()
            acc_nm = item.get("account_nm", "").strip().replace(" ", "")

            th = self._parse_amount(item.get("thstrm_amount"))
            th_add = self._parse_amount(item.get("thstrm_add_amount"))

            if sj in ["IS", "CIS"]:
                if is_revenue_account(acc_id, acc_nm):
                    if data["rev_cum"] is None:
                        data["rev_cum"] = th_add if th_add != 0 else th
                        data["rev_discrete"] = th if th_add != 0 else None
                elif any(x in acc_id for x in ["OperatingIncomeLoss"]) or acc_nm in ["영업이익", "영업이익(손실)"]:
                    if data["op_cum"] is None:
                        data["op_cum"] = th_add if th_add != 0 else th
                        data["op_discrete"] = th if th_add != 0 else None
                elif any(x in acc_id for x in ["ProfitLoss"]) or acc_nm in ["당기순이익", "당기순이익(손실)", "연결당기순이익"]:
                    if data["net_cum"] is None:
                        data["net_cum"] = th_add if th_add != 0 else th
                        data["net_discrete"] = th if th_add != 0 else None

            elif sj == "CF":
                if any(x in acc_id for x in ["CashFlowsFromUsedInOperatingActivities", "OperatingActivities"]) or "영업활동" in acc_nm:
                    if data["ocf_cum"] is None:
                        data["ocf_cum"] = th_add if th_add != 0 else th
                elif any(x in acc_id for x in ["PurchasesOfPropertyPlantAndEquipment", "AcquisitionOfPropertyPlantAndEquipment"]) or "유형자산의취득" in acc_nm:
                    if data["capex"] is None:
                        data["capex"] = abs(th_add if th_add != 0 else th)

            elif sj == "BS":
                if acc_id == "ifrs-full_Assets" or acc_nm in ["자산총계", "자산"]:
                    if data["total_assets"] is None: data["total_assets"] = th
                elif acc_id == "ifrs-full_Liabilities" or acc_nm in ["부채총계", "부채"]:
                    if data["total_liabilities"] is None: data["total_liabilities"] = th
                elif acc_id in ["ifrs-full_Equity", "ifrs-full_EquityAttributableToOwnersOfParent"] or acc_nm in ["자본총계", "자본"]:
                    if data["total_equity"] is None: data["total_equity"] = th
                elif acc_id == "ifrs-full_Inventories" or "재고자산" in acc_nm:
                    if data["inventory"] is None: data["inventory"] = th
                elif acc_id == "ifrs-full_CurrentTradeReceivables" or acc_nm in ["매출채권", "매출채권및기타유동채권"]:
                    if data["accounts_receivable"] is None: data["accounts_receivable"] = th
                elif acc_id == "ifrs-full_CashAndCashEquivalents" or acc_nm in ["현금및현금성자산"]:
                    if data["cash_and_equivalents"] is None: data["cash_and_equivalents"] = th
                elif "단기차입금" in acc_nm or "ShorttermBorrowings" in acc_id:
                    data["short_term_debt"] += th
                elif "유동성장기부채" in acc_nm or "유동성장기차입금" in acc_nm or "유동성사채" in acc_nm:
                    data["short_term_debt"] += th
                elif "장기차입금" in acc_nm or "LongtermBorrowings" in acc_id:
                    data["long_term_debt"] += th
                elif acc_nm == "사채" or "BondsIssued" in acc_id:
                    data["bonds"] += th
                elif "리스부채" in acc_nm:
                    data["lease_debt"] += th

        return data

    def collect_8quarters_financials(self, stock_code: str) -> Dict[str, Any]:
        """종목별 최신 정기보고서를 동적 탐색하고 최근 8개 완료 분기를 DB에 적재한다."""
        corp_code = self.dart_client.get_corp_code(stock_code)
        if not corp_code:
            logger.warning(f"[QuarterlyDart] {stock_code} corp_code 미확인")
            return {
                "records": [],
                "financial_data_asof": "N/A",
                "latest_fiscal_quarter": "N/A",
                "quarterly_data_age_days": 999,
                "quarterly_data_quality": "INVALID",
                "oldest_included_quarter": "N/A",
                "latest_included_quarter": "N/A"
            }

        today = datetime.now(KST).date()
        current_year = today.year
        potential_quarters = self._build_potential_quarters(current_year)
        years = sorted({yr for yr, _, _, _ in potential_quarters})

        # 직전 사업연도 연간보고서의 CFS 유무로 우선 fs_div 결정
        sample_year = current_year - 1
        sample_items, _, sample_st = self._fetch_single_report_items(corp_code, sample_year, "11011", "CFS")
        if sample_st == "000" and len(sample_items) > 0:
            selected_fs_div = "CFS"
        else:
            _, _, ofs_st = self._fetch_single_report_items(corp_code, sample_year, "11011", "OFS")
            selected_fs_div = "OFS" if ofs_st == "000" else "CFS"

        latest_idx = -1
        raw_reports: Dict[Tuple[int, str], Dict[str, Any]] = {}

        for idx, (yr, r_code, q_label, p_end) in enumerate(potential_quarters):
            used_fs_div = selected_fs_div
            items, rcept_no, st = self._fetch_single_report_items(corp_code, yr, r_code, used_fs_div)
            if st != "000" or not items:
                used_fs_div = "OFS" if selected_fs_div == "CFS" else "CFS"
                items, rcept_no, st = self._fetch_single_report_items(corp_code, yr, r_code, used_fs_div)

            if st == "000" and len(items) > 0:
                latest_idx = idx
                raw_reports[(yr, q_label)] = {
                    "items": items,
                    "rcept_no": rcept_no,
                    "rcept_date": rcept_no[:8] if len(str(rcept_no)) >= 8 and str(rcept_no)[:8].isdigit() else None,
                    "report_code": r_code,
                    "period_end": p_end,
                    "status": st,
                    "fs_div": used_fs_div,
                    "raw": self._extract_raw_accounts_from_items(items)
                }

        if latest_idx == -1:
            logger.warning(f"[QuarterlyDart] {stock_code} 사용 가능한 재무보고서가 없습니다.")
            return {
                "records": [],
                "financial_data_asof": "N/A",
                "latest_fiscal_quarter": "N/A",
                "quarterly_data_age_days": 999,
                "quarterly_data_quality": "INVALID",
                "oldest_included_quarter": "N/A",
                "latest_included_quarter": "N/A"
            }

        all_processed_quarters: List[Dict[str, Any]] = []

        for yr in years:
            q1_raw = raw_reports.get((yr, "Q1"), {}).get("raw", {})
            q2_raw = raw_reports.get((yr, "Q2"), {}).get("raw", {})
            q3_raw = raw_reports.get((yr, "Q3"), {}).get("raw", {})
            q4_raw = raw_reports.get((yr, "Q4"), {}).get("raw", {})

            q1_rev_cum = q1_raw.get("rev_cum") or 0.0
            q2_rev_cum = q2_raw.get("rev_cum") or 0.0
            q3_rev_cum = q3_raw.get("rev_cum") or 0.0
            fy_rev_cum = q4_raw.get("rev_cum") or 0.0
            q1_op_cum = q1_raw.get("op_cum") or 0.0
            q2_op_cum = q2_raw.get("op_cum") or 0.0
            q3_op_cum = q3_raw.get("op_cum") or 0.0
            fy_op_cum = q4_raw.get("op_cum") or 0.0
            q1_net_cum = q1_raw.get("net_cum") or 0.0
            q2_net_cum = q2_raw.get("net_cum") or 0.0
            q3_net_cum = q3_raw.get("net_cum") or 0.0
            fy_net_cum = q4_raw.get("net_cum") or 0.0
            q1_ocf_cum = q1_raw.get("ocf_cum") or 0.0
            q2_ocf_cum = q2_raw.get("ocf_cum") or 0.0
            q3_ocf_cum = q3_raw.get("ocf_cum") or 0.0
            fy_ocf_cum = q4_raw.get("ocf_cum") or 0.0

            for q_label, r_code, p_end in [
                ("Q1", "11013", f"{yr}-03-31"),
                ("Q2", "11012", f"{yr}-06-30"),
                ("Q3", "11014", f"{yr}-09-30"),
                ("Q4", "11011", f"{yr}-12-31"),
            ]:
                if (yr, q_label) not in raw_reports:
                    continue

                rep_meta = raw_reports[(yr, q_label)]
                raw = rep_meta.get("raw", {})
                rcept_no = rep_meta.get("rcept_no", "")

                if q_label == "Q1":
                    q_rev = q1_rev_cum
                    q_op = q1_op_cum
                    q_net = q1_net_cum
                    q_ocf = q1_ocf_cum
                elif q_label == "Q2":
                    q_rev = raw.get("rev_discrete") if raw.get("rev_discrete") is not None else (q2_rev_cum - q1_rev_cum)
                    q_op = raw.get("op_discrete") if raw.get("op_discrete") is not None else (q2_op_cum - q1_op_cum)
                    q_net = raw.get("net_discrete") if raw.get("net_discrete") is not None else (q2_net_cum - q1_net_cum)
                    q_ocf = q2_ocf_cum - q1_ocf_cum
                elif q_label == "Q3":
                    q_rev = raw.get("rev_discrete") if raw.get("rev_discrete") is not None else (q3_rev_cum - q2_rev_cum)
                    q_op = raw.get("op_discrete") if raw.get("op_discrete") is not None else (q3_op_cum - q2_op_cum)
                    q_net = raw.get("net_discrete") if raw.get("net_discrete") is not None else (q3_net_cum - q2_net_cum)
                    q_ocf = q3_ocf_cum - q2_ocf_cum
                else:
                    q_rev = fy_rev_cum - q3_rev_cum if fy_rev_cum > 0 and q3_rev_cum > 0 else 0.0
                    q_op = fy_op_cum - q3_op_cum if fy_op_cum != 0 and q3_op_cum != 0 else (fy_op_cum if q3_op_cum == 0 else 0.0)
                    q_net = fy_net_cum - q3_net_cum if fy_net_cum != 0 and q3_net_cum != 0 else (fy_net_cum if q3_net_cum == 0 else 0.0)
                    q_ocf = fy_ocf_cum - q3_ocf_cum if fy_ocf_cum != 0 and q3_ocf_cum != 0 else (fy_ocf_cum if q3_ocf_cum == 0 else 0.0)

                assets = raw.get("total_assets") or 0.0
                liab = raw.get("total_liabilities") or 0.0
                equity = raw.get("total_equity") or 0.0
                inv = raw.get("inventory") or 0.0
                rec = raw.get("accounts_receivable") or 0.0
                cash = raw.get("cash_and_equivalents") or 0.0
                int_debt = raw.get("short_term_debt", 0.0) + raw.get("long_term_debt", 0.0) + raw.get("bonds", 0.0) + raw.get("lease_debt", 0.0)
                net_debt = int_debt - cash
                debt_ratio = round((liab / equity * 100.0), 2) if equity > 0 else 0.0
                op_margin = round((q_op / q_rev * 100.0), 2) if q_rev > 0 else 0.0

                source_qual = "VALID"
                if q_rev == 0.0 and q_op == 0.0:
                    source_qual = "DATA_PARTIAL"

                record = {
                    "stock_code": stock_code,
                    "fiscal_year": yr,
                    "fiscal_quarter": q_label,
                    "fiscal_period_end": p_end,
                    "fs_div": rep_meta.get("fs_div", selected_fs_div),
                    "revenue": q_rev,
                    "operating_income": q_op,
                    "operating_margin": op_margin,
                    "net_income": q_net,
                    "operating_cash_flow": q_ocf,
                    "total_assets": assets,
                    "total_liabilities": liab,
                    "total_equity": equity,
                    "inventory": inv,
                    "accounts_receivable": rec,
                    "cash_and_equivalents": cash,
                    "interest_bearing_debt": int_debt,
                    "net_debt": net_debt,
                    "debt_ratio": debt_ratio,
                    "capex": raw.get("capex"),
                    "r_and_d": raw.get("r_and_d"),
                    "rcept_no": rcept_no,
                    "rcept_date": rep_meta.get("rcept_date"),
                    "report_code": r_code,
                    "is_amended": 0,
                    "source_quality": source_qual
                }

                self.db.upsert_quarterly_financial(record)
                all_processed_quarters.append(record)

        all_processed_quarters.sort(key=lambda x: (x["fiscal_year"], x["fiscal_quarter"]))
        target_8q = all_processed_quarters[-8:] if len(all_processed_quarters) >= 8 else all_processed_quarters

        latest_rec = target_8q[-1] if target_8q else {}
        oldest_rec = target_8q[0] if target_8q else {}
        latest_fq = f"{latest_rec.get('fiscal_year')} {latest_rec.get('fiscal_quarter')}"
        oldest_fq = f"{oldest_rec.get('fiscal_year')} {oldest_rec.get('fiscal_quarter')}"
        as_of_date = latest_rec.get("fiscal_period_end", "N/A")

        fiscal_period_end = as_of_date
        fiscal_period_age_days = 0
        if fiscal_period_end != "N/A":
            try:
                asof_d = datetime.strptime(fiscal_period_end, "%Y-%m-%d").date()
                fiscal_period_age_days = max(0, (today - asof_d).days)
            except Exception:
                fiscal_period_age_days = 0

        filing_received_date = latest_rec.get("rcept_date") or today.strftime("%Y%m%d")
        if len(str(filing_received_date)) == 8 and str(filing_received_date).isdigit():
            filing_received_date = f"{str(filing_received_date)[:4]}-{str(filing_received_date)[4:6]}-{str(filing_received_date)[6:8]}"

        try:
            rcpt_d = datetime.strptime(filing_received_date, "%Y-%m-%d").date()
            filing_age_days = max(0, (today - rcpt_d).days)
        except Exception:
            filing_age_days = 0

        if f"{current_year} Q3" in latest_fq:
            q_quality = f"VALID ({current_year} Q3 분기보고서 최신 반영)"
        elif f"{current_year} Q2" in latest_fq:
            q_quality = f"VALID ({current_year} Q2 반기보고서 최신 반영)"
        elif f"{current_year} Q1" in latest_fq:
            q_quality = f"LATEST_AVAILABLE_Q1_{current_year} ({current_year} 1분기보고서 반영)"
        elif f"{current_year - 1} Q4" in latest_fq:
            q_quality = f"LATEST_AVAILABLE_FY{current_year - 1} ({current_year - 1} 사업보고서 반영)"
        else:
            q_quality = "DATA_PARTIAL"

        logger.info(
            f"[QuarterlyDart] {stock_code} 8분기 Window: [{oldest_fq} ~ {latest_fq}] "
            f"(PeriodEnd: {fiscal_period_end} ({fiscal_period_age_days}일 전) | "
            f"Filing: {filing_received_date} ({filing_age_days}일 전) | Quality: {q_quality})"
        )

        return {
            "records": target_8q,
            "all_records": all_processed_quarters,
            "fiscal_period_end": fiscal_period_end,
            "fiscal_period_age_days": fiscal_period_age_days,
            "filing_received_date": filing_received_date,
            "filing_age_days": filing_age_days,
            "financial_data_asof": fiscal_period_end,
            "latest_fiscal_quarter": latest_fq,
            "quarterly_data_age_days": fiscal_period_age_days,
            "quarterly_data_quality": q_quality,
            "oldest_included_quarter": oldest_fq,
            "latest_included_quarter": latest_fq
        }
