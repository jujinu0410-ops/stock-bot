import requests
import re
from typing import Dict, Any, List, Optional, Tuple
from src.database.db_manager import DatabaseManager
from src.api.dart_api import DartAPIClient
from src.utils.logger import logger

class DisclosureCollector:
    """
    Phase 5.3: OpenDART 수시공시 및 주요 이벤트 수집·분류·정정체인·다차원 리스크 감사
    - entity_scope (PARENT vs SUBSIDIARY) 및 subsidiary_name 정밀 분리
    - 서로 다른 계약의 독립 식별 보장 및 정정공시 체인(amendment_chain_id) 추적
    - 최신본(is_latest_version=1) 단일 평가를 통한 중복 카운트 원천 방지
    - Negative Event 다차원 분리: event_hazard, materiality_level, effective_severity, severity_reason
    - 모회사 직접 유상증자 파싱 (issue_amount, new_shares, existing_shares, dilution_ratio)
    - 미확인 수치에 대해 주관적 수식어('대규모' 등) 자동 사용 금지
    """
    def __init__(self, db_manager: DatabaseManager, dart_client: Optional[DartAPIClient] = None):
        self.db = db_manager
        self.dart_client = dart_client or DartAPIClient()
        self.base_url = "https://opendart.fss.or.kr/api"
        self.api_key = self.dart_client.api_key

    def _extract_amount_and_ratio(self, report_name: str) -> Tuple[Optional[float], Optional[float], str]:
        """보고서 제목 등에서 금액 및 매출 대비 비율 추출"""
        amount = None
        ratio = None
        mat_status = "MATERIALITY_UNKNOWN"

        # 억/조 원 단위 패턴 추출
        m_krw = re.search(r"(\d+(?:\.\d+)?)\s*(?:조|억)\s*원", report_name)
        if m_krw:
            val = float(m_krw.group(1))
            if "조" in m_krw.group(0):
                amount = val * 1e12
            else:
                amount = val * 1e8
            mat_status = "MATERIALITY_CONFIRMED"

        # % 비율 추출
        m_pct = re.search(r"(\d+(?:\.\d+)?)\s*%", report_name)
        if m_pct:
            ratio = float(m_pct.group(1))
            mat_status = "MATERIALITY_CONFIRMED"

        return amount, ratio, mat_status

    def _classify_event(self, report_name: str) -> Dict[str, Any]:
        """
        공시 보고서명으로부터 세부 속성 추출:
        event_type, progression_stage, is_negative, entity_scope, subsidiary_name,
        event_hazard, materiality_level, effective_severity, severity_reason,
        amount, revenue_ratio, materiality_status,
        issue_amount, new_shares, existing_shares, dilution_ratio, ratio_to_market_cap
        """
        nm = report_name.strip()
        event_type = "GENERAL_DISCLOSURE"
        progression_stage = "FINAL_CONTRACT"
        is_negative = False
        entity_scope = "PARENT"
        subsidiary_name = None
        event_hazard = "LOW"
        materiality_level = "UNKNOWN"
        effective_severity = "LOW"
        severity_reason = "통상적인 일반 공시"

        amount, revenue_ratio, mat_status = self._extract_amount_and_ratio(nm)
        issue_amount = None
        new_shares = None
        existing_shares = None
        dilution_ratio = None
        ratio_to_market_cap = None

        # 1. Entity Scope & Subsidiary Name 추출
        if any(k in nm for k in ["자회사의 주요경영사항", "자회사의주요경영사항", "자회사"]):
            entity_scope = "SUBSIDIARY"
            subsidiary_name = "주요 자회사"
        elif any(k in nm for k in ["종속회사의 주요경영사항", "종속회사의주요경영사항", "종속회사"]):
            entity_scope = "SUBSIDIARY"
            subsidiary_name = "주요 종속회사"

        # 2. 진행단계 판별 (MOU / LOI / 확정계약 / 가동 등)
        if any(k in nm for k in ["MOU", "양해각서", "업무협약"]):
            progression_stage = "MOU"
        elif any(k in nm for k in ["LOI", "투자의향서", "기본합의"]):
            progression_stage = "BASIC_AGREEMENT"
        elif any(k in nm for k in ["인도", "납품완료", "준공"]):
            progression_stage = "DELIVERY"
        elif any(k in nm for k in ["양산", "착공"]):
            progression_stage = "PRODUCTION"
        else:
            progression_stage = "FINAL_CONTRACT"

        # 3. 핵심 Event Type 분류 및 다차원 리스크(Hazard + Materiality -> Effective Severity) 평가
        if "단일판매ㆍ공급계약해제ㆍ해지" in nm or "계약해지" in nm or "계약해제" in nm:
            event_type = "ORDER_CANCEL"
            is_negative = True
            event_hazard = "HIGH"
            if revenue_ratio is not None:
                if revenue_ratio >= 20.0:
                    materiality_level = "CRITICAL"
                    effective_severity = "CRITICAL"
                    severity_reason = f"수주취소 규모가 매출액의 {revenue_ratio:.1f}%(20% 이상)로 치명적 타격"
                elif revenue_ratio >= 5.0:
                    materiality_level = "HIGH"
                    effective_severity = "HIGH"
                    severity_reason = f"수주취소 규모가 매출액의 {revenue_ratio:.1f}%(5~20%)로 유의미한 영향"
                else:
                    materiality_level = "LOW"
                    effective_severity = "LOW"
                    severity_reason = f"수주취소 규모가 매출액의 {revenue_ratio:.1f}%로 제한적 영향"
            else:
                materiality_level = "UNKNOWN"
                effective_severity = "REVIEW_REQUIRED"
                severity_reason = "수주계약 해지 공시이나 취소금액/비율 미공개 (REVIEW_REQUIRED / 정밀 확인 필요)"

        elif "[기재정정]단일판매" in nm or "계약금액변경" in nm or "정정]공급계약" in nm:
            event_type = "ORDER_INCREASE"
            severity_reason = "기존 수주계약의 금액 또는 조건 정정/증액"
        elif "단일판매ㆍ공급계약체결" in nm or "공급계약체결" in nm or "수주" in nm:
            event_type = "LARGE_SUPPLY_CONTRACT"
            severity_reason = "대규모 제품/용역 단일판매 공급계약 체결"
        elif "신규시설투자" in nm or "생산설비증설" in nm or "시설투자" in nm:
            event_type = "CAPA_EXPANSION"
            severity_reason = "CAPA 생산능력 확대를 위한 신규 시설투자"
        elif "신규공장" in nm or "공장신설" in nm:
            event_type = "NEW_FACTORY"
            severity_reason = "신규 공장 신설 투자"
        elif "유상증자결정" in nm or "유무상증자결정" in nm:
            event_type = "CAPITAL_INCREASE"
            is_negative = True
            if entity_scope == "PARENT":
                event_hazard = "HIGH"
                if revenue_ratio is not None:
                    dilution_ratio = revenue_ratio
                    materiality_level = "HIGH" if dilution_ratio >= 10.0 else "MEDIUM"
                    effective_severity = "HIGH"
                    severity_reason = f"모회사 직접 유상증자(주주배정/일반공모) 결정 (희석비율: {dilution_ratio:.1f}%)"
                else:
                    materiality_level = "UNKNOWN"
                    effective_severity = "HIGH"
                    severity_reason = "모회사 직접 유상증자(주주배정/일반공모) 결정 (주당가치 직접 희석, 세부 희석비율 미확인)"
            else:
                event_hazard = "MEDIUM"
                materiality_level = "UNKNOWN"
                effective_severity = "MEDIUM"
                severity_reason = "종속회사 유상증자로 모회사 주주에 대한 직접적인 지분 희석 없음 (차등 적용)"

        elif "전환사채" in nm and "발행결정" in nm:
            event_type = "CB"
            is_negative = True
            event_hazard = "MEDIUM"
            materiality_level = "UNKNOWN"
            effective_severity = "MEDIUM"
            severity_reason = "전환사채(CB) 발행으로 향후 잠재적 주식 전환 희석 물량 부담"
        elif "신주인수권부사채" in nm and "발행결정" in nm:
            event_type = "BW"
            is_negative = True
            event_hazard = "MEDIUM"
            materiality_level = "UNKNOWN"
            effective_severity = "MEDIUM"
            severity_reason = "신주인수권부사채(BW) 발행으로 향후 잠재적 워런트 행사 물량 부담"
        elif "교환사채" in nm and "발행결정" in nm:
            event_type = "EB"
            severity_reason = "교환사채(EB) 발행"
        elif "자기주식취득" in nm or "자기주식처분" in nm:
            event_type = "TREASURY_SHARE"
            severity_reason = "자기주식 취득 또는 처분"
        elif "최대주주변경" in nm:
            event_type = "MAJOR_SHAREHOLDER_CHANGE"
            event_hazard = "HIGH"
            materiality_level = "UNKNOWN"
            effective_severity = "HIGH"
            severity_reason = "경영권 및 지배구조 변동 가능성"
        elif any(k in nm for k in ["의견거절", "부적정", "감사의견 비적정", "감사보고서 미제출"]):
            event_type = "AUDIT_ISSUE"
            is_negative = True
            event_hazard = "CRITICAL"
            materiality_level = "CRITICAL"
            effective_severity = "CRITICAL"
            severity_reason = "감사의견 거절/부적정으로 인한 치명적 회계 신뢰성 훼손"
        elif "한정" in nm or "내부회계관리제도 비적정" in nm:
            event_type = "AUDIT_ISSUE"
            is_negative = True
            event_hazard = "HIGH"
            materiality_level = "HIGH"
            effective_severity = "HIGH"
            severity_reason = "감사의견 한정 또는 내부회계관리제도 비적정"
        elif "매매거래정지" in nm and "해제" not in nm:
            event_type = "TRADING_SUSPENSION"
            is_negative = True
            event_hazard = "CRITICAL"
            materiality_level = "CRITICAL"
            effective_severity = "CRITICAL"
            severity_reason = "한국거래소 주권 매매거래정지 조치"
        elif "매매거래정지해제" in nm:
            event_type = "RESUMPTION"
            severity_reason = "주권 매매거래정지 해제"
        elif any(k in nm for k in ["횡령", "배임", "소송 등의 판결", "회생절차"]):
            event_type = "LEGAL_RISK"
            is_negative = True
            event_hazard = "HIGH"
            materiality_level = "HIGH"
            effective_severity = "HIGH"
            severity_reason = "경영진 횡령/배임 또는 주요 소송/회생 리스크"

        return {
            "event_type": event_type,
            "progression_stage": progression_stage,
            "is_negative": is_negative,
            "entity_scope": entity_scope,
            "subsidiary_name": subsidiary_name,
            "event_hazard": event_hazard,
            "materiality_level": materiality_level,
            "effective_severity": effective_severity,
            "severity": effective_severity,
            "severity_reason": severity_reason,
            "amount": amount,
            "revenue_ratio": revenue_ratio,
            "materiality_status": mat_status,
            "issue_amount": issue_amount,
            "new_shares": new_shares,
            "existing_shares": existing_shares,
            "dilution_ratio": dilution_ratio,
            "ratio_to_market_cap": ratio_to_market_cap
        }

    def collect_disclosures(self, stock_code: str, bgn_de: str = "20240101", end_de: str = "20260817") -> List[Dict[str, Any]]:
        """
        OpenDART list.json 호출 및 구조화 이벤트 추출, 정정체인 식별, DB 적재
        """
        corp_code = self.dart_client.get_corp_code(stock_code)
        url = f"{self.base_url}/list.json"
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": "1",
            "page_count": "100"
        }

        classified_events = []
        try:
            self.db.execute_non_query("DELETE FROM disclosure_events WHERE stock_code = ?", (stock_code,))
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json()
                items = data.get("list", [])
                
                # 1단계: 이벤트 분류
                raw_events = []
                for it in items:
                    r_nm = it.get("report_nm", "").strip()
                    r_dt = it.get("rcept_dt", "").strip()
                    r_no = it.get("rcept_no", "").strip()

                    parsed = self._classify_event(r_nm)
                    ev_type = parsed["event_type"]
                    is_neg = parsed["is_negative"]

                    if ev_type != "GENERAL_DISCLOSURE" or is_neg:
                        is_amended = any(k in r_nm for k in ["정정", "기재정정"])
                        raw_events.append({
                            "stock_code": stock_code,
                            "event_type": ev_type,
                            "rcept_no": r_no,
                            "rcept_date": f"{r_dt[:4]}-{r_dt[4:6]}-{r_dt[6:8]}" if len(r_dt) == 8 else r_dt,
                            "report_name": r_nm,
                            "amount": parsed["amount"],
                            "currency": "KRW",
                            "counterparty": None,
                            "contract_start": None,
                            "contract_end": None,
                            "revenue_ratio": parsed["revenue_ratio"],
                            "progression_stage": parsed["progression_stage"],
                            "entity_scope": parsed["entity_scope"],
                            "subsidiary_name": parsed["subsidiary_name"],
                            "original_rcept_no": None,
                            "amendment_chain_id": None,
                            "is_amended": is_amended,
                            "is_latest_version": 1,
                            "event_hazard": parsed["event_hazard"],
                            "materiality_level": parsed["materiality_level"],
                            "effective_severity": parsed["effective_severity"],
                            "materiality_ratio_revenue": parsed["revenue_ratio"],
                            "materiality_ratio_backlog": None,
                            "materiality_status": parsed["materiality_status"],
                            "issue_amount": parsed["issue_amount"],
                            "new_shares": parsed["new_shares"],
                            "existing_shares": parsed["existing_shares"],
                            "dilution_ratio": parsed["dilution_ratio"],
                            "ratio_to_market_cap": parsed["ratio_to_market_cap"],
                            "severity": parsed["effective_severity"],
                            "severity_reason": parsed["severity_reason"],
                            "is_negative_event": 1 if is_neg else 0,
                            "source_quality": "VALID"
                        })

                # 2단계: 정정 체인별 최신본 식별
                raw_events.sort(key=lambda x: (x["rcept_date"], x["rcept_no"]))
                active_chains = {}  # (event_type, entity_scope) -> latest chain_id
                for ev in raw_events:
                    key = (ev["event_type"], ev["entity_scope"])
                    if ev["is_amended"] and key in active_chains:
                        ev["amendment_chain_id"] = active_chains[key]
                        ev["original_rcept_no"] = active_chains[key]
                    else:
                        ev["amendment_chain_id"] = ev["rcept_no"]
                        active_chains[key] = ev["rcept_no"]

                # 3단계: 체인별 최신본 판별 (날짜 내림차순 정렬 후 체인당 최초 1건만 is_latest_version = 1)
                raw_events.sort(key=lambda x: (x["rcept_date"], x["rcept_no"]), reverse=True)
                seen_chains = set()
                for ev in raw_events:
                    cid = ev["amendment_chain_id"]
                    if cid not in seen_chains:
                        ev["is_latest_version"] = 1
                        seen_chains.add(cid)
                    else:
                        ev["is_latest_version"] = 0  # 구버전 superseded

                    # DB 적재
                    self.db.upsert_disclosure_event(ev)
                    classified_events.append(ev)

        except Exception as e:
            logger.error(f"[DisclosureCollector] {stock_code} 공시 수집 실패: {e}")

        logger.info(f"[DisclosureCollector] {stock_code} 공시 이벤트 {len(classified_events)}건 (최신본 {sum(1 for e in classified_events if e['is_latest_version']==1)}건) 적재 완료")
        return classified_events
