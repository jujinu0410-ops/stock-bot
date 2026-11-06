from typing import Dict, Any, List, Optional
from src.database.db_manager import DatabaseManager
from src.utils.logger import logger

class WatchlistManager:
    """
    관심 종목 동적 추가/제거 및 목록 관리를 담당하는 모듈입니다.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self._init_watchlist_table()

    def _init_watchlist_table(self):
        """관심 종목 관리를 위한 SQLite 테이블 초기화"""
        query = """
            CREATE TABLE IF NOT EXISTS watchlist (
                stock_code TEXT PRIMARY KEY,
                stock_name TEXT NOT NULL,
                sector TEXT,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """
        self.db.execute_non_query(query)

    def add_stock(self, stock_code: str, stock_name: str, market_type: str = "KOSPI", sector: str = "주요업종") -> bool:
        """관심 종목 추가"""
        # 1. stock_info 등록
        self.db.upsert_stock_info({
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market_type": market_type,
            "sector": sector,
            "market_cap": 0,
            "floating_shares": 0
        })

        # 2. watchlist 등록
        query = """
            INSERT INTO watchlist (stock_code, stock_name, sector, added_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code) DO UPDATE SET
                stock_name=excluded.stock_name,
                sector=excluded.sector,
                added_at=CURRENT_TIMESTAMP;
        """
        success = self.db.execute_non_query(query, (stock_code, stock_name, sector))
        if success:
            logger.info(f"[WatchlistManager] 관심 종목 추가 성공: {stock_name}({stock_code})")
        return success

    def remove_stock(self, stock_code: str) -> bool:
        """관심 종목 제거"""
        query = "DELETE FROM watchlist WHERE stock_code = ?"
        success = self.db.execute_non_query(query, (stock_code,))
        if success:
            logger.info(f"[WatchlistManager] 관심 종목 제거 완료: {stock_code}")
        return success

    def get_all_watchlist() -> List[Dict[str, Any]]:
        """등록된 관심 종목 목록 반환"""
        rows = self.db.execute_query("SELECT stock_code, stock_name, sector FROM watchlist")
        if not rows:
            return []
        return [dict(r) for r in rows]
