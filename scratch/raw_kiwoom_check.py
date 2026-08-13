import sys
import json
sys.path.insert(0, '.')
from src.api.kiwoom_api import KiwoomAPIClient

client = KiwoomAPIClient()
positions = client.get_account_positions()

print("=== [키움 REST API kt00018 계좌평가 잔고 실시간 원본 데이터] ===")
print(f"총 수집된 잔고 종목 수: {len(positions)}개\n")

for i, p in enumerate(positions, 1):
    print(f"{i}. [{p['stock_code']}] {p['stock_name']}: 보유수량={p['quantity']}주, 평단가={p['avg_buy_price']:,}원")

target_4_codes = ['013030', '034020', '047050', '371460']
print("\n=== [사용자 질의 4개 종목(하이록, 두산에너, 포스코인터, 차이나전기차) 키움 API 검증] ===")
for c in target_4_codes:
    found = [p for p in positions if p['stock_code'] == c]
    if found:
        print(f"⚠️ [{c}] 종목이 잔고에 존재함: {found[0]}")
    else:
        print(f"✅ [{c}] 키움 REST API kt00018 조회 결과: 수량 0주 (실계좌 미보유 확정)")
