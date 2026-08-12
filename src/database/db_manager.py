import sqlite3
import os
from typing import List, Dict, Any, Optional
from config.settings import DB_PATH, TABLE_SCHEMAS, INDEX_SCHEMAS
from src.utils.logger import logger

class DatabaseManager:
    """
    SQLite 데이터베이스 연결 관리, 테이블 초기화, CRUD 쿼리 및 트랜잭션 예외 처리를 담당합니다.
    """
    def __init__(self, db_path: str = str(DB_PATH)):
        self.db_path = db_path
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        """데이터베이스 커넥션 생성 및 반환 (디렉토리 자동 생성)"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 딕셔너리 형태로 쿼리 결과 접근 가능
        return conn

    def _init_db(self) -> None:
        """테이블 및 인덱스 자동 생성 함수"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # 테이블 생성
                for table_name, schema in TABLE_SCHEMAS.items():
                    cursor.execute(schema)
                    logger.info(f"DB 테이블 확인/생성 완료: {table_name}")
                
                # 인덱스 생성
                for idx_query in INDEX_SCHEMAS:
                    cursor.execute(idx_query)
                
                # 컬럼 자동 마이그레이션 (portfolio_positions 최고종가/확정손절가/감시시작일)
                cursor.execute("PRAGMA table_info(portfolio_positions)")
                cols = [row[1] for row in cursor.fetchall()]
                if "highest_close_price" not in cols:
                    cursor.execute("ALTER TABLE portfolio_positions ADD COLUMN highest_close_price REAL DEFAULT 0;")
                if "confirmed_stop_price" not in cols:
                    cursor.execute("ALTER TABLE portfolio_positions ADD COLUMN confirmed_stop_price REAL DEFAULT 0;")
                
                # dart_financials 테이블 마이그레이션 (f_score_confirmed, sanity_reason)
                cursor.execute("PRAGMA table_info(dart_financials)")
                dart_cols = [row[1] for row in cursor.fetchall()]
                if "f_score_confirmed" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN f_score_confirmed INTEGER DEFAULT 1;")
                if "sanity_reason" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN sanity_reason TEXT;")
                if "prev_revenue" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN prev_revenue REAL DEFAULT 0;")
                if "prev_operating_profit" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN prev_operating_profit REAL DEFAULT 0;")
                if "prev_operating_cash_flow" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN prev_operating_cash_flow REAL DEFAULT 0;")
                if "fs_div" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN fs_div TEXT DEFAULT 'CFS';")
                if "prev_debt_ratio" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN prev_debt_ratio REAL DEFAULT 0;")
                if "sanity_detail_flag" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN sanity_detail_flag TEXT;")
                if "total_liabilities" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN total_liabilities REAL DEFAULT 0;")
                if "total_equity" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN total_equity REAL DEFAULT 0;")
                if "prev_total_liabilities" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN prev_total_liabilities REAL DEFAULT 0;")
                if "prev_total_equity" not in dart_cols:
                    cursor.execute("ALTER TABLE dart_financials ADD COLUMN prev_total_equity REAL DEFAULT 0;")

                # 구버전 Q4 더미 데이터 삭제 (11011 정식 보고서 코드 정제)
                cursor.execute("DELETE FROM dart_financials WHERE quarter_code = 'Q4'")

                conn.commit()
                logger.info("모든 DB 테이블 및 인덱스 초기화 완료")
        except sqlite3.Error as e:
            logger.error(f"DB 초기화 중 오류 발생: {e}", exc_info=True)
            raise

    def execute_query(self, query: str, params: tuple = ()) -> Optional[List[sqlite3.Row]]:
        """SELECT 쿼리 실행 함수 (예외 처리 및 로깅 포함)"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"쿼리 실행 실패 [Query: {query}]: {e}", exc_info=True)
            return None

    def execute_non_query(self, query: str, params: tuple = ()) -> bool:
        """INSERT / UPDATE / DELETE 쿼리 실행 (자동 트랜잭션 관리 및 롤백 지원)"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return True
        except sqlite3.Error as e:
            if conn:
                conn.rollback()
            logger.error(f"DB 작업 실패 (롤백 수행) [Query: {query}]: {e}", exc_info=True)
            return False
        finally:
            if conn:
                conn.close()

    def execute_many(self, query: str, params_list: List[tuple]) -> bool:
        """다량의 데이터 INSERT / UPDATE (대량 수집용, 롤백 지원)"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.executemany(query, params_list)
            conn.commit()
            logger.info(f"대량 DB 입력 성공: {len(params_list)}건")
            return True
        except sqlite3.Error as e:
            if conn:
                conn.rollback()
            logger.error(f"대량 DB 작업 실패 (롤백 수행): {e}", exc_info=True)
            return False
        finally:
            if conn:
                conn.close()

    # --- 도메인별 헬퍼 메소드 ---

    def upsert_stock_info(self, stock_info: Dict[str, Any]) -> bool:
        """종목 기본 정보 저장/업데이트"""
        query = """
            INSERT INTO stock_info (stock_code, stock_name, market_type, sector, market_cap, floating_shares, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code) DO UPDATE SET
                stock_name=excluded.stock_name,
                market_type=excluded.market_type,
                sector=excluded.sector,
                market_cap=excluded.market_cap,
                floating_shares=excluded.floating_shares,
                updated_at=CURRENT_TIMESTAMP;
        """
        params = (
            stock_info['stock_code'], stock_info['stock_name'], stock_info.get('market_type'),
            stock_info.get('sector'), stock_info.get('market_cap'), stock_info.get('floating_shares')
        )
        return self.execute_non_query(query, params)

    def insert_kiwoom_daily_batch(self, daily_data: List[Dict[str, Any]]) -> bool:
        """키움 일봉/수급 대량 데이터 저장"""
        query = """
            INSERT INTO kiwoom_daily (stock_code, stk_date, open_price, high_price, low_price, close_price, volume, foreign_net_buy, inst_net_buy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, stk_date) DO UPDATE SET
                open_price=excluded.open_price, high_price=excluded.high_price, low_price=excluded.low_price,
                close_price=excluded.close_price, volume=excluded.volume,
                foreign_net_buy=excluded.foreign_net_buy, inst_net_buy=excluded.inst_net_buy;
        """
        params_list = [
            (
                d['stock_code'], d['stk_date'], d['open_price'], d['high_price'], d['low_price'],
                d['close_price'], d['volume'], d.get('foreign_net_buy', 0), d.get('inst_net_buy', 0)
            ) for d in daily_data
        ]
        return self.execute_many(query, params_list)

    def upsert_dart_financials(self, fin_data: Dict[str, Any]) -> bool:
        """DART 재무 데이터 저장"""
        query = """
            INSERT INTO dart_financials 
            (stock_code, fiscal_year, quarter_code, revenue, operating_profit, net_income, operating_cash_flow, debt_ratio, revenue_yoy, op_profit_yoy, order_backlog, data_completeness, f_score_confirmed, sanity_reason, prev_revenue, prev_operating_profit, prev_operating_cash_flow, fs_div, prev_debt_ratio, sanity_detail_flag, total_liabilities, total_equity, prev_total_liabilities, prev_total_equity, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code, fiscal_year, quarter_code) DO UPDATE SET
                revenue=excluded.revenue, operating_profit=excluded.operating_profit, net_income=excluded.net_income,
                operating_cash_flow=excluded.operating_cash_flow, debt_ratio=excluded.debt_ratio,
                revenue_yoy=excluded.revenue_yoy, op_profit_yoy=excluded.op_profit_yoy,
                order_backlog=excluded.order_backlog, data_completeness=excluded.data_completeness,
                f_score_confirmed=excluded.f_score_confirmed, sanity_reason=excluded.sanity_reason,
                prev_revenue=excluded.prev_revenue, prev_operating_profit=excluded.prev_operating_profit,
                prev_operating_cash_flow=excluded.prev_operating_cash_flow, fs_div=excluded.fs_div, 
                prev_debt_ratio=excluded.prev_debt_ratio, sanity_detail_flag=excluded.sanity_detail_flag,
                total_liabilities=excluded.total_liabilities, total_equity=excluded.total_equity,
                prev_total_liabilities=excluded.prev_total_liabilities, prev_total_equity=excluded.prev_total_equity, collected_at=CURRENT_TIMESTAMP;
        """
        f_conf = 1 if fin_data.get('f_score_confirmed', True) else 0
        comp = fin_data.get('data_completeness', 100.0) if f_conf == 1 else min(fin_data.get('data_completeness', 100.0), 75.0)

        params = (
            fin_data['stock_code'], fin_data['fiscal_year'], fin_data['quarter_code'],
            fin_data.get('revenue', 0.0), fin_data.get('operating_profit', 0.0), fin_data.get('net_income', 0.0),
            fin_data.get('operating_cash_flow', 0.0), fin_data.get('debt_ratio', 100.0),
            fin_data.get('revenue_yoy', 0.0), fin_data.get('op_profit_yoy', 0.0),
            fin_data.get('order_backlog', 0.0), comp, f_conf, fin_data.get('sanity_reason', '정상'),
            fin_data.get('prev_revenue', 0.0), fin_data.get('prev_operating_profit', 0.0),
            fin_data.get('prev_operating_cash_flow', 0.0), fin_data.get('fs_div', 'CFS'),
            fin_data.get('prev_debt_ratio', fin_data.get('debt_ratio', 100.0)),
            fin_data.get('sanity_detail_flag', '정상'),
            fin_data.get('total_liabilities', 0.0), fin_data.get('total_equity', 0.0),
            fin_data.get('prev_total_liabilities', 0.0), fin_data.get('prev_total_equity', 0.0)
        )
        return self.execute_non_query(query, params)

    def upsert_trading_signal(self, signal: Dict[str, Any]) -> bool:
        """매매 분석 신호 결과 저장"""
        query = """
            INSERT INTO trading_signals
            (stock_code, analysis_date, f_score, t_score_raw, t_score_converted, score_stage1, score_stage2, score_stage3, position_stage, signal_type, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code, analysis_date) DO UPDATE SET
                f_score=excluded.f_score, t_score_raw=excluded.t_score_raw, t_score_converted=excluded.t_score_converted,
                score_stage1=excluded.score_stage1, score_stage2=excluded.score_stage2, score_stage3=excluded.score_stage3,
                position_stage=excluded.position_stage, signal_type=excluded.signal_type, reason=excluded.reason;
        """
        params = (
            signal['stock_code'], signal['analysis_date'], signal.get('f_score'), signal.get('t_score_raw'),
            signal.get('t_score_converted'), signal.get('score_stage1'), signal.get('score_stage2'),
            signal.get('score_stage3'), signal.get('position_stage', 0), signal.get('signal_type'), signal.get('reason')
        )
        return self.execute_non_query(query, params)
