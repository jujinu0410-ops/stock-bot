import sys
import os
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
from typing import Optional, List, Dict, Any, Tuple

# 프로젝트 루트 디렉토리 설정
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.database.db_manager import DatabaseManager
from src.api.kiwoom_api import KiwoomAPIClient
from src.api.dart_api import DartAPIClient
from src.api.real_market_api import RealMarketAPIClient
from src.engine.trading_engine import TradingEngine
from src.engine.portfolio_manager import PortfolioManager
from src.engine.watchlist_manager import WatchlistManager
from src.notifications.gmail_notifier import GmailNotifier
from src.utils.excel_exporter import create_analysis_excel_report
from src.utils.logger import logger

def update_market_data_stub(db: DatabaseManager, dart_client: DartAPIClient, watchlist_mgr: WatchlistManager):
    """
    [데이터 수집 레이어] 실제 등록된 모든 보유 종목 및 관심 종목에 대하여
    네이버 금융 실시간 시세 API 및 DART 실시간 재무제표 100% 연동
    """
    logger.info("실제 한국주식 시장 실시간 시세 및 DART 재무 데이터 수집 진행 중...")
    
    real_market_client = RealMarketAPIClient()
    all_targets = db.execute_query("SELECT stock_code, stock_name FROM stock_info")

    for target in all_targets:
        code = target["stock_code"]
        name = target["stock_name"]
        
        # 1. 네이버 금융 API를 통해 100% 실제 일봉 시세 데이터 수집
        real_candles = real_market_client.get_real_daily_candles(code, count=60)
        if real_candles:
            db.insert_kiwoom_daily_batch(real_candles)
        
        # 2. DART API를 통해 100% 실제 기업 재무제표 수집
        dart_fin = dart_client.get_financial_statement(code, fiscal_year=2024)
        if dart_fin:
            if "fiscal_year" not in dart_fin:
                dart_fin["fiscal_year"] = 2024
            if "quarter_code" not in dart_fin:
                dart_fin["quarter_code"] = "11011"
            db.upsert_dart_financials(dart_fin)

    logger.info("실시간 실제 시세 및 DART 재무 데이터 최신화 완료")

