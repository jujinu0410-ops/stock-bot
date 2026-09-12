"""
tests/test_stock_code_name_mapping.py

종목코드-종목명 매핑 정합성 검증 (임시 DB 픽스처 기반).
운영 DB에 의존하지 않고 픽스처 데이터로 검증합니다.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.database.db_manager import DatabaseManager

# 확정된 종목코드-종목명 매핑 (8월 18일 기준 실계좌 데이터)
CORRECT_NAME_MAP = {
    "000490": "대동",
    "004960": "한신공영",
    "055490": "테이팩스",
    "140670": "알에스오토메이션",
    "161510": "PLUS 고배당주",
    "206650": "유바이오로직스",
    "234920": "자이글",
    "241520": "DSC인베스트먼트",
    "348340": "뉴로메카",
    "490590": "RISE 미국AI밸류체인데일리고정커버드콜",
}

# 픽스처 보유수량 (검증용)
CORRECT_QTY_MAP = {
    "000490": 2475,
    "004960": 1739,
    "055490": 4283,
    "140670": 531,
    "161510": 250,
    "206650": 293,
    "234920": 9314,
    "241520": 115,
    "348340": 124,
    "490590": 7476,
}


class TestStockCodeNameMapping(unittest.TestCase):
    """종목코드-종목명 매핑 정합성 검증 (임시 DB 픽스처 기반, 운영 DB 미접촉)."""

    @classmethod
    def setUpClass(cls):
        """임시 DB에 픽스처 데이터를 삽입하여 테스트 환경 구성."""
        cls._tmp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls._tmp_file.close()
        cls._tmp_db_path = cls._tmp_file.name
        cls.db = DatabaseManager(cls._tmp_db_path)

        # stock_info 및 portfolio_positions 픽스처 삽입
        for code, name in CORRECT_NAME_MAP.items():
            cls.db.execute_non_query(
                "INSERT OR REPLACE INTO stock_info (stock_code, stock_name, market_type) VALUES (?, ?, 'KRX')",
                (code, name)
            )
            qty = CORRECT_QTY_MAP[code]
            cls.db.execute_non_query(
                "INSERT OR REPLACE INTO portfolio_positions (stock_code, quantity, avg_buy_price) VALUES (?, ?, 10000.0)",
                (code, qty)
            )

    @classmethod
    def tearDownClass(cls):
        """임시 DB 정리."""
        try:
            os.unlink(cls._tmp_db_path)
        except Exception:
            pass

    def test_01_stock_info_correct_names(self):
        """stock_info 테이블의 종목명이 확정 매핑과 일치함."""
        mismatches = []
        for code, expected_name in CORRECT_NAME_MAP.items():
            rows = self.db.execute_query(
                "SELECT stock_name FROM stock_info WHERE stock_code = ?", (code,)
            )
            if not rows:
                mismatches.append(f"  {code}: stock_info 행 없음")
                continue
            actual = rows[0]["stock_name"]
            if actual != expected_name:
                mismatches.append(f"  {code}: DB='{actual}' vs 기대='{expected_name}'")
        if mismatches:
            self.fail("stock_info 종목명 불일치:\n" + "\n".join(mismatches))

    def test_02_portfolio_positions_has_all_10_codes(self):
        """portfolio_positions에 10개 종목이 모두 존재함 (quantity > 0)."""
        rows = self.db.execute_query("SELECT stock_code FROM portfolio_positions WHERE quantity > 0")
        db_codes = {r["stock_code"] for r in rows}
        expected_codes = set(CORRECT_NAME_MAP.keys())
        missing = expected_codes - db_codes
        extra = db_codes - expected_codes
        msgs = []
        if missing:
            msgs.append(f"누락: {sorted(missing)}")
        if extra:
            msgs.append(f"예상 외: {sorted(extra)}")
        if msgs:
            self.fail("portfolio_positions 종목코드 불일치: " + ", ".join(msgs))

    def test_03_portfolio_positions_name_via_join(self):
        """portfolio_positions JOIN stock_info 결과 종목명이 확정 매핑과 일치함."""
        rows = self.db.execute_query("""
            SELECT p.stock_code, s.stock_name
            FROM portfolio_positions p
            JOIN stock_info s ON p.stock_code = s.stock_code
            WHERE p.quantity > 0
            ORDER BY p.stock_code
        """)
        mismatches = []
        for row in rows:
            code = row["stock_code"]
            actual = row["stock_name"]
            expected = CORRECT_NAME_MAP.get(code)
            if expected and actual != expected:
                mismatches.append(f"  {code}: JOIN='{actual}' vs 기대='{expected}'")
        if mismatches:
            self.fail("JOIN 종목명 불일치:\n" + "\n".join(mismatches))

    def test_04_quantity_matches_expected(self):
        """픽스처의 수량이 기대값과 일치함."""
        rows = self.db.execute_query(
            "SELECT stock_code, quantity FROM portfolio_positions WHERE quantity > 0 ORDER BY stock_code"
        )
        mismatches = []
        for row in rows:
            code = row["stock_code"]
            actual_qty = row["quantity"]
            expected_qty = CORRECT_QTY_MAP.get(code, -1)
            if actual_qty != expected_qty:
                mismatches.append(f"  {code}: qty={actual_qty} vs 기대={expected_qty}")
        if mismatches:
            self.fail("수량 불일치:\n" + "\n".join(mismatches))

    def test_05_no_operational_db_was_accessed(self):
        """이 테스트가 운영 DB 경로를 사용하지 않음을 확인."""
        op_db = str((BASE_DIR / "data" / "stock_system.db").resolve())
        test_db = str(Path(self.db.db_path).resolve())
        self.assertNotEqual(
            test_db, op_db,
            f"테스트 DB 경로가 운영 DB와 동일합니다! ({test_db})"
        )


if __name__ == "__main__":
    unittest.main()
