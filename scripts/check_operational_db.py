"""
scripts/check_operational_db.py

운영 DB 상태 읽기 전용 점검 스크립트.
테스트 discover에 포함되지 않음.
DB를 수정하지 않고 상태만 확인합니다.

사용법:
  python scripts/check_operational_db.py
"""
import sqlite3
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "stock_system.db"
JSON_PATH = BASE_DIR / "config" / "portfolio_holdings.json"

EXPECTED = {
    "000490": {"name": "대동", "qty": 2475},
    "004960": {"name": "한신공영", "qty": 1739},
    "055490": {"name": "테이팩스", "qty": 4283},
    "140670": {"name": "알에스오토메이션", "qty": 531},
    "161510": {"name": "PLUS 고배당주", "qty": 250},
    "206650": {"name": "유바이오로직스", "qty": 293},
    "234920": {"name": "자이글", "qty": 9314},
    "241520": {"name": "DSC인베스트먼트", "qty": 115},
    "348340": {"name": "뉴로메카", "qty": 124},
    "490590": {"name": "RISE 미국AI밸류체인데일리고정커버드콜", "qty": 7476},
}


def main():
    all_ok = True
    print("=" * 60)
    print("운영 DB 읽기 전용 상태 점검")
    print("=" * 60)

    # DB integrity
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    ic = cur.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"SQLite integrity_check: {ic}")
    if ic != "ok":
        all_ok = False

    rows = cur.execute("""
        SELECT p.stock_code, p.quantity, p.avg_buy_price,
               p.anchor_price_p0, p.anchor_atr_a0, s.stock_name
        FROM portfolio_positions p
        LEFT JOIN stock_info s ON p.stock_code = s.stock_code
        WHERE p.quantity > 0 ORDER BY p.stock_code
    """).fetchall()

    print(f"Active holdings: {len(rows)} (Expected: 10)")
    if len(rows) != 10:
        all_ok = False

    print()
    print("종목코드  | 종목명                                    | 수량   | 평단가     | P0      | A0")
    print("-" * 100)
    for r in rows:
        code = r["stock_code"]
        name = r["stock_name"] or "(없음)"
        qty = r["quantity"]
        avg = r["avg_buy_price"]
        p0 = r["anchor_price_p0"]
        a0 = r["anchor_atr_a0"]
        exp = EXPECTED.get(code, {})
        exp_name = exp.get("name", "?")
        exp_qty = exp.get("qty", -1)
        name_ok = (name == exp_name)
        qty_ok = (qty == exp_qty)
        status = "OK" if (name_ok and qty_ok) else "MISMATCH"
        if not (name_ok and qty_ok):
            all_ok = False
        print(f"[{status}] {code} | {name:<40} | {qty:>6} | {avg:>10} | {p0} | {a0}")
    conn.close()

    # JSON
    print()
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        jdata = json.load(f)
    json_codes = {item["stock_code"] for item in jdata}
    db_codes = {r["stock_code"] for r in rows}
    codes_match = (json_codes == db_codes)
    print(f"JSON-DB 종목코드 일치: {codes_match}")
    if not codes_match:
        all_ok = False
        print(f"  JSON만: {json_codes - db_codes}")
        print(f"  DB만  : {db_codes - json_codes}")

    print()
    print("최종 판정:", "ALL OK" if all_ok else "ISSUES FOUND")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
