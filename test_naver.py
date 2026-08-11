import sqlite3

def check_zaigle():
    conn = sqlite3.connect('data/stock_system.db')
    c = conn.cursor()
    c.execute("SELECT stk_date, open_price, high_price, low_price, close_price FROM kiwoom_daily WHERE stock_code='219550' ORDER BY stk_date DESC LIMIT 5")
    rows = c.fetchall()
    print("Zaigle (219550) DB Rows:")
    for r in rows:
        print(r)

if __name__ == "__main__":
    check_zaigle()