def run_post_market_analysis(
    add_code: Optional[str] = None,
    add_name: Optional[str] = None,
    remove_code: Optional[str] = None,
    force: bool = False
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    실시간 계좌 보유 종목 평가 ➔ 관심 종목 스캔 ➔ 이메일 리포트 발송 실행 함수
    """
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now.strftime("%Y%m%d")

    logger.info("=" * 50)
    logger.info(f" [내 계좌 보유 종목 중심 실시간 정밀 평가 & 리포트 발송] - {today_str}")
    logger.info("=" * 50)

    # 1. 초기화
    db = DatabaseManager()
    kiwoom_client = KiwoomAPIClient()
    dart_client = DartAPIClient()
    engine = TradingEngine(db)
    portfolio_mgr = PortfolioManager(db, kiwoom_client)
    watchlist_mgr = WatchlistManager(db)
    notifier = GmailNotifier()

    # CLI / 옵션 처리 (관심종목 추가/제거)
    if add_code and add_name:
        watchlist_mgr.add_stock(add_code, add_name)
    if remove_code:
        watchlist_mgr.remove_stock(remove_code)

    # 2. 내 계좌 보유 종목 키움 API 실시간 동기화
    try:
        portfolio_mgr.sync_portfolio_from_kiwoom()
    except Exception as e_sync:
        logger.error(f"보유 계좌 동기화 중 오류 발생: {e_sync}", exc_info=True)

    # 3. 시세 및 재무 데이터 최신화
    try:
        update_market_data_stub(db, dart_client, watchlist_mgr)
    except Exception as e:
        logger.error(f"데이터 최신화 오류 (기존 데이터로 진행): {e}", exc_info=True)

    # 4. 🔥 [핵심] 내 계좌 보유 종목 정밀 평가
    held_status = []
    try:
        held_status = portfolio_mgr.get_held_portfolio_status(engine)
        if held_status:
            logger.info(f"=== [내 계좌 보유 종목 정밀 평가 결과] (F+T 종합점수 순위순 / 총 {len(held_status)}개) ===")
            for item in held_status:
                rank = item.get("rank", 0)
                code = item["stock_code"]
                name = item["stock_name"]
                pnl_pct = item["pnl_pct"]
                pnl_amt = item["pnl_amount"]
                f_sc = item.get("f_score", 0.0)
                t_sc = item.get("t_score", 0.0)
                fin_sc = item.get("final_score", 0.0)
                act_st = item["action_status"]

                logger.info(
                    f"• [{rank}] {name}({code}) - F점수:{f_sc:.1f} | T점수:{t_sc:.1f} | 종합점수:{fin_sc:.1f} | "
                    f"평가손익률:{pnl_pct:.2f}% ({pnl_amt:,}원) | 대응전략: [{act_st}]"
                )
    except Exception as e_port:
        logger.error(f"보유 종목 평가 중 예외 발생: {e_port}", exc_info=True)

    # 5. 관심 종목 스캔 및 신규 매매 신호 추출
    caught_signals = []
    all_results = []
    try:
        STOCK_NAME_MAP = {
            "000490": "대동", "004960": "한신공영", "047770": "코데즈컴바인", "055490": "테이팩스",
            "140670": "알에스오토메이션", "161510": "PLUS 고배당주", "206650": "유바이오로직스",
            "234920": "자이글", "241520": "DSC인베스트먼트", "267260": "HD현대일렉트릭",
            "348340": "뉴로메카", "490590": "RISE 미국AI밸류체인데일리고정커버드콜",
            "010120": "LS일렉트릭", "010140": "삼성중공업", "207940": "삼성바이오로직스",
            "034020": "두산에너빌리티", "015540": "하이록코리아", "005930": "삼성전자",
            "000660": "SK하이닉스", "035420": "NAVER", "035720": "카카오"
        }

        # DB stock_info 테이블 내 010120 등 종목명 누락 건 100% 보정
        for code_k, name_v in STOCK_NAME_MAP.items():
            db.execute_non_query(
                "UPDATE stock_info SET stock_name = ? WHERE stock_code = ? AND (stock_name = ? OR stock_name IS NULL OR stock_name = '')",
                (name_v, code_k, code_k)
            )

        stock_rows = db.execute_query("SELECT stock_code, stock_name FROM stock_info")
        total_count = len(stock_rows) if stock_rows else 0

        # 보유 종목 코드 및 종목명 세트 (보유 중인 종목은 신규 매수 신호 리스트에서 100% 원천 예외 처리)
        held_db_rows = db.execute_query("SELECT p.stock_code, s.stock_name FROM portfolio_positions p LEFT JOIN stock_info s ON p.stock_code = s.stock_code WHERE p.quantity > 0")
        held_codes_set = {str(r['stock_code']).strip().zfill(6) for r in held_db_rows} if held_db_rows else set()
        held_names_set = {str(r['stock_name']).strip() for r in held_db_rows if r['stock_name']} if held_db_rows else set()

        if held_status:
            for h in held_status:
                held_codes_set.add(str(h['stock_code']).strip().zfill(6))
                held_names_set.add(str(h['stock_name']).strip())

        for idx, row in enumerate(stock_rows, start=1):
            code = str(row['stock_code']).strip().zfill(6)
            raw_name = str(row['stock_name']).strip()
            name = STOCK_NAME_MAP.get(code, raw_name if raw_name != code else code)

            try:
                result = engine.analyze_stock(code)
                if result:
                    result['stock_name'] = name
                    all_results.append(result)
                    # 이미 계좌에 보유 중인 종목은 '신규 매수 포착 리스트'에서 100% 제외
                    is_held = (code in held_codes_set) or (name in held_names_set) or any(n in name for n in held_names_set if len(n) > 2)
                    if result['signal_type'] != "관망" and not is_held:
                        caught_signals.append(result)
            except Exception as e_stock:
                logger.error(f"[{name}({code})] 개별 에러: {e_stock}", exc_info=True)

        logger.info("==================================================")
        logger.info(f" [통합 평가 완료] 내 보유종목: {len(held_status)}개 | 매매 신호 포착: {len(caught_signals)}개")
        logger.info("==================================================")

        # 6. 📊 실제 분석 데이터 종합 엑셀파일(.xlsx) 생성 및 지메일 첨부 발송
        date_str_file = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        excel_path = create_analysis_excel_report(
            date_str=date_str_file,
            held_portfolio=held_status,
            all_results=all_results,
            db_manager=db
        )

        csv_held_path = excel_path.parent / excel_path.name.replace('stock_analysis_', 'portfolio_monitoring_').replace('.xlsx', '.csv')
        csv_summary_path = excel_path.parent / excel_path.name.replace('stock_analysis_', 'stock_summary_').replace('.xlsx', '.csv')

        now_dt = datetime.now()
        hour_now = now_dt.hour
        session_code = "1120" if hour_now < 13 else "1535"
        dispatch_id = f"{now_dt.strftime('%Y%m%d')}_{session_code}"
        dispatch_tag = "1차 장중 리포트(11:20)" if hour_now < 13 else "2차 장마감 정밀 리포트(15:35)"

        subject = f"[{dispatch_tag}] {now_dt.month}월 {now_dt.day}일 내 계좌 보유 종목 정밀 평가 & 관심 종목 리포트"
        
        html_report = notifier.generate_html_report(
            date_str=date_str,
            total_count=len(all_results),
            caught_signals=caught_signals,
            all_results=all_results,
            held_portfolio=held_status
        )
        
        # 🔥 중복 발송 방지 검사 (동일 날짜/회차 메일 이미 발송 시 중복 발송 건너뛰기)
        if not force and db.is_dispatch_already_sent(dispatch_id):
            logger.info(f"🛑 [중복 발송 방지] {dispatch_id} ({dispatch_tag}) 리포트가 오늘 이미 성공적으로 발송되었습니다. 메일 발송을 안전하게 건너뜁니다. (강제 재발송 필요 시 --force 옵션 사용)")
        else:
            sent_success = notifier.send_email(
                subject=subject,
                html_content=html_report,
                attachments=[excel_path, csv_held_path, csv_summary_path]
            )
            if sent_success:
                db.record_dispatch_success(dispatch_id, dispatch_tag, notifier.recipient_email, subject)
                logger.info(f"내 종목 정밀 평가 지메일 리포트 성공 발송 및 발송 기록 완료! [식별자: {dispatch_id}]")
            else:
                logger.warning("지메일 발송에 실패했거나 설정이 미비합니다. 로컬 로그 파일을 확인하세요.")

    except Exception as e:
        logger.critical(f"시스템 실행 중 예외 발생: {e}", exc_info=True)

    return held_status, caught_signals

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stock Analysis System CLI")
    parser.add_argument("--add-code", type=str, help="관심종목 추가 코드")
    parser.add_argument("--add-name", type=str, help="관심종목 추가 종목명")
    parser.add_argument("--remove-code", type=str, help="관심종목 제거 코드")
    parser.add_argument("--add-holding", nargs=4, metavar=('CODE', 'NAME', 'QTY', 'PRICE'), help="실제 보유 종목 추가 (예: --add-holding 005930 삼성전자 10 70000)")
    parser.add_argument("--clear-holdings", action="store_true", help="기존 테스트/모의 보유 종목 모두 삭제")
    parser.add_argument("--force", action="store_true", help="중복 발송 방지 검사를 우회하여 메일 강제 재발송")
    args = parser.parse_args()

    db_init = DatabaseManager()
    p_mgr = PortfolioManager(db_init)

    if args.clear_holdings:
        p_mgr.clear_all_holdings()

    if args.add_holding:
        h_code, h_name, h_qty, h_price = args.add_holding
        p_mgr.add_holding(h_code, h_name, int(h_qty), float(h_price))

    run_post_market_analysis(
        add_code=args.add_code,
        add_name=args.add_name,
        remove_code=args.remove_code,
        force=args.force
    )
