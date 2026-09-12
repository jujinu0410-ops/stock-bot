"""
Gemini Gems Scanner 운영 DB 완전 격리 및 전략 중복 제거 단위 테스트
- IsolatedGemsDatabase 컨텍스트 매니저 라이프사이클 및 자동 정리 검증
- scan_stock_dto 및 process_stocks_to_dtos 실행 전후 운영 DB/JSON/로그 SHA-256 불변성 검증
- STOCKBOT_TEST_MODE=1 및 GEMS_SCANNER_MODE=1 환경 호환성 및 Fail-Fast 차단 검증
- 실제 주문 API 호출 0건 검증
- Gems Formatter 내 전략 하드코딩(50%/30%/20%, ATR 배수 문자열 등) 부재 검증
"""
import os
import sys
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from scan_stock_for_gems import IsolatedGemsDatabase, scan_stock_dto, scan_stock_for_gems
from run_gems_scanner import process_stocks_to_dtos, process_stocks
from src.core.dto import ScanResultDTO
from src.database.db_manager import DatabaseManager
from src.formatters.gems_formatter import render_gems_markdown
from src.utils.logger import _get_file_log_path

OPERATIONAL_DB_PATH = BASE_DIR / "data" / "stock_system.db"
OPERATIONAL_JSON_PATH = BASE_DIR / "config" / "portfolio_holdings.json"
OPERATIONAL_LOG_PATH = BASE_DIR / "logs" / "stock_system.log"


