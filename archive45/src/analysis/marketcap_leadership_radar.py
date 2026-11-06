# -*- coding: utf-8 -*-
"""
MarketCap Leadership Radar (Sidecar Module - Phase A & B)
- 한국 대형주 35개 유니버스 기반 시가총액 순위 및 리더십 구간 추적
- RankBand: TOP_9, RANK_10_20, RANK_21_30, RANK_31_50, OTHER, UNKNOWN
- 이메일 요약 연동: RANK_10_20 및 RANK_21_30 상위 종목 최대 5개 선별 노출
- Shadow Core, ATR V4, 45M ADD, 주문 로직과 100% 격리된 순수 읽기/표시 전용 모듈
"""

import html
from typing import List, Dict, Any, Optional

MARKETCAP_RADAR_UNIVERSE = [
    {"active": True, "stock_name": "삼성전자", "market": "KOSPI", "stock_code": "005930", "ticker": "KRX:005930", "sector": "전기전자"},
    {"active": True, "stock_name": "SK하이닉스", "market": "KOSPI", "stock_code": "000660", "ticker": "KRX:000660", "sector": "전기전자"},
    {"active": True, "stock_name": "LG에너지솔루션", "market": "KOSPI", "stock_code": "373220", "ticker": "KRX:373220", "sector": "전기전자"},
    {"active": True, "stock_name": "삼성바이오로직스", "market": "KOSPI", "stock_code": "207940", "ticker": "KRX:207940", "sector": "의약품"},
    {"active": True, "stock_name": "현대차", "market": "KOSPI", "stock_code": "005380", "ticker": "KRX:005380", "sector": "운수장비"},
    {"active": True, "stock_name": "기아", "market": "KOSPI", "stock_code": "000270", "ticker": "KRX:000270", "sector": "운수장비"},
    {"active": True, "stock_name": "셀트리온", "market": "KOSPI", "stock_code": "068270", "ticker": "KRX:068270", "sector": "의약품"},
    {"active": True, "stock_name": "KB금융", "market": "KOSPI", "stock_code": "105560", "ticker": "KRX:105560", "sector": "금융업"},
    {"active": True, "stock_name": "POSCO홀딩스", "market": "KOSPI", "stock_code": "005490", "ticker": "KRX:005490", "sector": "철강금속"},
    {"active": True, "stock_name": "NAVER", "market": "KOSPI", "stock_code": "035420", "ticker": "KRX:035420", "sector": "서비스업"},
    {"active": True, "stock_name": "신한지주", "market": "KOSPI", "stock_code": "055550", "ticker": "KRX:055550", "sector": "금융업"},
    {"active": True, "stock_name": "HD현대중공업", "market": "KOSPI", "stock_code": "329180", "ticker": "KRX:329180", "sector": "운수장비"},
    {"active": True, "stock_name": "삼성물산", "market": "KOSPI", "stock_code": "028260", "ticker": "KRX:028260", "sector": "유통업"},
    {"active": True, "stock_name": "현대모비스", "market": "KOSPI", "stock_code": "012330", "ticker": "KRX:012330", "sector": "운수장비"},
    {"active": True, "stock_name": "하나금융지주", "market": "KOSPI", "stock_code": "086790", "ticker": "KRX:086790", "sector": "금융업"},
    {"active": True, "stock_name": "LG화학", "market": "KOSPI", "stock_code": "051910", "ticker": "KRX:051910", "sector": "화학"},
    {"active": True, "stock_name": "삼성SDI", "market": "KOSPI", "stock_code": "006400", "ticker": "KRX:006400", "sector": "전기전자"},
    {"active": True, "stock_name": "메리츠금융지주", "market": "KOSPI", "stock_code": "138040", "ticker": "KRX:138040", "sector": "금융업"},
    {"active": True, "stock_name": "카카오", "market": "KOSPI", "stock_code": "035720", "ticker": "KRX:035720", "sector": "서비스업"},
    {"active": True, "stock_name": "한화에어로스페이스", "market": "KOSPI", "stock_code": "012450", "ticker": "KRX:012450", "sector": "운수장비"},
    {"active": True, "stock_name": "크래프톤", "market": "KOSPI", "stock_code": "259960", "ticker": "KRX:259960", "sector": "서비스업"},
    {"active": True, "stock_name": "HMM", "market": "KOSPI", "stock_code": "011200", "ticker": "KRX:011200", "sector": "운수창고업"},
    {"active": True, "stock_name": "한국전력", "market": "KOSPI", "stock_code": "015760", "ticker": "KRX:015760", "sector": "전기가스업"},
    {"active": True, "stock_name": "삼성생명", "market": "KOSPI", "stock_code": "032830", "ticker": "KRX:032830", "sector": "금융업"},
    {"active": True, "stock_name": "HD한국조선해양", "market": "KOSPI", "stock_code": "009540", "ticker": "KRX:009540", "sector": "운수장비"},
    {"active": True, "stock_name": "KT&G", "market": "KOSPI", "stock_code": "033780", "ticker": "KRX:033780", "sector": "기타제조업"},
    {"active": True, "stock_name": "SK이노베이션", "market": "KOSPI", "stock_code": "096770", "ticker": "KRX:096770", "sector": "화학"},
    {"active": True, "stock_name": "LG전자", "market": "KOSPI", "stock_code": "066570", "ticker": "KRX:066570", "sector": "전기전자"},
    {"active": True, "stock_name": "SK스퀘어", "market": "KOSPI", "stock_code": "402340", "ticker": "KRX:402340", "sector": "금융업"},
    {"active": True, "stock_name": "카카오뱅크", "market": "KOSPI", "stock_code": "323410", "ticker": "KRX:323410", "sector": "금융업"},
    {"active": True, "stock_name": "알테오젠", "market": "KOSDAQ", "stock_code": "196170", "ticker": "KOSDAQ:196170", "sector": "제약"},
    {"active": True, "stock_name": "에코프로비엠", "market": "KOSDAQ", "stock_code": "247540", "ticker": "KOSDAQ:247540", "sector": "일반전기전자"},
    {"active": True, "stock_name": "에코프로", "market": "KOSDAQ", "stock_code": "086520", "ticker": "KOSDAQ:086520", "sector": "금융"},
    {"active": True, "stock_name": "HLB", "market": "KOSDAQ", "stock_code": "028300", "ticker": "KOSDAQ:028300", "sector": "운송장비부품"},
    {"active": True, "stock_name": "삼천당제약", "market": "KOSDAQ", "stock_code": "000250", "ticker": "KOSDAQ:000250", "sector": "제약"},
]

