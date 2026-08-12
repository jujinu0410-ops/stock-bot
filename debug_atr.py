import pandas as pd
import numpy as np
from src.database.db_manager import DatabaseManager
from src.api.real_market_api import RealMarketAPIClient

db = DatabaseManager()
api = RealMarketAPIClient()

candles = api.get_real_daily_candles("000490", count=60)
df = pd.DataFrame(candles)

print("Columns:", df.columns)
print(df.tail(20))

# True Range calculation
tr1 = df['high_price'] - df['low_price']
tr2 = (df['high_price'] - df['close_price'].shift(1)).abs()
tr3 = (df['low_price'] - df['close_price'].shift(1)).abs()
tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

# SMA vs Wilder's EWM ATR
atr_sma = tr.rolling(window=14).mean()
atr_ewm = tr.ewm(alpha=1/14, adjust=False).mean()
atr_wilder = tr.ewm(span=27, adjust=False).mean()

print("\n--- 14일 ATR 계산 방식 비교 ---")
print("1. SMA (14일 단순 이동평균):", round(atr_sma.iloc[-1], 2))
print("2. Wilder's EWM (키움 HTS 표준 14일 지수 이동평균):", round(atr_ewm.iloc[-1], 2))
print("3. 최근 14일 일일 변동폭 (TR) 목록:")
print(tr.tail(14).values)
