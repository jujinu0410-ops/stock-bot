import sys
import json
sys.path.insert(0, '.')
from src.database.db_manager import DatabaseManager
from src.engine.portfolio_manager import PortfolioManager
from src.api.kiwoom_api import KiwoomAPIClient

print("=== [실시간 계좌 잔고 동기화 교정 100% 검증 라이브 테스팅] ===")

db = DatabaseManager()
portfolio_mgr = PortfolioManager(db)

# 1. 키움 API 계좌 동기화 함수 실행
synced_positions = portfolio_mgr.sync_portfolio_from_kiwoom()

# 2. SQLite DB portfolio_positions 테이블 실시간 종목 수 및 목록 확인
db_rows = db.execute_query("SELECT p.stock_code, s.stock_name, p.quantity, p.avg_buy_price FROM portfolio_positions p JOIN stock_info s ON p.stock_code = s.stock_code WHERE p.quantity > 0")

print(f"\n[1] Kiwoom REST API 동기화 직후 DB에 저장된 종목 개수: {len(db_rows)}개")
for r in db_rows:
    print(f"  • [{r['stock_code']}] {r['stock_name']}: {r['quantity']}주 (평단가: {r['avg_buy_price']:,}원)")

# 3. config/portfolio_holdings.json 파일 내용 검증
with open('config/portfolio_holdings.json', 'r', encoding='utf-8') as f:
    cfg_data = json.load(f)

print(f"\n[2] config/portfolio_holdings.json 파일 내 보유 종목 개수: {len(cfg_data)}개")
deleted_target_codes = ['013030', '034020', '047050', '371460']
found_deleted = [c for c in cfg_data if c['stock_code'] in deleted_target_codes]
print(f"  • 매도된 4개 종목(하이록, 두산에너, 포스코인터, 차이나전기차) 존재 여부: {len(found_deleted)}개 (0개면 정상 제거)")
