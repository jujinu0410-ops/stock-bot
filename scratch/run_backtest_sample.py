import sys
sys.path.insert(0, ".")
import pandas as pd
from src.database.db_manager import DatabaseManager
from src.analysis.backtest_atr import ATRRiskBacktester
import json

db = DatabaseManager("data/stock_system.db")
bt = ATRRiskBacktester()

stocks = db.execute_query("SELECT DISTINCT stock_code FROM kiwoom_daily")
print(f"Total stocks in DB: {len(stocks)}")

for s in stocks[:5]:
    code = s["stock_code"]
    rows = db.execute_query("SELECT stk_date, open_price, high_price, low_price, close_price, volume FROM kiwoom_daily WHERE stock_code = ? ORDER BY stk_date ASC", (code,))
    if len(rows) >= 20:
        df = pd.DataFrame([dict(r) for r in rows])
        res = bt.run_backtest_on_series(df, entry_index=15)
        print(f"\n==========================================")
        print(f"Stock {code} Backtest Result (Entry: {res.get('entry_date')} @ {res.get('entry_price_p0')})")
        print(f"==========================================")
        for opt_name, opt_res in res["comparison"].items():
            print(f"[{opt_name}] Net Return: {opt_res['net_return_pct']}% | Exit: {opt_res['exit_reason']} | Days: {opt_res['holding_days']} | Win: {opt_res['is_win']}")