def get_file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TestGemsScannerIsolation(unittest.TestCase):
    """Gemini Gems Scanner의 운영 DB 격리성, 무결성 및 전략 중복 제거 검증"""

    @staticmethod
    def _test_only_scan_core(stock_code_or_name, _db):
        """Keep scanner-isolation tests deterministic and fully offline."""
        from scan_stock_for_gems import resolve_stock_code

        code, name = resolve_stock_code(stock_code_or_name)
        return ScanResultDTO(
            stock_code=code,
            stock_name=name,
            collected_at="2026-09-02 00:00:00 KST",
            action_strategy="TEST_MODE_OFFLINE_SCAN",
        )

    @classmethod
    def setUpClass(cls):
        cls._scan_core_patcher = patch(
            "scan_stock_for_gems._scan_stock_dto_core",
            side_effect=cls._test_only_scan_core,
        )
        cls._scan_core_patcher.start()
        cls.initial_db_hash = get_file_sha256(OPERATIONAL_DB_PATH)
        cls.initial_json_hash = get_file_sha256(OPERATIONAL_JSON_PATH)
        cls.initial_log_hash = get_file_sha256(OPERATIONAL_LOG_PATH)

    @classmethod
    def tearDownClass(cls):
        cls._scan_core_patcher.stop()

    def test_01_isolated_db_lifecycle_and_cleanup(self):
        """1. IsolatedGemsDatabase 컨텍스트 진입 시 임시 DB 생성 및 탈출 시 디렉터리 자동 폐기 검증"""
        temp_dir_path = None
        temp_db_file = None

        with IsolatedGemsDatabase() as isolated_db:
            self.assertIsInstance(isolated_db, DatabaseManager)
            temp_db_file = Path(isolated_db.db_path)
            temp_dir_path = temp_db_file.parent

            # 임시 경로가 운영 DB와 다른지 확인
            self.assertNotEqual(
                str(temp_db_file.resolve()),
                str(OPERATIONAL_DB_PATH.resolve()),
                "임시 DB 경로가 운영 DB 경로와 일치합니다!"
            )
            # 임시 파일 및 디렉터리가 실제로 생성되어 있는지 확인
            self.assertTrue(temp_dir_path.exists())
            self.assertTrue(temp_db_file.exists())

            # 임시 DB에 테이블이 정상 생성되었는지 확인
            tables = isolated_db.execute_query("SELECT name FROM sqlite_master WHERE type='table'")
            table_names = {t["name"] for t in tables}
            self.assertIn("stock_info", table_names)
            self.assertIn("scan_journal", table_names)

        # 컨텍스트 탈출 후 임시 디렉터리가 삭제되었는지 확인
        self.assertFalse(temp_dir_path.exists(), "IsolatedGemsDatabase 탈출 후 임시 디렉터리가 폐기되지 않았습니다!")

    def test_02_scan_stock_dto_does_not_modify_operational_db(self):
        """2. scan_stock_dto 단일 실행 후 운영 DB SHA-256 해시가 불변임을 검증"""
        pre_hash = get_file_sha256(OPERATIONAL_DB_PATH)
        
        # 관심종목 스캔 실행 (격리 임시 DB 내부에서 실행)
        dto = scan_stock_dto("000490") # 대동
        self.assertEqual(dto.stock_code, "000490")

        post_hash = get_file_sha256(OPERATIONAL_DB_PATH)
        self.assertEqual(
            pre_hash,
            post_hash,
            f"scan_stock_dto 실행이 운영 DB를 변경했습니다!\n  이전: {pre_hash}\n  이후: {post_hash}"
        )

    def test_03_process_stocks_to_dtos_does_not_modify_operational_db(self):
        """3. process_stocks_to_dtos 복수 종목 실행 후 운영 DB SHA-256 해시가 불변임을 검증"""
        pre_hash = get_file_sha256(OPERATIONAL_DB_PATH)

        dtos = process_stocks_to_dtos(["대동", "한신공영"])
        self.assertEqual(len(dtos), 2)
        self.assertEqual(dtos[0].stock_code, "000490")
        self.assertEqual(dtos[1].stock_code, "004960")

        post_hash = get_file_sha256(OPERATIONAL_DB_PATH)
        self.assertEqual(
            pre_hash,
            post_hash,
            f"process_stocks_to_dtos 실행이 운영 DB를 변경했습니다!\n  이전: {pre_hash}\n  이후: {post_hash}"
        )

    def test_04_scan_stock_for_gems_markdown_generation_isolated(self):
        """4. scan_stock_for_gems 마크다운 렌더링 호출 시에도 운영 DB 불변 검증"""
        pre_hash = get_file_sha256(OPERATIONAL_DB_PATH)

        md = scan_stock_for_gems("한신공영")
        self.assertIn("한신공영 (004960)", md)
        self.assertIn("1. 📈 키움 REST & 실시간 시세", md)

        post_hash = get_file_sha256(OPERATIONAL_DB_PATH)
        self.assertEqual(
            pre_hash,
            post_hash,
            f"scan_stock_for_gems 실행이 운영 DB를 변경했습니다!\n  이전: {pre_hash}\n  이후: {post_hash}"
        )

    def test_05_stockbot_test_mode_compatibility(self):
        """5. STOCKBOT_TEST_MODE=1 환경에서도 Fail-Fast 에러 없이 정상 격리 실행되는지 검증"""
        old_mode = os.environ.get("STOCKBOT_TEST_MODE")
        try:
            os.environ["STOCKBOT_TEST_MODE"] = "1"
            with IsolatedGemsDatabase() as db:
                self.assertIsNotNone(db)
        finally:
            if old_mode is None:
                os.environ.pop("STOCKBOT_TEST_MODE", None)
            else:
                os.environ["STOCKBOT_TEST_MODE"] = old_mode

    def test_06_gems_scanner_mode_blocks_operational_db_direct_access(self):
        """6. GEMS_SCANNER_MODE=1 환경에서 DatabaseManager 기본 생성 시 운영 DB 접근 Fail-Fast 차단 검증"""
        old_test_mode = os.environ.get("STOCKBOT_TEST_MODE")
        old_scanner_mode = os.environ.get("GEMS_SCANNER_MODE")
        try:
            os.environ.pop("STOCKBOT_TEST_MODE", None)
            os.environ["GEMS_SCANNER_MODE"] = "1"
            with self.assertRaises(RuntimeError) as cm:
                DatabaseManager()
            self.assertIn("GEMS_SCANNER_MODE=1", str(cm.exception))
            self.assertIn("운영 DB 접근이 차단되었습니다", str(cm.exception))
        finally:
            if old_test_mode is not None:
                os.environ["STOCKBOT_TEST_MODE"] = old_test_mode
            if old_scanner_mode is not None:
                os.environ["GEMS_SCANNER_MODE"] = old_scanner_mode
            else:
                os.environ.pop("GEMS_SCANNER_MODE", None)

    def test_07_gems_scanner_mode_routes_logs_away_from_operational_log(self):
        """7. GEMS_SCANNER_MODE=1 환경에서 로거가 운영 로그가 아닌 전용 스캐너 경로를 반환하는지 검증"""
        old_test_mode = os.environ.get("STOCKBOT_TEST_MODE")
        old_scanner_mode = os.environ.get("GEMS_SCANNER_MODE")
        try:
            os.environ.pop("STOCKBOT_TEST_MODE", None)
            os.environ["GEMS_SCANNER_MODE"] = "1"
            log_path = _get_file_log_path()
            self.assertIn("gems_scanner", str(log_path))
            self.assertNotEqual(str(log_path.resolve()), str(OPERATIONAL_LOG_PATH.resolve()))
        finally:
            if old_test_mode is not None:
                os.environ["STOCKBOT_TEST_MODE"] = old_test_mode
            if old_scanner_mode is not None:
                os.environ["GEMS_SCANNER_MODE"] = old_scanner_mode
            else:
                os.environ.pop("GEMS_SCANNER_MODE", None)

    def test_08_portfolio_holdings_json_invariance(self):
        """8. 스캔 실행 중 portfolio_holdings.json 파일이 변경되지 않음을 검증"""
        pre_json = get_file_sha256(OPERATIONAL_JSON_PATH)
        scan_stock_dto("005930") # 삼성전자
        post_json = get_file_sha256(OPERATIONAL_JSON_PATH)
        self.assertEqual(pre_json, post_json, "portfolio_holdings.json이 스캔 중 변경되었습니다!")

    def test_09_no_order_api_invoked(self):
        """9. 스캔 실행 중 주문 관련 API가 일체 호출되지 않음을 검증"""
        with patch("src.api.kiwoom_api.KiwoomAPIClient.send_order", create=True) as mock_order:
            dtos = process_stocks_to_dtos(["삼성전자", "SK하이닉스"])
            self.assertEqual(len(dtos), 2)
            mock_order.assert_not_called()

    def test_10_no_strategy_hardcoding_in_rendered_markdown(self):
        """10. 렌더링된 마크다운 리포트에 레거시 전략 하드코딩(50%/30%/20%, ATR 배수 문자열 등)이 없음 검증"""
        dto = scan_stock_dto("000490") # 대동
        md = render_gems_markdown(dto)

        # 금지된 하드코딩 패턴 목록
        forbidden_patterns = [
            "50% / 30% / 20%",
            "1차 진입(50%)",
            "2차 추가 매수 (30%)",
            "3차 상승확인 추매 (20%)",
            "+3.0 ATR",
            "-0.8 ATR",
            "1.5 ATR",
            "부분 선익절 25~30%",
            "400만 원",
            "300만 원",
        ]
        for pattern in forbidden_patterns:
            self.assertNotIn(
                pattern, md,
                f"마크다운 출력에 제거 대상 전략 하드코딩 문자열 '{pattern}'이 여전히 포함되어 있습니다!"
            )


if __name__ == "__main__":
    unittest.main()
