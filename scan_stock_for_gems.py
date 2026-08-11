import sys
import json
from pathlib import Path
from datetime import datetime

# 프로젝트 루트 디렉토리 추가
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.database.db_manager import DatabaseManager
from src.api.kiwoom_api import KiwoomAPIClient
from src.api.real_market_api import RealMarketAPIClient
from src.api.dart_api import DartAPIClient
from src.engine.trading_engine import TradingEngine
from src.utils.logger import logger

def resolve_stock_code(stock_input: str) -> tuple[str, str]:
    """종목명 또는 종목코드를 6자리 코드 및 정식 종목명으로 변환"""
    s = stock_input.strip()
    if len(s) == 6 and s.isdigit():
        return s, s

    stock_dict = {
        "삼성전자": "005930", "SK하이닉스": "000660", "현대차": "005380",
        "삼성바이오로직스": "207940", "삼바": "207940", "셀트리온": "068270", "알테오젠": "196170",
        "카카오": "035720", "NAVER": "035420", "네이버": "035420",
        "한화에어로스페이스": "012450", "대동": "000490", "한신공영": "004960",
        "한신기계": "011700", "하이록코리아": "013030", "두산에너빌리티": "034020",
        "포스코인터내셔널": "047050", "코데즈컴바인": "047770", "테이팩스": "055490",
        "알에스오토메이션": "140670", "유바이오로직스": "206650", "자이글": "219550",
        "DSC인베스트먼트": "241520", "HD현대일렉트릭": "267260", "뉴로메카": "348340",
        "PLUS 고배당주": "088500", "PLUS고배당주": "088500",
        "TIGER 차이나전기차": "371460", "TIGER차이나전기차": "371460",
        "RISE 미국AI밸류체인": "490590", "RISE미국AI밸류체인": "490590"
    }
    if s in stock_dict:
        return stock_dict[s], s

    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing('KRX')
        matched = df[df['Name'].str.replace(' ', '').str.lower() == s.replace(' ', '').lower()]
        if len(matched) > 0:
            return str(matched['Code'].iloc[0]).zfill(6), str(matched['Name'].iloc[0])
        
        contains = df[df['Name'].str.contains(s, case=False)]
        if len(contains) > 0:
            return str(contains['Code'].iloc[0]).zfill(6), str(contains['Name'].iloc[0])
    except Exception as e:
        logger.error(f"KRX 종목 자동검색 실패 ({s}): {e}")

    return s, s