def format_rank_band_korean(band: str) -> str:
    """RankBand 영문 코드를 한국어 표시명으로 변환"""
    mapping = {
        "TOP_9": "TOP 9",
        "RANK_10_20": "10~20위",
        "RANK_21_30": "21~30위",
        "RANK_31_50": "31~50위",
        "OTHER": "50위 밖",
        "UNKNOWN": "확인대기",
    }
    return mapping.get(str(band).upper(), str(band))

def get_marketcap_leadership_summary_for_email(
    items: Optional[List[Dict[str, Any]]] = None,
    max_count: int = 5
) -> List[Dict[str, Any]]:
    """
    오전/정기 이메일 표시용 시총 리더십 상위 5개 종목 선정:
    1순위: RANK_10_20 (순위 오름차순)
    2순위: RANK_21_30 (순위 오름차순)
    최대 5개 반환
    """
    if not items:
        return []

    rank_10_20_list = []
    rank_21_30_list = []

    for it in items:
        dq = str(it.get("data_quality", "VALID")).upper()
        if dq == "DATA_REVIEW":
            continue

        band = str(it.get("rank_band", "")).upper()
        raw_rank = it.get("current_rank")
        try:
            rank_val = int(raw_rank)
        except (ValueError, TypeError):
            rank_val = 999

        enriched = dict(it)
        enriched["_rank_num"] = rank_val

        if band == "RANK_10_20":
            rank_10_20_list.append(enriched)
        elif band == "RANK_21_30":
            rank_21_30_list.append(enriched)

    rank_10_20_list.sort(key=lambda x: x["_rank_num"])
    rank_21_30_list.sort(key=lambda x: x["_rank_num"])

    selected = rank_10_20_list[:max_count]
    if len(selected) < max_count:
        remaining = max_count - len(selected)
        selected.extend(rank_21_30_list[:remaining])

    return selected

