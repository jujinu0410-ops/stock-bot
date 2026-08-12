import sys
import os
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
from typing import Optional, List, Dict, Any

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

def run_post_market_analysis(add_code: Optional[str] = None, add_name: Optional[str] = None, remove_code: Optional[str] = None):
    """
    실시간 계좌 보유 종목 평가 ➔ 관심 종목 스캔 ➔ 이메일 리포트 발송 실행 함수
    """
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d %H:%M:%S")
    date_title_str = f"{now.month}월 {now.day}일"

    logger.info("==================================================")
    logger.info(f" [내 계좌 보유 종목 중심 실시간 정밀 평가 & 리포트 발송] - {today_str}")
    logger.info("==================================================")

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
        stock_rows = db.execute_query("SELECT stock_code, stock_name FROM stock_info")
        total_count = len(stock_rows) if stock_rows else 0

        held_codes = {item["stock_code"] for item in held_status} if held_status else set()

        for idx, row in enumerate(stock_rows, start=1):
            code = row['stock_code']
            name = row['stock_name']
            try:
                result = engine.analyze_stock(code)
                if result:
                    all_results.append(result)
                    # 이미 계좌에 보유 중인 종목은 '신규 매수 포착 리스트'에서 제외하고 오직 '보유 종목 관리'에서만 평가
                    if result['signal_type'] != "관망" and code not in held_codes:
                        caught_signals.append(result)
            except Exception as e_stock:
                logger.error(f"[{name}({code})] 개별 에러: {e_stock}", exc_info=True)

        logger.info("==================================================")
        logger.info(f" [통합 평가 완료] 내 보유종목: {len(held_status)}개 | 매매 신호 포착: {len(caught_signals)}개")
        logger.info("==================================================")

        # 5. 📊 실제 분석 데이터 종합 엑셀파일(.xlsx) 생성 및 지메일 첨부 발송
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        from src.utils.excel_exporter import create_analysis_excel_report
        excel_path = create_analysis_excel_report(
            date_str=date_str,
            held_portfolio=held_status,
            all_results=all_results,
            db_manager=db
        )

        csv_held_path = excel_path.parent / excel_path.name.replace('stock_analysis_', 'portfolio_monitoring_').replace('.xlsx', '.csv')
        csv_summary_path = excel_path.parent / excel_path.name.replace('stock_analysis_', 'stock_summary_').replace('.xlsx', '.csv')

        subject = f"[내 계좌 스윙 리포트] {datetime.now().month}월 {datetime.now().day}일 보유 종목 정밀 평가 & 관심 종목 리포트"
        
        notifier = GmailNotifier()
        html_report = notifier.generate_html_report(
            date_str=date_str,
            total_count=len(all_results),
            caught_signals=caught_signals,
            all_results=all_results,
            held_portfolio=held_status
        )
        
        sent_success = notifier.send_email(
            subject=subject,
            html_content=html_report,
            attachments=[excel_path, csv_held_path, csv_summary_path]
        )
        if sent_success:
            logger.info("내 종목 정밀 평가 지메일 리포트 (본문 CSV, 백업 CSV 및 ASCII XLSX 첨부) 성공 발송 완료!")
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
        remove_code=args.remove_code
    )