def scan_stock_for_gems(stock_code_or_name: str) -> str:
    """
    관심 종목 1개(또는 종목코드)에 대해:
    1) Kiwoom REST API + Naver 시세로 실시간 현재가, 60봉 OHLCV, 14일 ATR, T점수 산출
    2) OpenDART 정식 2025년 공시로 2개년 원천 매출/영업이익/OCF/부채비율 실산출, F점수 및 Sanity 검증
    3) 6대 안전 가드레일 및 5단계 Decision Matrix 매수 승인 여부 판정
    4) Gemini Gems 복사-붙여넣기용 최종 마크다운 리포트 텍스트 생성
    """
    db = DatabaseManager()
    kiwoom = KiwoomAPIClient()
    market_api = RealMarketAPIClient()
    dart_api = DartAPIClient()
    engine = TradingEngine(db)

    code, resolved_name = resolve_stock_code(stock_code_or_name)
    db.upsert_stock_info({"stock_code": code, "stock_name": resolved_name, "market_type": "KOSPI/KOSDAQ"})

    logger.info(f"=== [Gemini Gems 전용] {resolved_name} ({code}) Kiwoom REST + DART 정밀 분석 시작 ===")

    # 1. 키움 REST API / RealMarket 시세 수집
    candles = market_api.get_real_daily_candles(code, count=60)
    if candles:
        db.insert_kiwoom_daily_batch(candles)
    
    # 2. OpenDART 정식 2025년 공시 수집 및 DB 저장
    dart_info = dart_api.get_financial_statement(code)
    if dart_info:
        db.upsert_dart_financials(dart_info)

    # 3. TradingEngine 통합 분석 (F점수 5세부 + T점수 14일 ATR + Decision Matrix)
    res = engine.analyze_stock(code)
    if not res:
        return f"❌ 종목 코드 ({code})의 데이터를 조회할 수 없습니다."

    # 데이터 추출
    name = res.get("stock_name", stock_code_or_name)
    cur_p = res.get("latest_close", 0)
    daily_chg = res.get("daily_change_pct", 0.0)
    f_sc = res.get("f_score", 0.0)
    t_sc = res.get("t_score_converted", 0.0)
    final_sc = res.get("final_score", 0.0)
    
    # F점수 5대 세부 항목
    growth_pts = res.get("growth_pts", 0.0)
    ocf_pts = res.get("cf_pts", 0.0)
    momentum_pts = res.get("cat_pts", 0.0)
    debt_pts = res.get("stab_pts", 0.0)
    gov_pts = res.get("val_pts", 0.0)
    
    # DART 수치
    rev_val = dart_info.get("revenue", 0.0)
    prev_rev = dart_info.get("prev_revenue", 0.0)
    op_val = dart_info.get("operating_profit", 0.0)
    prev_op = dart_info.get("prev_operating_profit", 0.0)
    ocf_val = dart_info.get("operating_cash_flow", 0.0)
    prev_ocf = dart_info.get("prev_operating_cash_flow", 0.0)
    debt_ratio = dart_info.get("debt_ratio", 0.0)
    prev_debt_ratio = dart_info.get("prev_debt_ratio", 0.0)
    fs_div = dart_info.get("fs_div", "CFS")
    sanity_flag = dart_info.get("sanity_detail_flag", "PASS")

    # ATR 및 가격가이드
    atr_v = res.get("atr_14", 0.0)
    atr_pct = res.get("atr_pct", 0.0)
    t_buy = res.get("kiwoom_buy_tick_price", 0)
    t_stop = res.get("kiwoom_stop_tick_price", 0)
    t_target = res.get("kiwoom_target_tick_price", 0)

    # 매수 승인 판정 및 상태 (우량 종목 과열 미발생 시 트레일링/눌림목 매수 승인)
    act_st = res.get("reason", "보유")
    if final_sc >= 70.0 and f_sc >= 65.0 and t_sc >= 60.0 and daily_chg < 5.0:
        buy_approval = "🔵 ON (트레일링/눌림목 분할매수 승인)"
        act_st = f"우수한 펀더멘탈({f_sc:.1f}점)과 기술추세({t_sc:.1f}점)를 갖춘 우량 종목으로, 당일 과열 폭등 없는 안정 구간({daily_chg:+.2f}%)입니다. 1.5 ATR 트레일링 매수가({t_buy:,}원) 라인 또는 눌림목 분할 매수 진입이 매우 합리적입니다."
    elif "제한적 분할추매 고려" in act_st:
        buy_approval = "🔵 ON (제한적 매수 승인)"
    else:
        buy_approval = "🔴 OFF (매수 금지/관망)"
    
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S KST")

    rebound_delta = int(atr_v * 0.5) if atr_v > 0 else 0
    drop_delta = int(atr_v * 0.8) if atr_v > 0 else 0

    # Gemini Gems에 그대로 입력할 정밀 텍스트 리포트 생성
    gems_report = f"""
================================================================================
🤖 [Gemini Gems 전용 정밀 진단 프롬프트 데이터] 
종목명: {name} ({code}) | 수집시각: {time_str}
================================================================================

1. 📈 키움 REST & 실시간 시세 (Technical Analysis)
   • 현재가: {cur_p:,}원 (당일 등락률: {daily_chg:+.2f}%)
   • 14일 ATR (변동폭): {atr_v:,.0f}원 ({atr_pct:.2f}%)
   • 기술 T점수 (100점 만점): {t_sc:.1f}점

2. ⚙️ 키움 HTS/MTS 트레일링 주문 정밀 설정 파라미터 (Auto-Trading Rules)
   🔹 [트레일링 매수 설정]:
      - 감시 시작가: {t_buy:,}원 (1.5 ATR 눌림목 도달 시 감시 시작)
      - 최저가 대비 반등폭: +{rebound_delta:,}원 (+0.5 ATR 반등 시 주문 발동)
      - 1차 주문 수량 비중: 목표 수량의 50% 분할 매수 (2차 50% 추가 눌림목 대기)
   🔹 [트레일링 익절 설정]:
      - 감시 시작가 (목표가): {t_target:,}원 (2.5 ATR 상단 도달 시 감시 시작)
      - 최고가 대비 추락폭: -{drop_delta:,}원 (-0.8 ATR 추락 시 이익실현 발동)
      - 1차 익절 수량 비중: 보유 수량의 50% 차익 실현 (잔여 50% 추세 추적)
   🔹 [스탑로스 손절 설정]:
      - 감시 시작가 (손절가): {t_stop:,}원 (2.0 ATR 하단 이탈 시)
      - 손절 매도 수량 비중: 보유 수량 100% 전량 이탈 손절

3. 🏢 OpenDART 2025년 공시 재무 (Fundamental Analysis)
   • 공시 보고서 종류: 2025년 사업보고서 ({fs_div} 연결/별도)
   • 2025년 당기 매출액: {rev_val/1e8:,.1f}억 원 (전기: {prev_rev/1e8:,.1f}억 원)
   • 2025년 당기 영업이익: {op_val/1e8:,.1f}억 원 (전기: {prev_op/1e8:,.1f}억 원)
   • 2025년 당기 영업현금흐름(OCF): {ocf_val/1e8:,.1f}억 원 (전기: {prev_ocf/1e8:,.1f}억 원)
   • 당기 부채비율(%) vs 전기 부채비율(%): {debt_ratio:.2f}% vs {prev_debt_ratio:.2f}% (BS 원천 실산출)
   • DART Sanity 세부 검증: {sanity_flag}
   • 기본 F점수 세부 5대 항목 (100점 만점): {f_sc:.1f}점
     └ ① 성장성(25점): {growth_pts:.1f}점 | ② 현금흐름(20점): {ocf_pts:.1f}점
     └ ③ 모멘텀(20점): {momentum_pts:.1f}점 | ④ 재무안정(20점): {debt_pts:.1f}점 | ⑤ 밸류경영(15점): {gov_pts:.1f}점

4. ⚖️ 100점 만점 가중 종합점수 & 5단계 매수 승인 최종 판정
   • 가중 종합점수: {final_sc:.1f}점 = (F점수 {f_sc:.1f} × 0.4) + (T점수 {t_sc:.1f} × 0.6)
   • 신규/추가 매수 승인 여부: {buy_approval}
   • 5단계 Decision Matrix 최종 대응 전략: [{act_st}]

================================================================================
💡 Gemini Gems 사용 방법:
위 데이터 블록 전체를 복사하여 Gemini Gems 챗봇 질문창에 붙여넣으신 후
"이 종목의 키움 트레일링 매수/매도 설정가와 수량 비중 가이드를 요약해 줘" 라고 질문하세요!
================================================================================
"""
    return gems_report

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "대동"
    print(scan_stock_for_gems(target))
