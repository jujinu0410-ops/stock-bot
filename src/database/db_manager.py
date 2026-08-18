import sqlite3
import os
import json
import pandas as pd
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
                
                # 컬럼 자동 마이그레이션 (portfolio_positions V4-PILOT-C 전체 필드 지원)
                cursor.execute("PRAGMA table_info(portfolio_positions)")
                cols = [row[1] for row in cursor.fetchall()]
                
                v4_col_defs = {
                    "position_cycle_id": "TEXT",
                    "parameter_version": "TEXT DEFAULT 'V4-PILOT-C'",
                    "trade_mode": "TEXT DEFAULT 'NORMAL'",
                    "mode_override": "TEXT",
                    "entry_stage": "INTEGER DEFAULT 1",
                    "lifecycle_status": "TEXT DEFAULT 'POSITION_OPEN'",
                    "anchor_price_p0": "REAL DEFAULT 0",
                    "anchor_atr_a0": "REAL DEFAULT 0",
                    "anchor_created_at": "TEXT",
                    "atr_method": "TEXT DEFAULT 'WILDER'",
                    "atr_timeframe": "TEXT DEFAULT '1D_COMPLETED'",
                    "current_completed_atr": "REAL DEFAULT 0",
                    "natr_pct": "REAL DEFAULT 0",
                    "initial_stop": "REAL DEFAULT 0",
                    "profit_progress_1atr_reached": "INTEGER DEFAULT 0",
                    "highest_close": "REAL DEFAULT 0",
                    "highest_intraday": "REAL DEFAULT 0",
                    "previous_confirmed_stop": "REAL DEFAULT 0",
                    "ratchet_stop": "REAL DEFAULT 0",
                    "profit_activation_raw": "REAL DEFAULT 0",
                    "profit_activation_effective": "REAL DEFAULT 0",
                    "profit_activation_status": "TEXT DEFAULT 'INACTIVE'",
                    "highest_after_activation": "REAL DEFAULT 0",
                    "previous_profit_trail": "REAL DEFAULT 0",
                    "profit_trail": "REAL DEFAULT 0",
                    "effective_exit_line": "REAL DEFAULT 0",
                    "account_risk_pct": "REAL DEFAULT 0.005",
                    "risk_budget_amount": "REAL DEFAULT 0",
                    "risk_per_share": "REAL DEFAULT 0",
                    "recommended_quantity": "INTEGER DEFAULT 0",
                    "slippage_buffer": "REAL DEFAULT 0",
                    "data_validity_flag": "INTEGER DEFAULT 1",
                    "data_hold_reason": "TEXT",
                    "reanchor_flag": "INTEGER DEFAULT 0",
                    "highest_close_price": "REAL DEFAULT 0",
                    "confirmed_stop_price": "REAL DEFAULT 0"
                }
                
                for col_name, col_type in v4_col_defs.items():
                    if col_name not in cols:
                        cursor.execute(f"ALTER TABLE portfolio_positions ADD COLUMN {col_name} {col_type};")
                        logger.info(f"[DB Migration] portfolio_positions 테이블에 컬럼 추가: {col_name} ({col_type})")
                
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

                cursor.execute("PRAGMA table_info(disclosure_events)")
                disc_cols = [row[1] for row in cursor.fetchall()]
                disc_col_defs = {
                    "entity_scope": "TEXT DEFAULT 'PARENT'",
                    "subsidiary_name": "TEXT",
                    "original_rcept_no": "TEXT",
                    "amendment_chain_id": "TEXT",
                    "is_latest_version": "INTEGER DEFAULT 1",
                    "event_hazard": "TEXT DEFAULT 'LOW'",
                    "materiality_level": "TEXT DEFAULT 'UNKNOWN'",
                    "effective_severity": "TEXT DEFAULT 'LOW'",
                    "materiality_ratio_revenue": "REAL",
                    "materiality_ratio_backlog": "REAL",
                    "materiality_status": "TEXT DEFAULT 'MATERIALITY_UNKNOWN'",
                    "issue_amount": "REAL",
                    "new_shares": "REAL",
                    "existing_shares": "REAL",
                    "dilution_ratio": "REAL",
                    "ratio_to_market_cap": "REAL",
                    "severity": "TEXT DEFAULT 'LOW'",
                    "severity_reason": "TEXT"
                }
                for c_nm, c_typ in disc_col_defs.items():
                    if c_nm not in disc_cols:
                        cursor.execute(f"ALTER TABLE disclosure_events ADD COLUMN {c_nm} {c_typ};")

                cursor.execute("PRAGMA table_info(order_backlog_metrics)")
                ob_cols = [row[1] for row in cursor.fetchall()]
                ob_col_defs = {
                    "book_to_bill_period": "TEXT",
                    "book_to_bill_status": "TEXT DEFAULT 'PROVISIONAL'",
                    "numerator_orders": "REAL",
                    "provisional_new_orders": "REAL",
                    "is_unadjusted_bridge": "INTEGER DEFAULT 0",
                    "numerator_period_start": "TEXT",
                    "numerator_period_end": "TEXT",
                    "numerator_basis": "TEXT",
                    "denominator_revenue": "REAL",
                    "denominator_period_start": "TEXT",
                    "denominator_period_end": "TEXT",
                    "denominator_basis": "TEXT",
                    "scope_id": "TEXT DEFAULT 'CONSOLIDATED'",
                    "scope_description": "TEXT",
                    "confidence_level": "TEXT DEFAULT 'MEDIUM'",
                    "opportunity_confidence": "TEXT DEFAULT 'MEDIUM'",
                    "beginning_backlog": "REAL",
                    "ending_backlog": "REAL",
                    "recognized_revenue": "REAL",
                    "cancellations_adj": "TEXT DEFAULT 'UNKNOWN'",
                    "fx_adj": "TEXT DEFAULT 'UNKNOWN'",
                    "scope_adj": "TEXT DEFAULT 'UNKNOWN'",
                    "source_type": "TEXT DEFAULT 'COMPANY_REPORTED'"
                }
                for c_nm, c_typ in ob_col_defs.items():
                    if c_nm not in ob_cols:
                        cursor.execute(f"ALTER TABLE order_backlog_metrics ADD COLUMN {c_nm} {c_typ};")

                # Phase 6.1, 6.2 & 6.3: industry_runs 컬럼 마이그레이션
                cursor.execute("PRAGMA table_info(industry_runs)")
                ir_cols = [row[1] for row in cursor.fetchall()]
                ir_col_defs = {
                    "run_type": "TEXT NOT NULL DEFAULT 'PRODUCTION'",
                    "as_of_date": "TEXT DEFAULT '2026-08-17'",
                    "evidence_cutoff": "TEXT DEFAULT '2026-08-17'",
                    "source_count": "INTEGER DEFAULT 0",
                    "verified_evidence_count": "INTEGER DEFAULT 0",
                    "unverified_evidence_count": "INTEGER DEFAULT 0",
                    "synthetic_evidence_count": "INTEGER DEFAULT 0",
                    "replay_verified_count": "INTEGER DEFAULT 0",
                    "replay_failed_count": "INTEGER DEFAULT 0",
                    "replay_not_possible_count": "INTEGER DEFAULT 0",
                    "evidence_quality_score": "REAL DEFAULT 0.0",
                    "data_quality": "TEXT DEFAULT 'VALID'",
                    "run_status": "TEXT DEFAULT 'COMPLETED'",
                    "qa_status": "TEXT DEFAULT 'QA_PASSED'",
                    "scoring_version": "TEXT DEFAULT 'V1.0'"
                }
                for c_nm, c_typ in ir_col_defs.items():
                    if c_nm not in ir_cols:
                        cursor.execute(f"ALTER TABLE industry_runs ADD COLUMN {c_nm} {c_typ};")

                # Phase 6.2 & 6.3: industry_scores 컬럼 마이그레이션
                cursor.execute("PRAGMA table_info(industry_scores)")
                isc_cols = [row[1] for row in cursor.fetchall()]
                isc_col_defs = {
                    "verified_evidence_pct": "REAL DEFAULT 100.0",
                    "live_fetched_pct": "REAL DEFAULT 0.0",
                    "reference_verified_pct": "REAL DEFAULT 0.0",
                    "internal_derived_pct": "REAL DEFAULT 0.0",
                    "manual_evidence_pct": "REAL DEFAULT 0.0",
                    "synthetic_evidence_pct": "REAL DEFAULT 0.0",
                    "fresh_evidence_pct": "REAL DEFAULT 100.0",
                    "replay_verified_pct": "REAL DEFAULT 100.0",
                    "replay_failed_pct": "REAL DEFAULT 0.0",
                    "driver_count": "INTEGER DEFAULT 0",
                    "evidence_count": "INTEGER DEFAULT 0",
                    "qa_status": "TEXT DEFAULT 'QA_PASSED'"
                }
                for c_nm, c_typ in isc_col_defs.items():
                    if c_nm not in isc_cols:
                        cursor.execute(f"ALTER TABLE industry_scores ADD COLUMN {c_nm} {c_typ};")

                # Phase 6.2 & 6.3: industry_evidence 컬럼 마이그레이션
                cursor.execute("PRAGMA table_info(industry_evidence)")
                iev_cols = [row[1] for row in cursor.fetchall()]
                iev_col_defs = {
                    "evidence_type": "TEXT DEFAULT 'METRIC'",
                    "evidence_family": "TEXT DEFAULT 'GENERAL'",
                    "underlying_driver_id": "TEXT DEFAULT 'DRV_GEN'",
                    "origin_type": "TEXT DEFAULT 'LIVE_FETCHED'",
                    "collector_name": "TEXT DEFAULT 'SYSTEM_COLLECTOR'",
                    "fetch_method": "TEXT DEFAULT 'HTTP_REST_API'",
                    "http_status": "TEXT DEFAULT '200'",
                    "parser_name": "TEXT DEFAULT 'DefaultParser'",
                    "parser_version": "TEXT DEFAULT 'V1.0'",
                    "source_document_id": "TEXT DEFAULT 'DOC_001'",
                    "source_published_at": "TEXT DEFAULT '2026-08-17'",
                    "fetched_at": "TEXT DEFAULT '2026-08-17 09:00:00'",
                    "raw_value": "TEXT",
                    "extracted_fact_json": "TEXT",
                    "raw_payload_hash": "TEXT",
                    "normalization_rule": "TEXT",
                    "transformation_version": "TEXT DEFAULT 'NORM_V1.0'",
                    "driver_contribution_cap": "REAL DEFAULT 10.0",
                    "is_verified": "INTEGER DEFAULT 1",
                    "replay_status": "TEXT DEFAULT 'REPLAY_VERIFIED'"
                }
                for c_nm, c_typ in iev_col_defs.items():
                    if c_nm not in iev_cols:
                        cursor.execute(f"ALTER TABLE industry_evidence ADD COLUMN {c_nm} {c_typ};")

                # Phase 6.1: industry_company_map 컬럼 마이그레이션
                cursor.execute("PRAGMA table_info(industry_company_map)")
                ic_cols = [row[1] for row in cursor.fetchall()]
                ic_col_defs = {
                    "valid_from": "TEXT DEFAULT '2026-01-01'",
                    "valid_to": "TEXT DEFAULT '9999-12-31'",
                    "mapping_version": "TEXT DEFAULT 'V1.0'",
                    "is_active": "INTEGER DEFAULT 1"
                }
                for c_nm, c_typ in ic_col_defs.items():
                    if c_nm not in ic_cols:
                        cursor.execute(f"ALTER TABLE industry_company_map ADD COLUMN {c_nm} {c_typ};")

                # Phase 6.2 Unique Index 강제 생성
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_industry_evidence_run_ind_fact_doc ON industry_evidence(run_id, industry_id, factor_name, source_document_id);")

                # 구버전 Q4 더미 데이터 및 ETF 재무 데이터, 주말 비영업일 시세 데이터 정돈
                cursor.execute("DELETE FROM dart_financials WHERE quarter_code = 'Q4' OR stock_code IN ('161510', '490590', '088500', '371460', '484730')")
                cursor.execute("DELETE FROM kiwoom_daily WHERE stk_date IN ('20260808', '20260809') OR strftime('%w', substr(stk_date, 1, 4) || '-' || substr(stk_date, 5, 2) || '-' || substr(stk_date, 7, 2)) IN ('0', '6')")

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

    def get_stock_info(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """종목 기본 정보 조회"""
        code = str(stock_code).zfill(6)
        query = "SELECT * FROM stock_info WHERE stock_code = ? LIMIT 1;"
        rows = self.execute_query(query, (code,))
        return dict(rows[0]) if rows else None

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

    def upsert_quarterly_financial(self, qf: Dict[str, Any]) -> bool:
        """분기별 정밀 구조화 재무 데이터(quarterly_financials) 저장"""
        query = """
            INSERT INTO quarterly_financials 
            (stock_code, fiscal_year, fiscal_quarter, fiscal_period_end, fs_div,
             revenue, operating_income, operating_margin, net_income, operating_cash_flow,
             total_assets, total_liabilities, total_equity, inventory, accounts_receivable,
             cash_and_equivalents, interest_bearing_debt, net_debt, debt_ratio,
             capex, r_and_d, rcept_no, rcept_date, report_code, is_amended, source_quality, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code, fiscal_year, fiscal_quarter, fs_div) DO UPDATE SET
                fiscal_period_end=excluded.fiscal_period_end,
                revenue=excluded.revenue, operating_income=excluded.operating_income,
                operating_margin=excluded.operating_margin, net_income=excluded.net_income,
                operating_cash_flow=excluded.operating_cash_flow, total_assets=excluded.total_assets,
                total_liabilities=excluded.total_liabilities, total_equity=excluded.total_equity,
                inventory=excluded.inventory, accounts_receivable=excluded.accounts_receivable,
                cash_and_equivalents=excluded.cash_and_equivalents,
                interest_bearing_debt=excluded.interest_bearing_debt, net_debt=excluded.net_debt,
                debt_ratio=excluded.debt_ratio, capex=excluded.capex, r_and_d=excluded.r_and_d,
                rcept_no=excluded.rcept_no, rcept_date=excluded.rcept_date, report_code=excluded.report_code,
                is_amended=excluded.is_amended, source_quality=excluded.source_quality, updated_at=CURRENT_TIMESTAMP;
        """
        params = (
            qf['stock_code'], qf['fiscal_year'], qf['fiscal_quarter'], qf.get('fiscal_period_end', ''),
            qf.get('fs_div', 'CFS'), qf.get('revenue', 0.0), qf.get('operating_income', 0.0),
            qf.get('operating_margin', 0.0), qf.get('net_income', 0.0), qf.get('operating_cash_flow', 0.0),
            qf.get('total_assets', 0.0), qf.get('total_liabilities', 0.0), qf.get('total_equity', 0.0),
            qf.get('inventory', 0.0), qf.get('accounts_receivable', 0.0), qf.get('cash_and_equivalents', 0.0),
            qf.get('interest_bearing_debt', 0.0), qf.get('net_debt', 0.0), qf.get('debt_ratio', 0.0),
            qf.get('capex'), qf.get('r_and_d'), qf.get('rcept_no', ''), qf.get('rcept_date', ''),
            qf.get('report_code', ''), 1 if qf.get('is_amended', False) else 0, qf.get('source_quality', 'VALID')
        )
        return self.execute_non_query(query, params)

    def get_recent_quarterly_financials(self, stock_code: str, limit: int = 8) -> List[Dict[str, Any]]:
        """최근 분기별 재무 데이터 조회 (연도/분기 오름차순 정렬)"""
        query = """
            SELECT * FROM quarterly_financials
            WHERE stock_code = ?
            ORDER BY fiscal_year ASC, fiscal_quarter ASC
        """
        rows = self.execute_query(query, (stock_code,))
        res = [dict(r) for r in rows]
        if len(res) > limit:
            res = res[-limit:]
        return res

    def upsert_disclosure_event(self, event: Dict[str, Any]) -> bool:
        """Phase 5.3: 공시 이벤트 저장 (event_hazard, materiality_level, effective_severity, 희석 파싱 지표 포함)"""
        query = """
            INSERT INTO disclosure_events
            (stock_code, event_type, rcept_no, rcept_date, report_name, amount, currency,
             counterparty, contract_start, contract_end, revenue_ratio, progression_stage,
             entity_scope, subsidiary_name, original_rcept_no, amendment_chain_id,
             is_latest_version, event_hazard, materiality_level, effective_severity,
             materiality_ratio_revenue, materiality_ratio_backlog, materiality_status,
             issue_amount, new_shares, existing_shares, dilution_ratio, ratio_to_market_cap,
             severity, severity_reason, is_negative_event, source_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, rcept_no) DO UPDATE SET
                event_type=excluded.event_type, rcept_date=excluded.rcept_date,
                report_name=excluded.report_name, amount=excluded.amount,
                currency=excluded.currency, counterparty=excluded.counterparty,
                contract_start=excluded.contract_start, contract_end=excluded.contract_end,
                revenue_ratio=excluded.revenue_ratio, progression_stage=excluded.progression_stage,
                entity_scope=excluded.entity_scope, subsidiary_name=excluded.subsidiary_name,
                original_rcept_no=excluded.original_rcept_no, amendment_chain_id=excluded.amendment_chain_id,
                is_latest_version=excluded.is_latest_version,
                event_hazard=excluded.event_hazard,
                materiality_level=excluded.materiality_level,
                effective_severity=excluded.effective_severity,
                materiality_ratio_revenue=excluded.materiality_ratio_revenue,
                materiality_ratio_backlog=excluded.materiality_ratio_backlog,
                materiality_status=excluded.materiality_status,
                issue_amount=excluded.issue_amount,
                new_shares=excluded.new_shares,
                existing_shares=excluded.existing_shares,
                dilution_ratio=excluded.dilution_ratio,
                ratio_to_market_cap=excluded.ratio_to_market_cap,
                severity=excluded.severity, severity_reason=excluded.severity_reason,
                is_negative_event=excluded.is_negative_event, source_quality=excluded.source_quality;
        """
        params = (
            event['stock_code'], event['event_type'], event['rcept_no'], event['rcept_date'],
            event['report_name'], event.get('amount'), event.get('currency', 'KRW'),
            event.get('counterparty'), event.get('contract_start'), event.get('contract_end'),
            event.get('revenue_ratio'), event.get('progression_stage', 'FINAL_CONTRACT'),
            event.get('entity_scope', 'PARENT'), event.get('subsidiary_name'),
            event.get('original_rcept_no'), event.get('amendment_chain_id'),
            1 if event.get('is_latest_version', True) else 0,
            event.get('event_hazard', 'LOW'),
            event.get('materiality_level', 'UNKNOWN'),
            event.get('effective_severity', event.get('severity', 'LOW')),
            event.get('materiality_ratio_revenue'), event.get('materiality_ratio_backlog'),
            event.get('materiality_status', 'MATERIALITY_UNKNOWN'),
            event.get('issue_amount'), event.get('new_shares'), event.get('existing_shares'),
            event.get('dilution_ratio'), event.get('ratio_to_market_cap'),
            event.get('severity', 'LOW'), event.get('severity_reason', ''),
            1 if event.get('is_negative_event', False) else 0, event.get('source_quality', 'VALID')
        )
        return self.execute_non_query(query, params)

    def get_recent_disclosure_events(self, stock_code: str, limit: int = 20, only_latest: bool = True) -> List[Dict[str, Any]]:
        """최근 공시 이벤트 조회 (일자 내림차순, 기본적으로 최신본만 조회)"""
        if only_latest:
            query = """
                SELECT * FROM disclosure_events
                WHERE stock_code = ? AND is_latest_version = 1
                ORDER BY rcept_date DESC, id DESC
                LIMIT ?
            """
        else:
            query = """
                SELECT * FROM disclosure_events
                WHERE stock_code = ?
                ORDER BY rcept_date DESC, id DESC
                LIMIT ?
            """
        rows = self.execute_query(query, (stock_code, limit)) or []
        return [dict(r) for r in rows]

    def upsert_order_backlog(self, ob: Dict[str, Any]) -> bool:
        """Phase 5.3: 수주잔고 및 신규수주 지표 저장 (5단계 B2B 상태, provisional_new_orders, opportunity_confidence 포함)"""
        query = """
            INSERT INTO order_backlog_metrics
            (stock_code, fiscal_year, fiscal_quarter, order_backlog, new_orders,
             provisional_new_orders, is_unadjusted_bridge,
             order_backlog_yoy, order_backlog_to_revenue, book_to_bill,
             book_to_bill_period, book_to_bill_status, numerator_orders,
             numerator_period_start, numerator_period_end, numerator_basis,
             denominator_revenue, denominator_period_start, denominator_period_end, denominator_basis,
             scope_id, scope_description, confidence_level, opportunity_confidence,
             beginning_backlog, ending_backlog, recognized_revenue,
             cancellations_adj, fx_adj, scope_adj,
             source_type, disclosure_status, source_quality, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code, fiscal_year, fiscal_quarter) DO UPDATE SET
                order_backlog=excluded.order_backlog, new_orders=excluded.new_orders,
                provisional_new_orders=excluded.provisional_new_orders,
                is_unadjusted_bridge=excluded.is_unadjusted_bridge,
                order_backlog_yoy=excluded.order_backlog_yoy,
                order_backlog_to_revenue=excluded.order_backlog_to_revenue,
                book_to_bill=excluded.book_to_bill,
                book_to_bill_period=excluded.book_to_bill_period,
                book_to_bill_status=excluded.book_to_bill_status,
                numerator_orders=excluded.numerator_orders,
                numerator_period_start=excluded.numerator_period_start,
                numerator_period_end=excluded.numerator_period_end,
                numerator_basis=excluded.numerator_basis,
                denominator_revenue=excluded.denominator_revenue,
                denominator_period_start=excluded.denominator_period_start,
                denominator_period_end=excluded.denominator_period_end,
                denominator_basis=excluded.denominator_basis,
                scope_id=excluded.scope_id,
                scope_description=excluded.scope_description,
                confidence_level=excluded.confidence_level,
                opportunity_confidence=excluded.opportunity_confidence,
                beginning_backlog=excluded.beginning_backlog,
                ending_backlog=excluded.ending_backlog,
                recognized_revenue=excluded.recognized_revenue,
                cancellations_adj=excluded.cancellations_adj,
                fx_adj=excluded.fx_adj,
                scope_adj=excluded.scope_adj,
                source_type=excluded.source_type,
                disclosure_status=excluded.disclosure_status,
                source_quality=excluded.source_quality,
                updated_at=CURRENT_TIMESTAMP;
        """
        params = (
            ob['stock_code'], ob['fiscal_year'], ob['fiscal_quarter'],
            ob.get('order_backlog'), ob.get('new_orders'),
            ob.get('provisional_new_orders'), 1 if ob.get('is_unadjusted_bridge', False) else 0,
            ob.get('order_backlog_yoy'),
            ob.get('order_backlog_to_revenue'), ob.get('book_to_bill'),
            ob.get('book_to_bill_period', '2026 Q2 (Discrete Quarter)'),
            ob.get('book_to_bill_status', 'PROVISIONAL'),
            ob.get('numerator_orders'),
            ob.get('numerator_period_start'), ob.get('numerator_period_end'), ob.get('numerator_basis'),
            ob.get('denominator_revenue'),
            ob.get('denominator_period_start'), ob.get('denominator_period_end'), ob.get('denominator_basis'),
            ob.get('scope_id', 'CONSOLIDATED'), ob.get('scope_description', '연결 재무제표 기준 (CFS)'),
            ob.get('confidence_level', 'MEDIUM'),
            ob.get('opportunity_confidence', 'MEDIUM'),
            ob.get('beginning_backlog'), ob.get('ending_backlog'), ob.get('recognized_revenue'),
            ob.get('cancellations_adj', 'UNKNOWN'), ob.get('fx_adj', 'UNKNOWN'), ob.get('scope_adj', 'UNKNOWN'),
            ob.get('source_type', 'COMPANY_REPORTED'),
            ob.get('disclosure_status', 'DISCLOSED'), ob.get('source_quality', 'VALID')
        )
        return self.execute_non_query(query, params)

    def get_recent_order_backlog(self, stock_code: str, limit: int = 8) -> List[Dict[str, Any]]:
        """최근 수주잔고 지표 조회 (연도/분기 오름차순)"""
        query = """
            SELECT * FROM order_backlog_metrics
            WHERE stock_code = ?
            ORDER BY fiscal_year ASC, fiscal_quarter ASC
        """
        rows = self.execute_query(query, (stock_code,)) or []
        res = [dict(r) for r in rows]
        if len(res) > limit:
            res = res[-limit:]
        return res

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

    def get_daily_prices(self, stock_code: str) -> pd.DataFrame:
        """특정 종목의 일봉 OHLCV 시세 데이터 반환 (날짜 오름차순)"""
        code = str(stock_code).zfill(6)
        rows = self.execute_query("""
            SELECT stk_date, open_price, high_price, low_price, close_price, volume, foreign_net_buy, inst_net_buy
            FROM kiwoom_daily
            WHERE stock_code = ?
            ORDER BY stk_date ASC
        """, (code,))
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])

    def is_dispatch_already_sent(self, dispatch_id: str) -> bool:
        """해당 회차(예: 20260814_1120 또는 20260814_1535) 또는 잔고 Fingerprint 메일이 이미 발송되었는지 확인"""
        rows = self.execute_query("SELECT dispatch_id, sent_at FROM dispatch_history WHERE dispatch_id = ?", (dispatch_id,))
        return bool(rows and len(rows) > 0)

    def record_dispatch_success(self, dispatch_id: str, dispatch_type: str, recipient: str, subject: str) -> None:
        """메일 발송 성공 기록 저장"""
        self.execute_non_query("""
            INSERT OR REPLACE INTO dispatch_history (dispatch_id, dispatch_type, recipient, subject, sent_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (dispatch_id, dispatch_type, recipient, subject))

    def get_position_lots(self, stock_code: str, cycle_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """특정 종목의 매수 Lot 목록 반환"""
        code = str(stock_code).zfill(6)
        if cycle_id:
            rows = self.execute_query(
                "SELECT * FROM position_lots WHERE stock_code = ? AND position_cycle_id = ? ORDER BY buy_datetime ASC",
                (code, cycle_id)
            )
        else:
            rows = self.execute_query(
                "SELECT * FROM position_lots WHERE stock_code = ? ORDER BY buy_datetime ASC",
                (code,)
            )
        return [dict(r) for r in rows] if rows else []

    def add_position_lot(self, lot_data: Dict[str, Any]) -> bool:
        """추가 매수 Lot 기록"""
        query = """
            INSERT INTO position_lots 
            (position_cycle_id, stock_code, buy_datetime, quantity, entry_price, lot_anchor_atr, lot_initial_stop, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            lot_data.get("position_cycle_id"),
            str(lot_data["stock_code"]).zfill(6),
            lot_data.get("buy_datetime"),
            int(lot_data.get("quantity", 0)),
            float(lot_data.get("entry_price", 0.0)),
            float(lot_data.get("lot_anchor_atr", 0.0)),
            float(lot_data.get("lot_initial_stop", 0.0)),
            lot_data.get("source", "MANUAL")
        )
        return self.execute_non_query(query, params)

    # ----------------------------------------------------
    # Phase 6, 6.1 & 6.2: Weekly Industry Radar DB Methods
    # ----------------------------------------------------
    def upsert_industry_run(self, run: Dict[str, Any]) -> bool:
        """Industry Radar 실행 회차 정보 저장 (TEST_FIXTURE vs PRODUCTION 물리적 분리 및 QA 메타데이터)"""
        query = """
            INSERT INTO industry_runs 
            (run_id, run_date, run_mode, run_type, as_of_date, evidence_cutoff, 
             source_count, verified_evidence_count, unverified_evidence_count,
             synthetic_evidence_count, replay_verified_count, replay_failed_count,
             replay_not_possible_count, evidence_quality_score, data_quality,
             run_status, qa_status, scoring_version, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                run_date=excluded.run_date,
                run_mode=excluded.run_mode,
                run_type=excluded.run_type,
                as_of_date=excluded.as_of_date,
                evidence_cutoff=excluded.evidence_cutoff,
                source_count=excluded.source_count,
                verified_evidence_count=excluded.verified_evidence_count,
                unverified_evidence_count=excluded.unverified_evidence_count,
                synthetic_evidence_count=excluded.synthetic_evidence_count,
                replay_verified_count=excluded.replay_verified_count,
                replay_failed_count=excluded.replay_failed_count,
                replay_not_possible_count=excluded.replay_not_possible_count,
                evidence_quality_score=excluded.evidence_quality_score,
                data_quality=excluded.data_quality,
                run_status=excluded.run_status,
                qa_status=excluded.qa_status,
                scoring_version=excluded.scoring_version,
                description=excluded.description;
        """
        params = (
            run["run_id"], run["run_date"], run.get("run_mode", "WEEKLY_UPDATE"),
            run.get("run_type", "PRODUCTION"),
            run.get("as_of_date", run["run_date"]),
            run.get("evidence_cutoff", run["run_date"]),
            int(run.get("source_count", 0)),
            int(run.get("verified_evidence_count", 0)),
            int(run.get("unverified_evidence_count", 0)),
            int(run.get("synthetic_evidence_count", 0)),
            int(run.get("replay_verified_count", 0)),
            int(run.get("replay_failed_count", 0)),
            int(run.get("replay_not_possible_count", 0)),
            float(run.get("evidence_quality_score", 0.0)),
            run.get("data_quality", "VALID"),
            run.get("run_status", "COMPLETED"),
            run.get("qa_status", "QA_PASSED"),
            run.get("scoring_version", "V1.0"),
            run.get("description", "")
        )
        return self.execute_non_query(query, params)

    def delete_industry_evidence_for_run(self, run_id: str, industry_id: Optional[str] = None) -> bool:
        """특정 Run의 증거(Evidence) 데이터 삭제 (중복 누적 방지용)"""
        if industry_id:
            return self.execute_non_query("DELETE FROM industry_evidence WHERE run_id = ? AND industry_id = ?", (run_id, industry_id))
        return self.execute_non_query("DELETE FROM industry_evidence WHERE run_id = ?", (run_id,))

    def insert_industry_evidence(self, ev: Dict[str, Any]) -> bool:
        """Industry Radar Provenance 증거 데이터 개별 저장 (Phase 6.3 메타데이터 완비)"""
        query = """
            INSERT INTO industry_evidence
            (run_id, industry_id, factor_name, evidence_type, evidence_family, underlying_driver_id,
             origin_type, collector_name, fetch_method, http_status, parser_name, parser_version,
             source_type, source_name, source_reference, source_document_id, source_published_at,
             fetched_at, evidence_date, raw_value, extracted_fact_json, raw_payload_hash,
             normalization_rule, transformation_version, normalized_value, driver_contribution_cap,
             is_verified, replay_status, evidence_direction, reliability, freshness_days, rationale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, industry_id, factor_name, source_document_id) DO UPDATE SET
                evidence_type=excluded.evidence_type,
                evidence_family=excluded.evidence_family,
                underlying_driver_id=excluded.underlying_driver_id,
                origin_type=excluded.origin_type,
                collector_name=excluded.collector_name,
                fetch_method=excluded.fetch_method,
                http_status=excluded.http_status,
                parser_name=excluded.parser_name,
                parser_version=excluded.parser_version,
                source_type=excluded.source_type,
                source_name=excluded.source_name,
                source_reference=excluded.source_reference,
                source_published_at=excluded.source_published_at,
                fetched_at=excluded.fetched_at,
                evidence_date=excluded.evidence_date,
                raw_value=excluded.raw_value,
                extracted_fact_json=excluded.extracted_fact_json,
                raw_payload_hash=excluded.raw_payload_hash,
                normalization_rule=excluded.normalization_rule,
                transformation_version=excluded.transformation_version,
                normalized_value=excluded.normalized_value,
                driver_contribution_cap=excluded.driver_contribution_cap,
                is_verified=excluded.is_verified,
                replay_status=excluded.replay_status,
                evidence_direction=excluded.evidence_direction,
                reliability=excluded.reliability,
                freshness_days=excluded.freshness_days,
                rationale=excluded.rationale;
        """
        # 해시 및 canonical fact json 생성
        raw_val = str(ev.get("raw_value", ""))
        fact_json = ev.get("extracted_fact_json", "")
        payload_hash = ev.get("raw_payload_hash") or str(hash(f"{ev.get('source_name')}_{raw_val}_{fact_json}_{ev.get('source_published_at')}"))
        is_ver = 1 if (ev.get("source_reference") and ev.get("origin_type") not in ("UNVERIFIED_SOURCE", "SYNTHETIC", "TEST_FIXTURE")) else (1 if ev.get("is_verified", True) else 0)

        params = (
            ev["run_id"], ev["industry_id"], ev["factor_name"],
            ev.get("evidence_type", ev.get("evidence_family", "METRIC")),
            ev.get("evidence_family", "GENERAL"),
            ev.get("underlying_driver_id", "DRV_GEN"),
            ev.get("origin_type", "LIVE_FETCHED"),
            ev.get("collector_name", "SYSTEM_COLLECTOR"),
            ev.get("fetch_method", "HTTP_REST_API"),
            ev.get("http_status", "200"),
            ev.get("parser_name", "DefaultParser"),
            ev.get("parser_version", "V1.0"),
            ev.get("source_type", "INDUSTRY_REPORT"),
            ev.get("source_name", "ANALYST_CONSENSUS"),
            ev.get("source_reference", ""),
            ev.get("source_document_id", f"DOC_{abs(hash(raw_val)) % 10000:04d}"),
            ev.get("source_published_at", ev.get("evidence_date", "2026-08-17")),
            ev.get("fetched_at", "2026-08-17 09:00:00"),
            ev.get("evidence_date", "2026-08-17"),
            raw_val,
            fact_json,
            payload_hash,
            ev.get("normalization_rule", "LINEAR_SCALED"),
            ev.get("transformation_version", "NORM_V1.0"),
            float(ev.get("normalized_value", 0.0)),
            float(ev.get("driver_contribution_cap", 10.0)),
            is_ver,
            ev.get("replay_status", "REPLAY_VERIFIED"),
            ev.get("evidence_direction", "POSITIVE"),
            ev.get("reliability", "HIGH" if is_ver else "LOW"),
            int(ev.get("freshness_days", 0)),
            ev.get("rationale", "")
        )
        return self.execute_non_query(query, params)

    def get_industry_evidence_for_run(
        self,
        run_id: str,
        industry_id: Optional[str] = None,
        factor_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """특정 Run의 증거(Evidence) Provenance 목록 조회"""
        conditions = ["run_id = ?"]
        params = [run_id]
        if industry_id:
            conditions.append("industry_id = ?")
            params.append(industry_id)
        if factor_name:
            conditions.append("factor_name = ?")
            params.append(factor_name)

        where_clause = " AND ".join(conditions)
        query = f"SELECT * FROM industry_evidence WHERE {where_clause} ORDER BY id ASC"
        rows = self.execute_query(query, tuple(params))
        return [dict(r) for r in rows] if rows else []

    def upsert_industry_score(self, score: Dict[str, Any]) -> bool:
        """Industry Radar 산업별 점수 및 근거 저장 (Phase 6.3 QA 및 Replay 필드 포함)"""
        query = """
            INSERT INTO industry_scores
            (run_id, industry_id, industry_name, total_score, policy_continuity, earnings_linkage,
             visibility_order_revenue, catalysts_score, valuation_burden, downside_risk_score,
             industry_bucket, industry_gate, industry_confidence, positive_evidence, negative_evidence,
             catalysts_6_18m, downside_risks, thesis,
             live_fetched_pct, reference_verified_pct, internal_derived_pct, manual_evidence_pct,
             synthetic_evidence_pct, fresh_evidence_pct, replay_verified_pct, replay_failed_pct,
             driver_count, evidence_count, qa_status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(run_id, industry_id) DO UPDATE SET
                industry_name=excluded.industry_name,
                total_score=excluded.total_score,
                policy_continuity=excluded.policy_continuity,
                earnings_linkage=excluded.earnings_linkage,
                visibility_order_revenue=excluded.visibility_order_revenue,
                catalysts_score=excluded.catalysts_score,
                valuation_burden=excluded.valuation_burden,
                downside_risk_score=excluded.downside_risk_score,
                industry_bucket=excluded.industry_bucket,
                industry_gate=excluded.industry_gate,
                industry_confidence=excluded.industry_confidence,
                positive_evidence=excluded.positive_evidence,
                negative_evidence=excluded.negative_evidence,
                catalysts_6_18m=excluded.catalysts_6_18m,
                downside_risks=excluded.downside_risks,
                thesis=excluded.thesis,
                live_fetched_pct=excluded.live_fetched_pct,
                reference_verified_pct=excluded.reference_verified_pct,
                internal_derived_pct=excluded.internal_derived_pct,
                manual_evidence_pct=excluded.manual_evidence_pct,
                synthetic_evidence_pct=excluded.synthetic_evidence_pct,
                fresh_evidence_pct=excluded.fresh_evidence_pct,
                replay_verified_pct=excluded.replay_verified_pct,
                replay_failed_pct=excluded.replay_failed_pct,
                driver_count=excluded.driver_count,
                evidence_count=excluded.evidence_count,
                qa_status=excluded.qa_status,
                updated_at=CURRENT_TIMESTAMP;
        """
        params = (
            score["run_id"], score["industry_id"], score.get("industry_name", score["industry_id"]),
            float(score.get("total_score", 0.0)),
            float(score.get("policy_continuity", 0.0)),
            float(score.get("earnings_linkage", 0.0)),
            float(score.get("visibility_order_revenue", 0.0)),
            float(score.get("catalysts_score", 0.0)),
            float(score.get("valuation_burden", 0.0)),
            float(score.get("downside_risk_score", 0.0)),
            score.get("industry_bucket", "WATCH"),
            score.get("industry_gate", "INDUSTRY_WAIT"),
            score.get("industry_confidence", "MEDIUM"),
            score.get("positive_evidence", ""),
            score.get("negative_evidence", ""),
            score.get("catalysts_6_18m", ""),
            score.get("downside_risks", ""),
            score.get("thesis", ""),
            float(score.get("live_fetched_pct", 0.0)),
            float(score.get("reference_verified_pct", 0.0)),
            float(score.get("internal_derived_pct", 0.0)),
            float(score.get("manual_evidence_pct", 0.0)),
            float(score.get("synthetic_evidence_pct", 0.0)),
            float(score.get("fresh_evidence_pct", 100.0)),
            float(score.get("replay_verified_pct", 100.0)),
            float(score.get("replay_failed_pct", 0.0)),
            int(score.get("driver_count", 0)),
            int(score.get("evidence_count", 0)),
            score.get("qa_status", "QA_PASSED")
        )
        return self.execute_non_query(query, params)

    def upsert_industry_company_map(self, mapping: Dict[str, Any]) -> bool:
        """기업-산업 매핑 및 Exposure Type 저장 (버전/유효기간 관리)"""
        query = """
            INSERT INTO industry_company_map
            (industry_id, stock_code, stock_name, exposure_type, evidence_rationale, 
             is_eligible_candidate, valid_from, valid_to, mapping_version, is_active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(industry_id, stock_code) DO UPDATE SET
                stock_name=excluded.stock_name,
                exposure_type=excluded.exposure_type,
                evidence_rationale=excluded.evidence_rationale,
                is_eligible_candidate=excluded.is_eligible_candidate,
                valid_from=excluded.valid_from,
                valid_to=excluded.valid_to,
                mapping_version=excluded.mapping_version,
                is_active=excluded.is_active,
                updated_at=CURRENT_TIMESTAMP;
        """
        code = str(mapping["stock_code"]).zfill(6)
        is_eligible = 0 if mapping.get("exposure_type") == "THEME_ONLY" else (1 if mapping.get("is_eligible_candidate", True) else 0)
        params = (
            mapping["industry_id"], code, mapping.get("stock_name", code),
            mapping.get("exposure_type", "DIRECT_CORE"),
            mapping.get("evidence_rationale", ""),
            is_eligible,
            mapping.get("valid_from", "2026-01-01"),
            mapping.get("valid_to", "9999-12-31"),
            mapping.get("mapping_version", "V1.0"),
            1 if mapping.get("is_active", True) else 0
        )
        return self.execute_non_query(query, params)

    def get_latest_industry_score(self, industry_id: str, run_type: str = "PRODUCTION") -> Optional[Dict[str, Any]]:
        """
        특정 산업의 최신 Radar 점수 조회
        - run_type = 'PRODUCTION' (기본값): TEST_FIXTURE는 절대 조회되지 않음
        """
        query = """
            SELECT s.*, r.run_date, r.run_mode, r.run_type, r.as_of_date, r.data_quality, r.scoring_version
            FROM industry_scores s
            JOIN industry_runs r ON s.run_id = r.run_id
            WHERE s.industry_id = ? AND r.run_type = ?
            ORDER BY r.run_date DESC, r.id DESC, s.updated_at DESC
            LIMIT 1
        """
        rows = self.execute_query(query, (industry_id, run_type))
        return dict(rows[0]) if rows else None

    def get_all_latest_industry_scores(self, run_type: str = "PRODUCTION") -> List[Dict[str, Any]]:
        """모든 산업의 최신 Radar 점수 목록 조회 (기본값: PRODUCTION만)"""
        query = """
            SELECT s.*, r.run_date, r.run_mode, r.run_type, r.as_of_date, r.data_quality, r.scoring_version
            FROM industry_scores s
            JOIN industry_runs r ON s.run_id = r.run_id
            WHERE r.run_type = ? AND s.run_id = (
                SELECT run_id FROM industry_runs 
                WHERE run_type = ? 
                ORDER BY run_date DESC, id DESC LIMIT 1
            )
            ORDER BY s.total_score DESC
        """
        rows = self.execute_query(query, (run_type, run_type))
        return [dict(r) for r in rows] if rows else []

    def get_company_industry_mapping(
        self,
        stock_code: str,
        as_of_date: Optional[str] = None,
        version: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """특정 종목의 산업 매핑 및 Exposure Type 조회 (시점/버전 이력 지원)"""
        code = str(stock_code).zfill(6)
        if version:
            query = """
                SELECT * FROM industry_company_map
                WHERE stock_code = ? AND mapping_version = ?
                LIMIT 1
            """
            rows = self.execute_query(query, (code, version))
        elif as_of_date:
            query = """
                SELECT * FROM industry_company_map
                WHERE stock_code = ? AND valid_from <= ? AND valid_to >= ?
                ORDER BY mapping_version DESC
                LIMIT 1
            """
            rows = self.execute_query(query, (code, as_of_date, as_of_date))
        else:
            query = """
                SELECT * FROM industry_company_map
                WHERE stock_code = ? AND is_active = 1
                ORDER BY mapping_version DESC
                LIMIT 1
            """
            rows = self.execute_query(query, (code,))
        return dict(rows[0]) if rows else None

    # =========================================================================
    # Phase 7: Shadow Scan Journal & Signal Outcomes Methods
    # =========================================================================
    def insert_scan_journal(self, entry: Dict[str, Any]) -> bool:
        """
        Phase 7 Append-Only Immutable Scan Journal 적재
        - 과거 레코드 OVERWRITE 금지 (동일 journal_id 충돌 시 에러 또는 False 반환)
        """
        query = """
            INSERT INTO scan_journal (
                journal_id, scan_timestamp, trading_date, snapshot_reason,
                stock_code, stock_name, market_price,
                industry_run_id, industry_score, industry_gate, industry_confidence,
                exposure_type, mapping_version,
                fundamental_state, turnaround_type, turnaround_label,
                forward_opportunity, forward_confidence, forward_risk, forward_risk_override_tag,
                book_to_bill_summary,
                t_score, technical_state, technical_action, intraday_data_quality,
                atr14, natr, candidate_ref_price, candidate_stop_price, candidate_target_price, atr_mode,
                shadow_integrated_state, primary_blocker, all_blockers,
                existing_f_score, existing_final_score, buy_approval,
                p0_status, position_cycle_id,
                financial_data_asof, forward_data_asof, intraday_last_timestamp, scoring_versions
            ) VALUES (
                :journal_id, :scan_timestamp, :trading_date, :snapshot_reason,
                :stock_code, :stock_name, :market_price,
                :industry_run_id, :industry_score, :industry_gate, :industry_confidence,
                :exposure_type, :mapping_version,
                :fundamental_state, :turnaround_type, :turnaround_label,
                :forward_opportunity, :forward_confidence, :forward_risk, :forward_risk_override_tag,
                :book_to_bill_summary,
                :t_score, :technical_state, :technical_action, :intraday_data_quality,
                :atr14, :natr, :candidate_ref_price, :candidate_stop_price, :candidate_target_price, :atr_mode,
                :shadow_integrated_state, :primary_blocker, :all_blockers,
                :existing_f_score, :existing_final_score, :buy_approval,
                :p0_status, :position_cycle_id,
                :financial_data_asof, :forward_data_asof, :intraday_last_timestamp, :scoring_versions
            )
        """
        params = {
            "journal_id": entry["journal_id"],
            "scan_timestamp": entry.get("scan_timestamp", ""),
            "trading_date": entry.get("trading_date", ""),
            "snapshot_reason": entry.get("snapshot_reason", "MANUAL"),
            "stock_code": str(entry["stock_code"]).zfill(6),
            "stock_name": entry.get("stock_name", ""),
            "market_price": float(entry.get("market_price", 0.0)),
            "industry_run_id": entry.get("industry_run_id", "PROD_2026_W33_001"),
            "industry_score": float(entry.get("industry_score", 0.0)),
            "industry_gate": entry.get("industry_gate", "INDUSTRY_WAIT"),
            "industry_confidence": entry.get("industry_confidence", "UNKNOWN"),
            "exposure_type": entry.get("exposure_type", "DIRECT_CORE"),
            "mapping_version": entry.get("mapping_version", "V1.0"),
            "fundamental_state": entry.get("fundamental_state", "UNKNOWN"),
            "turnaround_type": entry.get("turnaround_type", "UNKNOWN"),
            "turnaround_label": entry.get("turnaround_label", ""),
            "forward_opportunity": entry.get("forward_opportunity", "UNKNOWN"),
            "forward_confidence": entry.get("forward_confidence", "UNKNOWN"),
            "forward_risk": entry.get("forward_risk", "NONE"),
            "forward_risk_override_tag": entry.get("forward_risk_override_tag", "NONE"),
            "book_to_bill_summary": entry.get("book_to_bill_summary", "NOT_APPLICABLE"),
            "t_score": float(entry.get("t_score", 0.0)),
            "technical_state": entry.get("technical_state", "NEUTRAL"),
            "technical_action": entry.get("technical_action", "BUY_BLOCKED"),
            "intraday_data_quality": entry.get("intraday_data_quality", "VALID"),
            "atr14": float(entry.get("atr14", 0.0)),
            "natr": float(entry.get("natr", 0.0)),
            "candidate_ref_price": float(entry.get("candidate_ref_price", 0.0)),
            "candidate_stop_price": float(entry.get("candidate_stop_price", 0.0)),
            "candidate_target_price": float(entry.get("candidate_target_price", 0.0)),
            "atr_mode": entry.get("atr_mode", "NORMAL"),
            "shadow_integrated_state": entry.get("shadow_integrated_state", "DATA_REVIEW"),
            "primary_blocker": entry.get("primary_blocker", "NONE"),
            "all_blockers": json.dumps(entry.get("all_blockers", []), ensure_ascii=False) if isinstance(entry.get("all_blockers"), list) else entry.get("all_blockers", "[]"),
            "existing_f_score": float(entry.get("existing_f_score", 0.0)),
            "existing_final_score": float(entry.get("existing_final_score", 0.0)),
            "buy_approval": entry.get("buy_approval", "🔴 OFF (매수 금지/관망)"),
            "p0_status": entry.get("p0_status", "NONE"),
            "position_cycle_id": entry.get("position_cycle_id", "NONE"),
            "financial_data_asof": entry.get("financial_data_asof", ""),
            "forward_data_asof": entry.get("forward_data_asof", ""),
            "intraday_last_timestamp": entry.get("intraday_last_timestamp", ""),
            "scoring_versions": json.dumps(entry.get("scoring_versions", {}), ensure_ascii=False) if isinstance(entry.get("scoring_versions"), dict) else entry.get("scoring_versions", "{}")
        }
        res = self.execute_non_query(query, params)
        return (res is not None and res > 0)

    def get_scan_journal(self, journal_id: str) -> Optional[Dict[str, Any]]:
        """특정 journal_id의 불변 스냅샷 조회"""
        query = "SELECT * FROM scan_journal WHERE journal_id = ? LIMIT 1"
        rows = self.execute_query(query, (journal_id,))
        if not rows:
            return None
        r = dict(rows[0])
        if r.get("all_blockers"):
            try:
                r["all_blockers"] = json.loads(r["all_blockers"])
            except Exception:
                pass
        return r

    def get_scan_journals_for_stock(self, stock_code: str, limit: int = 50) -> List[Dict[str, Any]]:
        """종목별 scan journal 이력 조회"""
        code = str(stock_code).zfill(6)
        query = "SELECT * FROM scan_journal WHERE stock_code = ? ORDER BY scan_timestamp DESC LIMIT ?"
        rows = self.execute_query(query, (code, limit))
        results = []
        for row in (rows or []):
            r = dict(row)
            if r.get("all_blockers"):
                try:
                    r["all_blockers"] = json.loads(r["all_blockers"])
                except Exception:
                    pass
            results.append(r)
        return results

    def get_all_scan_journals(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """모든 scan journal 이력 조회 (시간 오름차순)"""
        query = "SELECT * FROM scan_journal ORDER BY id ASC LIMIT ?"
        rows = self.execute_query(query, (limit,))
        results = []
        for row in (rows or []):
            r = dict(row)
            if r.get("all_blockers"):
                try:
                    r["all_blockers"] = json.loads(r["all_blockers"])
                except Exception:
                    pass
            results.append(r)
        return results

    def get_recent_daily_candles(self, stock_code: str, limit: int = 60) -> List[Dict[str, Any]]:
        """종목별 최근 일봉 캔들 조회 (최신일자 내림차순)"""
        code = str(stock_code).zfill(6)
        query = "SELECT * FROM kiwoom_daily WHERE stock_code = ? ORDER BY stk_date DESC LIMIT ?"
        rows = self.execute_query(query, (code, limit))
        return [dict(r) for r in (rows or [])]



    def upsert_signal_outcome(self, outcome: Dict[str, Any]) -> bool:
        """
        Phase 7 Signal Outcome 성과 기록/갱신
        """
        query = """
            INSERT INTO signal_outcomes (
                journal_id, outcome_type, entry_reference_price, entry_atr14,
                trading_days_evaluated, return_5d, return_10d, return_20d, return_40d,
                mfe_5d, mae_5d, mfe_10d, mae_10d, mfe_20d, mae_20d, mfe_40d, mae_40d,
                mfe_20d_atr, mae_20d_atr, max_price_40d, min_price_40d,
                hit_plus_1atr, hit_plus_2atr, hit_plus_3atr,
                hit_minus_1atr, hit_minus_1_5atr, stop_hit, trailing_activation_hit,
                outcome_status
            ) VALUES (
                :journal_id, :outcome_type, :entry_reference_price, :entry_atr14,
                :trading_days_evaluated, :return_5d, :return_10d, :return_20d, :return_40d,
                :mfe_5d, :mae_5d, :mfe_10d, :mae_10d, :mfe_20d, :mae_20d, :mfe_40d, :mae_40d,
                :mfe_20d_atr, :mae_20d_atr, :max_price_40d, :min_price_40d,
                :hit_plus_1atr, :hit_plus_2atr, :hit_plus_3atr,
                :hit_minus_1atr, :hit_minus_1_5atr, :stop_hit, :trailing_activation_hit,
                :outcome_status
            )
            ON CONFLICT(journal_id, outcome_type) DO UPDATE SET
                trading_days_evaluated = excluded.trading_days_evaluated,
                return_5d = excluded.return_5d,
                return_10d = excluded.return_10d,
                return_20d = excluded.return_20d,
                return_40d = excluded.return_40d,
                mfe_5d = excluded.mfe_5d,
                mae_5d = excluded.mae_5d,
                mfe_10d = excluded.mfe_10d,
                mae_10d = excluded.mae_10d,
                mfe_20d = excluded.mfe_20d,
                mae_20d = excluded.mae_20d,
                mfe_40d = excluded.mfe_40d,
                mae_40d = excluded.mae_40d,
                mfe_20d_atr = excluded.mfe_20d_atr,
                mae_20d_atr = excluded.mae_20d_atr,
                max_price_40d = excluded.max_price_40d,
                min_price_40d = excluded.min_price_40d,
                hit_plus_1atr = excluded.hit_plus_1atr,
                hit_plus_2atr = excluded.hit_plus_2atr,
                hit_plus_3atr = excluded.hit_plus_3atr,
                hit_minus_1atr = excluded.hit_minus_1atr,
                hit_minus_1_5atr = excluded.hit_minus_1_5atr,
                stop_hit = excluded.stop_hit,
                trailing_activation_hit = excluded.trailing_activation_hit,
                outcome_status = excluded.outcome_status,
                updated_at = CURRENT_TIMESTAMP
        """
        params = {
            "journal_id": outcome["journal_id"],
            "outcome_type": outcome.get("outcome_type", "SHADOW_OUTCOME"),
            "entry_reference_price": float(outcome["entry_reference_price"]),
            "entry_atr14": float(outcome["entry_atr14"]),
            "trading_days_evaluated": int(outcome.get("trading_days_evaluated", 0)),
            "return_5d": outcome.get("return_5d"),
            "return_10d": outcome.get("return_10d"),
            "return_20d": outcome.get("return_20d"),
            "return_40d": outcome.get("return_40d"),
            "mfe_5d": outcome.get("mfe_5d"),
            "mae_5d": outcome.get("mae_5d"),
            "mfe_10d": outcome.get("mfe_10d"),
            "mae_10d": outcome.get("mae_10d"),
            "mfe_20d": outcome.get("mfe_20d"),
            "mae_20d": outcome.get("mae_20d"),
            "mfe_40d": outcome.get("mfe_40d"),
            "mae_40d": outcome.get("mae_40d"),
            "mfe_20d_atr": outcome.get("mfe_20d_atr"),
            "mae_20d_atr": outcome.get("mae_20d_atr"),
            "max_price_40d": outcome.get("max_price_40d"),
            "min_price_40d": outcome.get("min_price_40d"),
            "hit_plus_1atr": 1 if outcome.get("hit_plus_1atr") else 0,
            "hit_plus_2atr": 1 if outcome.get("hit_plus_2atr") else 0,
            "hit_plus_3atr": 1 if outcome.get("hit_plus_3atr") else 0,
            "hit_minus_1atr": 1 if outcome.get("hit_minus_1atr") else 0,
            "hit_minus_1_5atr": 1 if outcome.get("hit_minus_1_5atr") else 0,
            "stop_hit": 1 if outcome.get("stop_hit") else 0,
            "trailing_activation_hit": 1 if outcome.get("trailing_activation_hit") else 0,
            "outcome_status": outcome.get("outcome_status", "PENDING")
        }
        res = self.execute_non_query(query, params)
        return (res is not None and res > 0)

    def get_signal_outcome(self, journal_id: str, outcome_type: str = "SHADOW_OUTCOME") -> Optional[Dict[str, Any]]:
        """특정 journal_id의 outcome 조회"""
        query = "SELECT * FROM signal_outcomes WHERE journal_id = ? AND outcome_type = ? LIMIT 1"
        rows = self.execute_query(query, (journal_id, outcome_type))
        return dict(rows[0]) if rows else None

    def get_all_signal_outcomes_with_journal(self) -> List[Dict[str, Any]]:
        """Journal과 Outcome 결합 데이터 조회 (Attribution 분석용)"""
        query = """
            SELECT j.*, o.outcome_type, o.trading_days_evaluated,
                   o.return_5d, o.return_10d, o.return_20d, o.return_40d,
                   o.mfe_5d, o.mae_5d, o.mfe_10d, o.mae_10d, o.mfe_20d, o.mae_20d, o.mfe_40d, o.mae_40d,
                   o.mfe_20d_atr, o.mae_20d_atr,
                   o.hit_plus_1atr, o.hit_plus_2atr, o.hit_plus_3atr,
                   o.hit_minus_1atr, o.hit_minus_1_5atr, o.stop_hit, o.trailing_activation_hit,
                   o.outcome_status
            FROM scan_journal j
            JOIN signal_outcomes o ON j.journal_id = o.journal_id
            ORDER BY j.scan_timestamp ASC
        """
        rows = self.execute_query(query)
        results = []
        for r in (rows or []):
            d = dict(r)
            if d.get("all_blockers"):
                try:
                    d["all_blockers"] = json.loads(d["all_blockers"])
                except Exception:
                    pass
            results.append(d)
        return results

    # ==========================================
    # Phase 8: Runtime Scheduler Operations
    # ==========================================

    def insert_scheduler_run(self, run: Dict[str, Any]) -> bool:
        """스케줄러 실행 기록 생성"""
        query = """
            INSERT INTO scheduler_runs (
                run_id, scheduled_time, actual_start_time, actual_end_time,
                trading_date, task_type, status,
                stocks_scanned, journals_created, signal_changes,
                last_completed_45m_bar, error_code, error_message
            ) VALUES (
                :run_id, :scheduled_time, :actual_start_time, :actual_end_time,
                :trading_date, :task_type, :status,
                :stocks_scanned, :journals_created, :signal_changes,
                :last_completed_45m_bar, :error_code, :error_message
            )
        """
        params = {
            "run_id": run["run_id"],
            "scheduled_time": run.get("scheduled_time", ""),
            "actual_start_time": run.get("actual_start_time", ""),
            "actual_end_time": run.get("actual_end_time"),
            "trading_date": run.get("trading_date", ""),
            "task_type": run.get("task_type", "INTRADAY_SHADOW_SCAN"),
            "status": run.get("status", "STARTED"),
            "stocks_scanned": int(run.get("stocks_scanned", 0)),
            "journals_created": int(run.get("journals_created", 0)),
            "signal_changes": int(run.get("signal_changes", 0)),
            "last_completed_45m_bar": run.get("last_completed_45m_bar"),
            "error_code": run.get("error_code"),
            "error_message": run.get("error_message")
        }
        res = self.execute_non_query(query, params)
        return (res is not None and res > 0)

    def update_scheduler_run(self, run_id: str, updates: Dict[str, Any]) -> bool:
        """스케줄러 실행 완료/종료 상태 갱신"""
        set_clauses = []
        params = {"run_id": run_id}
        for k, v in updates.items():
            set_clauses.append(f"{k} = :{k}")
            params[k] = v

        if not set_clauses:
            return True

        query = f"UPDATE scheduler_runs SET {', '.join(set_clauses)} WHERE run_id = :run_id"
        res = self.execute_non_query(query, params)
        return (res is not None and res > 0)

    def get_latest_scheduler_run(self, task_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """가장 최근 스케줄러 실행 기록 조회"""
        if task_type:
            query = "SELECT * FROM scheduler_runs WHERE task_type = ? ORDER BY id DESC LIMIT 1"
            rows = self.execute_query(query, (task_type,))
        else:
            query = "SELECT * FROM scheduler_runs ORDER BY id DESC LIMIT 1"
            rows = self.execute_query(query)
        return dict(rows[0]) if rows else None

    def get_today_scheduler_runs(self, trading_date: str) -> List[Dict[str, Any]]:
        """당일 스케줄러 실행 기록 목록 조회"""
        query = "SELECT * FROM scheduler_runs WHERE trading_date = ? ORDER BY id ASC"
        rows = self.execute_query(query, (trading_date,))
        return [dict(r) for r in rows] if rows else []

    def acquire_scheduler_lock(self, lock_name: str, task_name: str, pid: int, ttl_seconds: int = 600) -> bool:
        """
        스케줄러 프로세스 Lock 획득 (Stale lock 자동 청소 포함)
        """
        from datetime import datetime, timedelta
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        expires_at = (now + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. 만료된 stale lock 삭제
        clean_query = "DELETE FROM scheduler_locks WHERE lock_name = ? AND expires_at < ?"
        self.execute_non_query(clean_query, (lock_name, now_str))

        # 2. Lock 등록 시도
        insert_query = """
            INSERT INTO scheduler_locks (lock_name, task_name, pid, lock_created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """
        res = self.execute_non_query(insert_query, (lock_name, task_name, pid, now_str, expires_at))
        return (res is not None and res > 0)

    def release_scheduler_lock(self, lock_name: str, pid: Optional[int] = None) -> bool:
        """스케줄러 Lock 해제"""
        if pid is not None:
            query = "DELETE FROM scheduler_locks WHERE lock_name = ? AND pid = ?"
            res = self.execute_non_query(query, (lock_name, pid))
        else:
            query = "DELETE FROM scheduler_locks WHERE lock_name = ?"
            res = self.execute_non_query(query, (lock_name,))
        return (res is not None and res > 0)

    def cleanup_stale_scheduler_locks(self, ttl_seconds: int = 600) -> int:
        """만료된 Lock 일괄 청소"""
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        query = "DELETE FROM scheduler_locks WHERE expires_at < ?"
        res = self.execute_non_query(query, (now_str,))
        return res or 0

    def get_latest_scan_journal_for_stock(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """특정 종목의 가장 최근 Scan Journal 스냅샷 조회 (상태 변경 감지용)"""
        query = "SELECT * FROM scan_journal WHERE stock_code = ? ORDER BY id DESC LIMIT 1"
        rows = self.execute_query(query, (stock_code,))
        if not rows:
            return None
        d = dict(rows[0])
        if d.get("all_blockers"):
            try:
                d["all_blockers"] = json.loads(d["all_blockers"])
            except Exception:
                pass
        return d


