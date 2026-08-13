import sys
import json
import os
sys.path.insert(0, '.')
from config.settings import load_env_vars, KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_ACCOUNT_NO, KIWOOM_USE_MOCK
from src.api.kiwoom_api import KiwoomAPIClient

print("=== [키움 REST API 연동 및 실제 계좌 조회 검증] ===")
env_vars = load_env_vars()
print("• C:/Users/jooji/.env 로드 결과 키 목록:", list(env_vars.keys()))
print("• KIWOOM_APP_KEY 존재 여부:", bool(KIWOOM_APP_KEY and KIWOOM_APP_KEY != "YOUR_KIWOOM_APP_KEY_HERE"))
print("• KIWOOM_ACCOUNT_NO:", KIWOOM_ACCOUNT_NO)
print("• KIWOOM_USE_MOCK:", KIWOOM_USE_MOCK)

client = KiwoomAPIClient()
valid_key = client.is_valid_key()
print("• client.is_valid_key():", valid_key)

positions = client.get_account_positions()
print(f"• 수집된 계좌 종목 개수: {len(positions)}개")
for p in positions:
    print(f"  - [{p.get('stock_code')}] {p.get('stock_name')}: {p.get('quantity')}주 (평단 {p.get('avg_buy_price')})")
