"""
테스트 격리 검증: 운영 DB, JSON, 로그가 테스트 실행 중 변경되지 않음을 검증합니다.
"""
import hashlib
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

OPERATIONAL_DB_PATH = BASE_DIR / "data" / "stock_system.db"
OPERATIONAL_JSON_PATH = BASE_DIR / "config" / "portfolio_holdings.json"
OPERATIONAL_LOG_PATH = BASE_DIR / "logs" / "stock_system.log"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TestOperationalFileIsolation(unittest.TestCase):
    """운영파일 SHA-256 불변성 검증 테스트."""

    @classmethod
    def setUpClass(cls):
        cls.baseline_db_hash = sha256_file(OPERATIONAL_DB_PATH) if OPERATIONAL_DB_PATH.exists() else None
        cls.baseline_json_hash = sha256_file(OPERATIONAL_JSON_PATH) if OPERATIONAL_JSON_PATH.exists() else None
        cls.baseline_log_hash = sha256_file(OPERATIONAL_LOG_PATH) if OPERATIONAL_LOG_PATH.exists() else None

    def test_01_temp_db_manager_does_not_touch_operational_db(self):
        """임시 경로 DatabaseManager 생성이 운영 DB를 건드리지 않음."""
        from src.database.db_manager import DatabaseManager
        from src.utils.logger import logger
        operational_log = str(OPERATIONAL_LOG_PATH.resolve())
        operational_file_handlers = [
            handler for handler in logger.handlers
            if isinstance(handler, logging.FileHandler)
            and str(Path(handler.baseFilename).resolve()) == operational_log
        ]
        self.assertFalse(
            operational_file_handlers,
            "STOCKBOT_TEST_MODE=1에서 운영 로그 FileHandler가 생성되었습니다!",
        )
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            db = DatabaseManager(tmp_path)
            if self.baseline_db_hash:
                current_hash = sha256_file(OPERATIONAL_DB_PATH)
                self.assertEqual(
                    self.baseline_db_hash, current_hash,
                    f"DatabaseManager({tmp_path}) 생성이 운영 DB를 변경했습니다!"
                )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def test_02_portfolio_manager_with_temp_db_does_not_touch_operational(self):
        """임시 DB + 임시 JSON 주입 PM이 운영파일을 건드리지 않음."""
        from src.database.db_manager import DatabaseManager
        from src.engine.portfolio_manager import PortfolioManager
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmpj:
            tmpj.write("[]")
            tmp_json = tmpj.name
        try:
            db = DatabaseManager(tmp_path)
            pm = PortfolioManager(db_manager=db, holdings_backup_path=tmp_json)
            db.execute_non_query(
                "INSERT OR REPLACE INTO stock_info (stock_code, stock_name) VALUES ('999999', 'test')"
            )
            if self.baseline_db_hash:
                current_hash = sha256_file(OPERATIONAL_DB_PATH)
                self.assertEqual(
                    self.baseline_db_hash, current_hash,
                    "PortfolioManager(임시DB) 생성이 운영 DB를 변경했습니다!"
                )
            if self.baseline_json_hash:
                current_json_hash = sha256_file(OPERATIONAL_JSON_PATH)
                self.assertEqual(
                    self.baseline_json_hash, current_json_hash,
                    "PortfolioManager(임시DB) 생성이 운영 JSON을 변경했습니다!"
                )
        finally:
            for p in [tmp_path, tmp_json]:
                try:
                    os.unlink(p)
                except Exception:
                    pass

    def test_03_operational_db_hash_unchanged(self):
        """격리 테스트 후 운영 DB 해시 동일."""
        if self.baseline_db_hash is None:
            self.skipTest("운영 DB 없음")
        current_hash = sha256_file(OPERATIONAL_DB_PATH)
        self.assertEqual(
            self.baseline_db_hash, current_hash,
            f"운영 DB가 테스트 중 변경됨!\n  기준: {self.baseline_db_hash}\n  현재: {current_hash}"
        )

    def test_04_operational_json_hash_unchanged(self):
        """격리 테스트 후 운영 JSON 해시 동일."""
        if self.baseline_json_hash is None:
            self.skipTest("운영 JSON 없음")
        current_hash = sha256_file(OPERATIONAL_JSON_PATH)
        self.assertEqual(
            self.baseline_json_hash, current_hash,
            f"운영 JSON이 테스트 중 변경됨!\n  기준: {self.baseline_json_hash}\n  현재: {current_hash}"
        )

    def test_05_pm_injected_db_not_operational_path(self):
        """임시 DB를 주입하면 PM의 db_path가 운영 DB를 가리키지 않음."""
        from src.database.db_manager import DatabaseManager
        from src.engine.portfolio_manager import PortfolioManager
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            db = DatabaseManager(tmp_path)
            pm = PortfolioManager(db_manager=db)
            self.assertNotEqual(
                str(Path(pm.db.db_path).resolve()),
                str(OPERATIONAL_DB_PATH.resolve()),
                f"임시 DB를 주입했는데 운영 DB 경로가 열렸습니다!"
            )
            if self.baseline_log_hash is not None:
                current_log_hash = sha256_file(OPERATIONAL_LOG_PATH)
                self.assertEqual(
                    self.baseline_log_hash,
                    current_log_hash,
                    "테스트 실행 중 운영 로그가 변경되었습니다!",
                )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
