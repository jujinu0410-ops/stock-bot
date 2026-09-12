# -*- coding: utf-8 -*-
"""
GoogleFinance 장중감시 Sheet Exporter (Sidecar)
- 매일 15:35 장마감 리포트에서 실제 사용되는 감시 종목(Source of Truth)을 추출
- Google Sheet용 Excel 템플릿('보유관심종목_GoogleFinance_장중감시.xlsx') 생성
- WATCHLIST 탭: Active, 종목명, 시장, 종목코드, Ticker
- LIVE 탭: GOOGLEFINANCE 수식 연동 (price, changepct/100, volume, volumeavg, volume ratio, high, low, high52, high52 distance, tradetime, datadelay, data quality)
- Shadow V1.0, ATR V4, 45M ADD ADVISORY 코어에 완전 무영향 독립 사이드카
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from src.database.db_manager import DatabaseManager
from src.utils.logger import logger

# KOSDAQ 등록 종목 코드 세트
KOSDAQ_CODES = {
    "013030", "047770", "140670", "206650", "214450", "219550", "234920", "241520", "348340",
    "196170", "086520", "028300", "277810"
}

# 기본 종목명 보완 맵
FULL_NAME_MAP = {
    "000490": "대동",
    "004960": "한신공영",
    "005380": "현대차",
    "005930": "삼성전자",
    "009540": "HD한국조선해양",
    "010120": "LS일렉트릭",
    "010140": "삼성중공업",
    "011700": "한신기계",
    "012450": "한화에어로스페이스",
    "013030": "하이록코리아",
    "034020": "두산에너빌리티",
    "047050": "포스코인터내셔널",
    "047770": "코데즈컴바인",
    "055490": "테이팩스",
    "140670": "알에스오토메이션",
    "161510": "PLUS 고배당주",
    "206650": "유바이오로직스",
    "207940": "삼성바이오로직스",
    "214450": "파마리서치",
    "234920": "자이글",
    "241520": "DSC인베스트먼트",
    "267260": "HD현대일렉트릭",
    "348340": "뉴로메카",
    "371460": "TIGER 차이나전기차SOLACTIVE",
    "490590": "RISE 미국AI밸류체인데일리고정커버드콜",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "035720": "카카오",
    "219550": "디와이디",
}

def get_watchlist_universe_from_db(db_manager: Optional[DatabaseManager] = None) -> List[Dict[str, str]]:
    """
    DB stock_info 및 portfolio_positions로부터 장마감 감시 종목 목록(Source of Truth) 추출
    """
    db = db_manager if db_manager is not None else DatabaseManager()
    rows = db.execute_query("SELECT stock_code, stock_name, market_type FROM stock_info ORDER BY stock_code")
    if not rows:
        return []

    universe = []
    for r in rows:
        r_dict = dict(r) if hasattr(r, "keys") else {"stock_code": r[0], "stock_name": r[1], "market_type": r[2]}
        code = str(r_dict.get("stock_code", "")).strip().zfill(6)
        db_name = str(r_dict.get("stock_name", "")).strip()
        name = FULL_NAME_MAP.get(code, db_name if db_name and db_name != code else code)
        market = "KOSDAQ" if code in KOSDAQ_CODES else "KOSPI"
        ticker = f"KOSDAQ:{code}" if market == "KOSDAQ" else f"KRX:{code}"
        universe.append({
            "active": "Y",
            "stock_name": name,
            "market": market,
            "stock_code": code,
            "ticker": ticker
        })
    return universe

def generate_googlefinance_watchlist_excel(
    output_path: Optional[Path] = None,
    universe: Optional[List[Dict[str, str]]] = None,
    db_manager: Optional[DatabaseManager] = None
) -> Path:
    """
    Google Sheets에 업로드 가능한 2개 탭(WATCHLIST, LIVE) 구조의 엑셀 템플릿 생성
    """
    if universe is None:
        universe = get_watchlist_universe_from_db(db_manager)

    if output_path is None:
        output_path = Path("보유관심종목_GoogleFinance_장중감시.xlsx")
    else:
        output_path = Path(output_path)

    wb = openpyxl.Workbook()
    # Default sheet
    ws_watchlist = wb.active
    ws_watchlist.title = "WATCHLIST"
    ws_live = wb.create_sheet(title="LIVE")

    # Styling definitions
    font_header = Font(name="Malgun Gothic", size=10, bold=True, color="FFFFFF")
    font_data = Font(name="Malgun Gothic", size=10)
    fill_header_watchlist = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid") # Dark Blue
    fill_header_live = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid") # Dark Slate
    fill_zebra = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    thin_border_side = Side(border_style="thin", color="CBD5E1")
    border_cell = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)

    # 1. WATCHLIST Tab
    watchlist_headers = ["Active", "종목명", "시장", "종목코드", "Ticker"]
    ws_watchlist.append(watchlist_headers)
    for col_idx in range(1, len(watchlist_headers) + 1):
        cell = ws_watchlist.cell(row=1, column=col_idx)
        cell.font = font_header
        cell.fill = fill_header_watchlist
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border_cell

    for row_idx, item in enumerate(universe, start=2):
        ws_watchlist.append([
            item.get("active", "Y"),
            item.get("stock_name", ""),
            item.get("market", "KOSPI"),
            item.get("stock_code", ""),
            item.get("ticker", "")
        ])
        fill_to_use = fill_zebra if row_idx % 2 == 0 else PatternFill(fill_type=None)
        for col_idx in range(1, len(watchlist_headers) + 1):
            cell = ws_watchlist.cell(row=row_idx, column=col_idx)
            cell.font = font_data
            if fill_to_use.fill_type:
                cell.fill = fill_to_use
            cell.border = border_cell
            if col_idx in (1, 3, 4, 5):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # 2. LIVE Tab
    live_headers = [
        "종목명", "Ticker", "현재가", "등락률", "거래량", "평균거래량", "거래량비",
        "당일고가", "당일저가", "52주고가", "52주고가거리", "TradeTime", "DataDelay", "DataQuality"
    ]
    ws_live.append(live_headers)
    for col_idx in range(1, len(live_headers) + 1):
        cell = ws_live.cell(row=1, column=col_idx)
        cell.font = font_header
        cell.fill = fill_header_live
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border_cell

    for i, item in enumerate(universe, start=2):
        # A: 종목명, B: Ticker, C: 현재가, D: 등락률(/100), E: 거래량, F: 평균거래량, G: 거래량비,
        # H: 당일고가, I: 당일저가, J: 52주고가, K: 52주고가거리, L: TradeTime, M: DataDelay, N: DataQuality
        formula_row = [
            f"=WATCHLIST!B{i}",
            f"=WATCHLIST!E{i}",
            f'=IFERROR(GOOGLEFINANCE(B{i},"price"),NA())',
            f'=IFERROR(GOOGLEFINANCE(B{i},"changepct")/100,NA())',
            f'=IFERROR(GOOGLEFINANCE(B{i},"volume"),NA())',
            f'=IFERROR(GOOGLEFINANCE(B{i},"volumeavg"),NA())',
            f'=IFERROR(IF(AND(ISNUMBER(E{i}), ISNUMBER(F{i}), F{i}>0), E{i}/F{i}, NA()), NA())',
            f'=IFERROR(GOOGLEFINANCE(B{i},"high"),NA())',
            f'=IFERROR(GOOGLEFINANCE(B{i},"low"),NA())',
            f'=IFERROR(GOOGLEFINANCE(B{i},"high52"),NA())',
            f'=IFERROR(C{i}/J{i}-1,NA())',
            f'=IFERROR(GOOGLEFINANCE(B{i},"tradetime"),NA())',
            f'=IFERROR(GOOGLEFINANCE(B{i},"datadelay"),NA())',
            f'=IF(OR(NOT(ISNUMBER(C{i})), C{i}<=0, NOT(ISNUMBER(D{i})), NOT(ISNUMBER(E{i}))), "DATA_REVIEW", "VALID")'
        ]
        ws_live.append(formula_row)
        fill_to_use = fill_zebra if i % 2 == 0 else PatternFill(fill_type=None)
        for col_idx in range(1, len(live_headers) + 1):
            cell = ws_live.cell(row=i, column=col_idx)
            cell.font = font_data
            if fill_to_use.fill_type:
                cell.fill = fill_to_use
            cell.border = border_cell

            # Alignment & Number Formats
            if col_idx in (1,):
                cell.alignment = Alignment(horizontal="left", vertical="center")
            elif col_idx in (2, 12, 13, 14):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="right", vertical="center")

            # Number formatting
            if col_idx in (3, 8, 9, 10): # Currency / Price
                cell.number_format = '#,##0'
            elif col_idx in (4, 11): # Percentage
                cell.number_format = '+0.00%;-0.00%;0.00%'
            elif col_idx in (5, 6): # Volume
                cell.number_format = '#,##0'
            elif col_idx == 7: # Ratio
                cell.number_format = '0.00'

    # Auto column width
    for ws in (ws_watchlist, ws_live):
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or "")
                max_len = max(max_len, len(val_str.encode("utf-8", errors="ignore")))
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    logger.info(f"[WatchlistGoogleFinanceExporter] 엑셀 템플릿 생성 완료: {output_path} (종목수: {len(universe)})")
    return output_path

def export_watchlist_on_closing_report(db_manager: Optional[DatabaseManager] = None, output_path: Optional[Path] = None) -> Path:
    """
    장마감 15:35 리포트 완료 시점에 WATCHLIST를 갱신하는 최소 Sidecar 함수
    """
    return generate_googlefinance_watchlist_excel(output_path=output_path, db_manager=db_manager)
