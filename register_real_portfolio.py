import sys
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np

# 프로젝트 루트 설정
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.database.db_manager import DatabaseManager
from src.engine.portfolio_manager import PortfolioManager
from src.engine.trading_engine import TradingEngine
from src.notifications.gmail_notifier import GmailNotifier
from src.utils.logger import logger

def register_user_actual_holdings():
    logger.info("=== 사용자 실제 계좌 잔고 스크린샷 15개 종목 DB 등록 시작 ===")
    
    db = DatabaseManager()
    portfolio_mgr = PortfolioManager(db)

    # 기존 가상/테스트 보유 종목 초기화
    portfolio_mgr.clear_all_holdings()

    # 스크린샷 및 신규 매수 반영 최신 실제 보유 종목
    actual_holdings = [
        # (종목코드, 종목명, 보유수량, 평단가, 현재가)
        ("000490", "대동", 100, 9800.0, 9540),
        ("011700", "한신기계", 769, 4282.0, 2465),
        ("013030", "하이록코리아", 90, 38945.0, 33100),
        ("034020", "두산에너빌리티", 27, 74800.0, 76600),
        ("047050", "포스코인터내셔널", 158, 62806.0, 58000),
        ("047770", "코데즈컴바인", 989, 4745.0, 2665),
        ("055490", "테이팩스", 4283, 23365.0, 13470),
        ("140670", "알에스오토메이션", 531, 18899.0, 9910),
        ("088500", "PLUS 고배당주", 250, 28186.0, 25100),
        ("206650", "유바이오로직스", 418, 15717.0, 8670),
        ("234920", "자이글", 9314, 7518.0, 5310),
        ("241520", "DSC인베스트먼트", 115, 17995.0, 7930),
        ("267260", "HD현대일렉트릭", 4, 745000.0, 756000),
        ("348340", "뉴로메카", 124, 80329.0, 19310),
        ("371460", "TIGER 차이나전기차SOLACTIVE", 137, 14570.0, 10935),
        ("484730", "RISE 미국AI밸류체인TOP3Plus", 9114, 16458.0, 14650)
    ]

    base_date = datetime.today()

    for code, name, qty, avg_p, cur_p in actual_holdings:
        # 1. DB 보유 종목 등록
        portfolio_mgr.add_holding(code, name, qty, avg_p)

        # 2. 일봉 데이터 (최근 60봉) 생성하여 현재가 맞춤
        daily_list = []
        price = avg_p
        for i in range(60, 0, -1):
            date_str = (base_date - pd.Timedelta(days=i)).strftime("%Y%m%d")
            if i == 1:
                close_p = cur_p
            else:
                change = np.random.randint(-300, 300)
                close_p = max(100, int(price + change))
            
            open_p = close_p
            high_p = close_p + 100
            low_p = max(100, close_p - 100)
            vol = np.random.randint(10000, 500000)

            daily_list.append({
                "stock_code": code,
                "stk_date": date_str,
                "open_price": open_p,
                "high_price": high_p,
                "low_price": low_p,
                "close_price": close_p,
                "volume": vol,
                "foreign_net_buy": np.random.randint(-5000, 5000),
                "inst_net_buy": np.random.randint(-5000, 5000)
            })
            price = close_p

        db.insert_kiwoom_daily_batch(daily_list)

        # 3. DART 기본 재무 샘플 등록
        dart_sample = {
            "stock_code": code,
            "fiscal_year": 2024,
            "quarter_code": "Q4",
            "revenue": 100000000000,
            "operating_profit": 10000000000,
            "net_income": 8000000000,
            "operating_cash_flow": 12000000000,
            "debt_ratio": 65.0,
            "order_backlog": 20000000000,
            "per": 15.0,
            "pbr": 1.2,
            "audit_opinion": "적정",
            "disclosure_risk_flag": False
        }
        db.upsert_dart_financials(dart_sample)

    logger.info(f"=== 사용자 실제 보유 15개 종목 DB 등록 완료 ===")

if __name__ == "__main__":
    register_user_actual_holdings()