def build_marketcap_radar_section_html(
    radar_items: Optional[List[Dict[str, Any]]] = None,
    date_str: str = ""
) -> str:
    """
    모바일 최적화 이메일 리포트용 '📊 시총 리더십 레이더' 섹션 HTML 생성
    - 최대 5개 종목만 간결하게 표시
    - 전 거래일/최근 GoogleFinance 기준 시총 순위 명시
    """
    if not radar_items:
        return ""

    summary_items = get_marketcap_leadership_summary_for_email(radar_items, max_count=5)
    if not summary_items:
        return ""

    rows_html = ""
    for item in summary_items:
        s_name = html.escape(str(item.get("stock_name", item.get("name", ""))))
        s_code = html.escape(str(item.get("stock_code", item.get("code", ""))).zfill(6))
        rank_val = item.get("current_rank", "-")
        band_raw = item.get("rank_band", "UNKNOWN")
        band_kor = format_rank_band_korean(band_raw)
        
        chg_pct = item.get("change_pct", item.get("daily_change_pct", 0.0))
        if isinstance(chg_pct, (int, float)):
            if chg_pct > 0:
                chg_str = f"+{chg_pct:.1f}%"
                chg_color = "#DC2626"
            elif chg_pct < 0:
                chg_str = f"{chg_pct:.1f}%"
                chg_color = "#2563EB"
            else:
                chg_str = "0.0%"
                chg_color = "#64748B"
        else:
            chg_str = html.escape(str(chg_pct))
            if "+" in chg_str or "▲" in chg_str:
                chg_color = "#DC2626"
            elif "-" in chg_str or "▼" in chg_str:
                chg_color = "#2563EB"
            else:
                chg_color = "#64748B"

        # 구간 뱃지 스타일
        if band_raw == "RANK_10_20":
            band_style = "background:#EFF6FF; color:#1D4ED8; border:1px solid #BFDBFE; font-weight:bold;"
        elif band_raw == "RANK_21_30":
            band_style = "background:#F8FAFC; color:#475569; border:1px solid #CBD5E1; font-weight:normal;"
        else:
            band_style = "background:#F1F5F9; color:#64748B; border:1px solid #E2E8F0;"

        rows_html += f"""
        <tr style="border-bottom:1px solid #E2E8F0;">
            <td style="padding:7px 6px; font-size:12px; font-weight:bold; color:#0F172A; text-align:left;">
                {s_name} <span style="font-size:10px; font-weight:normal; color:#64748B;">({s_code})</span>
            </td>
            <td style="padding:7px 6px; font-size:12px; font-weight:bold; color:#1E293B; text-align:center;">
                {rank_val}위
            </td>
            <td style="padding:7px 6px; font-size:11px; text-align:center;">
                <span style="display:inline-block; padding:2px 6px; border-radius:4px; font-size:10px; {band_style}">
                    {band_kor}
                </span>
            </td>
            <td style="padding:7px 6px; font-size:12px; font-weight:bold; color:{chg_color}; text-align:right;">
                {chg_str}
            </td>
        </tr>
        """

    section_html = f"""
    <div style="background:#FFFFFF; border:1px solid #CBD5E1; border-left:4px solid #4F46E5; border-radius:8px; padding:12px 10px; margin-bottom:16px; box-sizing:border-box; width:100%;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; border-bottom:1px solid #E2E8F0; padding-bottom:6px;">
            <span style="font-size:13px; color:#1E1B4B; font-weight:bold;">📊 시총 리더십 레이더 <span style="font-size:11px; font-weight:normal; color:#6366F1;">(10~30위권 관찰)</span></span>
            <span style="font-size:10px; color:#64748B;">전 거래일/최근 GoogleFinance 기준</span>
        </div>
        <table style="width:100%; border-collapse:collapse; table-layout:fixed;">
            <thead>
                <tr style="background:#F8FAFC; border-bottom:1px solid #CBD5E1;">
                    <th style="padding:5px 6px; font-size:11px; font-weight:bold; color:#475569; text-align:left; width:36%;">종목</th>
                    <th style="padding:5px 6px; font-size:11px; font-weight:bold; color:#475569; text-align:center; width:22%;">시총순위</th>
                    <th style="padding:5px 6px; font-size:11px; font-weight:bold; color:#475569; text-align:center; width:22%;">구간</th>
                    <th style="padding:5px 6px; font-size:11px; font-weight:bold; color:#475569; text-align:right; width:20%;">등락</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
    """
    return section_html
