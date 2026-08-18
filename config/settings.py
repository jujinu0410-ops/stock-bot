import os
from pathlib import Path

# BASE DIRECTORY
BASE_DIR = Path(__file__).resolve().parent.parent

# DATA & LOG DIRECTORIES
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# DATABASE PATH
DB_PATH = DATA_DIR / "stock_system.db"

# LOGGING PATH
LOG_FILE_PATH = LOG_DIR / "stock_system.log"

# ENV FILE LOADER (C:/Users/jooji/.env 또는 로컬 .env)
def load_env_vars(env_path: str = "C:/Users/jooji/.env") -> dict:
    config = {}
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    config[k.strip()] = v.strip().strip('"').strip("'")
    return config

_env_vars = load_env_vars()

# GMAIL CONFIG
GMAIL_USER = os.getenv("GMAIL_USER") or os.getenv("GMAIL_SENDER_EMAIL") or _env_vars.get("GMAIL_USER") or _env_vars.get("USER_EMAIL")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD") or _env_vars.get("GMAIL_APP_PASSWORD") or _env_vars.get("GMAIL_PASSWORD")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_GMAIL") or os.getenv("GMAIL_SENDER_EMAIL") or os.getenv("GMAIL_USER") or _env_vars.get("RECIPIENT_GMAIL") or GMAIL_USER

# EMAIL RENDERER CONFIG (V1: 기본 매트릭스 표, V2: 모바일 최적화 반응형 카드 UI)
_raw_render_ver = (os.getenv("EMAIL_RENDER_VERSION") or _env_vars.get("EMAIL_RENDER_VERSION") or "V1").strip().upper()
if _raw_render_ver not in ("V1", "V2"):
    EMAIL_RENDER_VERSION = "V1"
else:
    EMAIL_RENDER_VERSION = _raw_render_ver

# DART API CONFIG
DART_API_KEY = os.getenv("DART_API_KEY") or os.getenv("OPENDART_API_KEY") or _env_vars.get("DART_API_KEY") or _env_vars.get("OPENDART_API_KEY") or "YOUR_DART_API_KEY_HERE"

# KIWOOM REST TEST API CONFIG
KIWOOM_APP_KEY = os.getenv("KIWOOM_APP_KEY") or _env_vars.get("KIWOOM_APP_KEY") or "YOUR_KIWOOM_APP_KEY_HERE"
KIWOOM_APP_SECRET = os.getenv("KIWOOM_APP_SECRET") or _env_vars.get("KIWOOM_APP_SECRET") or "YOUR_KIWOOM_APP_SECRET_HERE"
KIWOOM_ACCOUNT_NO = os.getenv("KIWOOM_ACCOUNT_NO") or _env_vars.get("KIWOOM_ACCOUNT_NO") or "3097-8228"

# 실제 키움 API 키가 존재하면 Mock 모드를 자동 해제
_has_kiwoom_key = bool(KIWOOM_APP_KEY and KIWOOM_APP_KEY != "YOUR_KIWOOM_APP_KEY_HERE")
KIWOOM_USE_MOCK = os.getenv("KIWOOM_USE_MOCK", "False" if _has_kiwoom_key else "True").lower() in ("true", "1", "t")

# ATR RISK ENGINE V4-PILOT-C GLOBAL CONFIGURATION
ATR_ENGINE_VERSION = "V4-PILOT-C"

ATR_CONFIG = {
    "parameter_version": "V4-PILOT-C",
    "atr_period": 14,
    "atr_method": "WILDER",
    "atr_timeframe": "1D_COMPLETED",

    # 추매 감시 및 반등 실행 배수
    "buy_watch_multiple": 1.5,
    "buy_rebound_multiple": 0.5,

    # 손절 및 트레일링 손절 배수 (운영 초기손절 1.5 ATR 고정)
    "initial_stop_multiple": 1.5,
    "trailing_stop_multiple": 1.5,

    # 익절 트레일링 활성 및 하락 실행폭
    "profit_activation_multiple": 3.0,
    "normal_profit_trail_multiple": 0.8,

    # 손실 축소 및 비상 모드 트레일링폭
    "recovery_profit_activation_multiple": 1.2,
    "recovery_trail_multiple": 0.3,
    "emergency_profit_activation_multiple": 1.0,
    "emergency_trail_multiple": 0.15,

    # 계좌 위험 관리 및 비중 한도
    "default_account_risk_pct": 0.005,  # 일반 종목 0.5%
    "max_account_risk_pct": 0.0075,     # 최상위 확정 종목 0.75%
    "max_position_weight_pct": 20.0,    # 단일 종목 최대 비중 20%
    "max_portfolio_open_risk_pct": 0.05,# 전체 포트폴리오 활성 위험 5%

    # 데이터 이상 및 변동성 필터링
    "natr_order_block_threshold_pct": 20.0,
    "atr_spike_median_multiple": 2.0
}

# SQL TABLE SCHEMA DEFINITIONS
TABLE_SCHEMAS = {
    "stock_info": """
        CREATE TABLE IF NOT EXISTS stock_info (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT NOT NULL,
            market_type TEXT,
            sector TEXT,
            market_cap INTEGER,
            floating_shares INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "dart_financials": """
        CREATE TABLE IF NOT EXISTS dart_financials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            fiscal_year INTEGER NOT NULL,
            quarter_code TEXT NOT NULL,
            revenue REAL,
            operating_profit REAL,
            net_income REAL,
            operating_cash_flow REAL,
            debt_ratio REAL,
            revenue_yoy REAL,
            op_profit_yoy REAL,
            order_backlog REAL,
            data_completeness REAL,
            f_score_confirmed INTEGER DEFAULT 1,
            sanity_reason TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_code) REFERENCES stock_info(stock_code),
            UNIQUE(stock_code, fiscal_year, quarter_code)
        );
    """,
    "kiwoom_daily": """
        CREATE TABLE IF NOT EXISTS kiwoom_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stk_date TEXT NOT NULL,
            open_price INTEGER NOT NULL,
            high_price INTEGER NOT NULL,
            low_price INTEGER NOT NULL,
            close_price INTEGER NOT NULL,
            volume INTEGER NOT NULL,
            foreign_net_buy INTEGER DEFAULT 0,
            inst_net_buy INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_code) REFERENCES stock_info(stock_code),
            UNIQUE(stock_code, stk_date)
        );
    """,
    "trading_signals": """
        CREATE TABLE IF NOT EXISTS trading_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            analysis_date TEXT NOT NULL,
            f_score REAL,
            t_score_raw REAL,
            t_score_converted REAL,
            score_stage1 REAL,
            score_stage2 REAL,
            score_stage3 REAL,
            position_stage INTEGER DEFAULT 0,
            signal_type TEXT,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_code) REFERENCES stock_info(stock_code),
            UNIQUE(stock_code, analysis_date)
        );
    """,
    "portfolio_positions": """
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            stock_code TEXT PRIMARY KEY,
            quantity INTEGER DEFAULT 0,
            avg_buy_price REAL DEFAULT 0,
            total_invested REAL DEFAULT 0,
            max_allowed_loss REAL DEFAULT 0,
            stop_loss_price REAL DEFAULT 0,
            target_profit_price REAL DEFAULT 0,
            highest_close_price REAL DEFAULT 0,
            confirmed_stop_price REAL DEFAULT 0,
            monitoring_start_date TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            position_cycle_id TEXT,
            parameter_version TEXT DEFAULT 'V4-PILOT-C',
            trade_mode TEXT DEFAULT 'NORMAL',
            mode_override TEXT,
            entry_stage INTEGER DEFAULT 1,
            lifecycle_status TEXT DEFAULT 'POSITION_OPEN',
            anchor_price_p0 REAL DEFAULT 0,
            anchor_atr_a0 REAL DEFAULT 0,
            anchor_created_at TEXT,
            atr_method TEXT DEFAULT 'WILDER',
            atr_timeframe TEXT DEFAULT '1D_COMPLETED',
            current_completed_atr REAL DEFAULT 0,
            natr_pct REAL DEFAULT 0,
            initial_stop REAL DEFAULT 0,
            profit_progress_1atr_reached INTEGER DEFAULT 0,
            highest_close REAL DEFAULT 0,
            highest_intraday REAL DEFAULT 0,
            previous_confirmed_stop REAL DEFAULT 0,
            ratchet_stop REAL DEFAULT 0,
            profit_activation_raw REAL DEFAULT 0,
            profit_activation_effective REAL DEFAULT 0,
            profit_activation_status TEXT DEFAULT 'INACTIVE',
            highest_after_activation REAL DEFAULT 0,
            previous_profit_trail REAL DEFAULT 0,
            profit_trail REAL DEFAULT 0,
            effective_exit_line REAL DEFAULT 0,
            account_risk_pct REAL DEFAULT 0.005,
            risk_budget_amount REAL DEFAULT 0,
            risk_per_share REAL DEFAULT 0,
            recommended_quantity INTEGER DEFAULT 0,
            slippage_buffer REAL DEFAULT 0,
            data_validity_flag INTEGER DEFAULT 1,
            data_hold_reason TEXT,
            reanchor_flag INTEGER DEFAULT 0,
            FOREIGN KEY (stock_code) REFERENCES stock_info(stock_code)
        );
    """,
    "position_lots": """
        CREATE TABLE IF NOT EXISTS position_lots (
            lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_cycle_id TEXT,
            stock_code TEXT NOT NULL,
            buy_datetime TEXT DEFAULT CURRENT_TIMESTAMP,
            quantity INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            lot_anchor_atr REAL DEFAULT 0,
            lot_initial_stop REAL DEFAULT 0,
            source TEXT DEFAULT 'MANUAL',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_code) REFERENCES stock_info(stock_code)
        );
    """,
    "dispatch_history": """
        CREATE TABLE IF NOT EXISTS dispatch_history (
            dispatch_id TEXT PRIMARY KEY,
            dispatch_type TEXT,
            sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
            recipient TEXT,
            subject TEXT
        );
    """,
    "quarterly_financials": """
        CREATE TABLE IF NOT EXISTS quarterly_financials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter TEXT NOT NULL,
            fiscal_period_end TEXT NOT NULL,
            fs_div TEXT NOT NULL,
            
            -- FLOW Accounts (Discrete Quarter)
            revenue REAL DEFAULT 0.0,
            operating_income REAL DEFAULT 0.0,
            operating_margin REAL DEFAULT 0.0,
            net_income REAL DEFAULT 0.0,
            operating_cash_flow REAL DEFAULT 0.0,
            
            -- STOCK Accounts (End of Period Balance)
            total_assets REAL DEFAULT 0.0,
            total_liabilities REAL DEFAULT 0.0,
            total_equity REAL DEFAULT 0.0,
            inventory REAL DEFAULT 0.0,
            accounts_receivable REAL DEFAULT 0.0,
            cash_and_equivalents REAL DEFAULT 0.0,
            interest_bearing_debt REAL DEFAULT 0.0,
            net_debt REAL DEFAULT 0.0,
            debt_ratio REAL DEFAULT 0.0,
            
            -- Optional Accounts
            capex REAL,
            r_and_d REAL,
            
            -- Metadata & Provenance
            rcept_no TEXT,
            rcept_date TEXT,
            report_code TEXT,
            is_amended INTEGER DEFAULT 0,
            source_quality TEXT DEFAULT 'VALID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_code) REFERENCES stock_info(stock_code),
            UNIQUE(stock_code, fiscal_year, fiscal_quarter, fs_div)
        );
    """,
    "disclosure_events": """
        CREATE TABLE IF NOT EXISTS disclosure_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            event_type TEXT NOT NULL,
            rcept_no TEXT NOT NULL,
            rcept_date TEXT NOT NULL,
            report_name TEXT NOT NULL,
            amount REAL,
            currency TEXT DEFAULT 'KRW',
            counterparty TEXT,
            contract_start TEXT,
            contract_end TEXT,
            revenue_ratio REAL,
            progression_stage TEXT DEFAULT 'FINAL_CONTRACT',
            entity_scope TEXT DEFAULT 'PARENT',
            subsidiary_name TEXT,
            original_rcept_no TEXT,
            amendment_chain_id TEXT,
            is_latest_version INTEGER DEFAULT 1,
            event_hazard TEXT DEFAULT 'LOW',
            materiality_level TEXT DEFAULT 'UNKNOWN',
            effective_severity TEXT DEFAULT 'LOW',
            materiality_ratio_revenue REAL,
            materiality_ratio_backlog REAL,
            materiality_status TEXT DEFAULT 'MATERIALITY_UNKNOWN',
            issue_amount REAL,
            new_shares REAL,
            existing_shares REAL,
            dilution_ratio REAL,
            ratio_to_market_cap REAL,
            severity TEXT DEFAULT 'LOW',
            severity_reason TEXT,
            is_negative_event INTEGER DEFAULT 0,
            source_quality TEXT DEFAULT 'VALID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_code) REFERENCES stock_info(stock_code),
            UNIQUE(stock_code, rcept_no)
        );
    """,
    "order_backlog_metrics": """
        CREATE TABLE IF NOT EXISTS order_backlog_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter TEXT NOT NULL,
            order_backlog REAL,
            new_orders REAL,
            provisional_new_orders REAL,
            is_unadjusted_bridge INTEGER DEFAULT 0,
            order_backlog_yoy REAL,
            order_backlog_to_revenue REAL,
            book_to_bill REAL,
            book_to_bill_period TEXT,
            book_to_bill_status TEXT DEFAULT 'PROVISIONAL',
            numerator_orders REAL,
            numerator_period_start TEXT,
            numerator_period_end TEXT,
            numerator_basis TEXT,
            denominator_revenue REAL,
            denominator_period_start TEXT,
            denominator_period_end TEXT,
            denominator_basis TEXT,
            scope_id TEXT DEFAULT 'CONSOLIDATED',
            scope_description TEXT,
            confidence_level TEXT DEFAULT 'MEDIUM',
            opportunity_confidence TEXT DEFAULT 'MEDIUM',
            beginning_backlog REAL,
            ending_backlog REAL,
            recognized_revenue REAL,
            cancellations_adj TEXT DEFAULT 'UNKNOWN',
            fx_adj TEXT DEFAULT 'UNKNOWN',
            scope_adj TEXT DEFAULT 'UNKNOWN',
            source_type TEXT DEFAULT 'COMPANY_REPORTED',
            disclosure_status TEXT DEFAULT 'DISCLOSED',
            source_quality TEXT DEFAULT 'VALID',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_code) REFERENCES stock_info(stock_code),
            UNIQUE(stock_code, fiscal_year, fiscal_quarter)
        );
    """,
    "industry_runs": """
        CREATE TABLE IF NOT EXISTS industry_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE NOT NULL,
            run_date TEXT NOT NULL,
            run_mode TEXT NOT NULL,
            run_type TEXT NOT NULL DEFAULT 'PRODUCTION',
            as_of_date TEXT NOT NULL,
            evidence_cutoff TEXT NOT NULL,
            source_count INTEGER DEFAULT 0,
            verified_evidence_count INTEGER DEFAULT 0,
            unverified_evidence_count INTEGER DEFAULT 0,
            synthetic_evidence_count INTEGER DEFAULT 0,
            replay_verified_count INTEGER DEFAULT 0,
            replay_failed_count INTEGER DEFAULT 0,
            replay_not_possible_count INTEGER DEFAULT 0,
            evidence_quality_score REAL DEFAULT 0.0,
            data_quality TEXT DEFAULT 'VALID',
            run_status TEXT DEFAULT 'COMPLETED',
            qa_status TEXT DEFAULT 'QA_PASSED',
            scoring_version TEXT DEFAULT 'V1.0',
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "industry_scores": """
        CREATE TABLE IF NOT EXISTS industry_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            industry_id TEXT NOT NULL,
            industry_name TEXT NOT NULL,
            total_score REAL NOT NULL,
            policy_continuity REAL NOT NULL,
            earnings_linkage REAL NOT NULL,
            visibility_order_revenue REAL NOT NULL,
            catalysts_score REAL NOT NULL,
            valuation_burden REAL NOT NULL,
            downside_risk_score REAL NOT NULL,
            industry_bucket TEXT NOT NULL,
            industry_gate TEXT NOT NULL,
            industry_confidence TEXT NOT NULL,
            positive_evidence TEXT,
            negative_evidence TEXT,
            catalysts_6_18m TEXT,
            downside_risks TEXT,
            thesis TEXT,
            live_fetched_pct REAL DEFAULT 0.0,
            reference_verified_pct REAL DEFAULT 0.0,
            internal_derived_pct REAL DEFAULT 0.0,
            manual_evidence_pct REAL DEFAULT 0.0,
            synthetic_evidence_pct REAL DEFAULT 0.0,
            fresh_evidence_pct REAL DEFAULT 100.0,
            replay_verified_pct REAL DEFAULT 100.0,
            replay_failed_pct REAL DEFAULT 0.0,
            driver_count INTEGER DEFAULT 0,
            evidence_count INTEGER DEFAULT 0,
            qa_status TEXT DEFAULT 'QA_PASSED',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES industry_runs(run_id),
            UNIQUE(run_id, industry_id)
        );
    """,
    "industry_evidence": """
        CREATE TABLE IF NOT EXISTS industry_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            industry_id TEXT NOT NULL,
            factor_name TEXT NOT NULL,
            evidence_type TEXT DEFAULT 'METRIC',
            evidence_family TEXT NOT NULL DEFAULT 'GENERAL',
            underlying_driver_id TEXT NOT NULL DEFAULT 'DRV_GEN',
            origin_type TEXT NOT NULL DEFAULT 'LIVE_FETCHED',
            collector_name TEXT NOT NULL DEFAULT 'SYSTEM_COLLECTOR',
            fetch_method TEXT DEFAULT 'HTTP_REST_API',
            http_status TEXT DEFAULT '200',
            parser_name TEXT DEFAULT 'DefaultParser',
            parser_version TEXT DEFAULT 'V1.0',
            source_type TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_reference TEXT,
            source_document_id TEXT NOT NULL DEFAULT 'DOC_001',
            source_published_at TEXT NOT NULL DEFAULT '2026-08-17',
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            evidence_date TEXT NOT NULL,
            collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            raw_value TEXT,
            extracted_fact_json TEXT,
            raw_payload_hash TEXT,
            normalization_rule TEXT,
            transformation_version TEXT DEFAULT 'NORM_V1.0',
            normalized_value REAL NOT NULL,
            driver_contribution_cap REAL DEFAULT 10.0,
            is_verified INTEGER DEFAULT 1,
            replay_status TEXT DEFAULT 'REPLAY_VERIFIED',
            evidence_direction TEXT NOT NULL,
            reliability TEXT NOT NULL,
            freshness_days INTEGER,
            rationale TEXT,
            FOREIGN KEY (run_id) REFERENCES industry_runs(run_id),
            UNIQUE(run_id, industry_id, factor_name, source_document_id)
        );
    """,
    "industry_company_map": """
        CREATE TABLE IF NOT EXISTS industry_company_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            industry_id TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            exposure_type TEXT NOT NULL,
            evidence_rationale TEXT,
            is_eligible_candidate INTEGER DEFAULT 1,
            valid_from TEXT DEFAULT '2026-01-01',
            valid_to TEXT DEFAULT '9999-12-31',
            mapping_version TEXT DEFAULT 'V1.0',
            is_active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(industry_id, stock_code)
        );
    """,
    "scan_journal": """
        CREATE TABLE IF NOT EXISTS scan_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journal_id TEXT UNIQUE NOT NULL,
            scan_timestamp TEXT NOT NULL,
            trading_date TEXT NOT NULL,
            snapshot_reason TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            market_price REAL NOT NULL,
            industry_run_id TEXT,
            industry_score REAL,
            industry_gate TEXT,
            industry_confidence TEXT,
            exposure_type TEXT,
            mapping_version TEXT DEFAULT 'V1.0',
            fundamental_state TEXT,
            turnaround_type TEXT,
            turnaround_label TEXT,
            forward_opportunity TEXT,
            forward_confidence TEXT,
            forward_risk TEXT,
            forward_risk_override_tag TEXT,
            book_to_bill_summary TEXT,
            t_score REAL,
            technical_state TEXT,
            technical_action TEXT,
            intraday_data_quality TEXT,
            atr14 REAL,
            natr REAL,
            candidate_ref_price REAL,
            candidate_stop_price REAL,
            candidate_target_price REAL,
            atr_mode TEXT,
            shadow_integrated_state TEXT,
            primary_blocker TEXT,
            all_blockers TEXT,
            existing_f_score REAL,
            existing_final_score REAL,
            buy_approval TEXT,
            p0_status TEXT,
            position_cycle_id TEXT,
            financial_data_asof TEXT,
            forward_data_asof TEXT,
            intraday_last_timestamp TEXT,
            scoring_versions TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "signal_outcomes": """
        CREATE TABLE IF NOT EXISTS signal_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journal_id TEXT NOT NULL,
            outcome_type TEXT DEFAULT 'SHADOW_OUTCOME',
            entry_reference_price REAL NOT NULL,
            entry_atr14 REAL NOT NULL,
            trading_days_evaluated INTEGER DEFAULT 0,
            return_5d REAL,
            return_10d REAL,
            return_20d REAL,
            return_40d REAL,
            mfe_5d REAL,
            mae_5d REAL,
            mfe_10d REAL,
            mae_10d REAL,
            mfe_20d REAL,
            mae_20d REAL,
            mfe_40d REAL,
            mae_40d REAL,
            mfe_20d_atr REAL,
            mae_20d_atr REAL,
            max_price_40d REAL,
            min_price_40d REAL,
            hit_plus_1atr INTEGER DEFAULT 0,
            hit_plus_2atr INTEGER DEFAULT 0,
            hit_plus_3atr INTEGER DEFAULT 0,
            hit_minus_1atr INTEGER DEFAULT 0,
            hit_minus_1_5atr INTEGER DEFAULT 0,
            stop_hit INTEGER DEFAULT 0,
            trailing_activation_hit INTEGER DEFAULT 0,
            outcome_status TEXT DEFAULT 'PENDING',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (journal_id) REFERENCES scan_journal(journal_id),
            UNIQUE(journal_id, outcome_type)
        );
    """,
    "scheduler_runs": """
        CREATE TABLE IF NOT EXISTS scheduler_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE NOT NULL,
            scheduled_time TEXT NOT NULL,
            actual_start_time TEXT NOT NULL,
            actual_end_time TEXT,
            trading_date TEXT NOT NULL,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL,
            stocks_scanned INTEGER DEFAULT 0,
            journals_created INTEGER DEFAULT 0,
            signal_changes INTEGER DEFAULT 0,
            last_completed_45m_bar TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "scheduler_locks": """
        CREATE TABLE IF NOT EXISTS scheduler_locks (
            lock_name TEXT PRIMARY KEY,
            task_name TEXT NOT NULL,
            pid INTEGER NOT NULL,
            lock_created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
    """
}

# INDEX DEFINITIONS FOR SPEED OPTIMIZATION
INDEX_SCHEMAS = [
    "CREATE INDEX IF NOT EXISTS idx_kiwoom_daily_code_date ON kiwoom_daily(stock_code, stk_date);",
    "CREATE INDEX IF NOT EXISTS idx_dart_financials_code ON dart_financials(stock_code);",
    "CREATE INDEX IF NOT EXISTS idx_trading_signals_code_date ON trading_signals(stock_code, analysis_date);",
    "CREATE INDEX IF NOT EXISTS idx_position_lots_cycle ON position_lots(position_cycle_id, stock_code);",
    "CREATE INDEX IF NOT EXISTS idx_quarterly_financials_code_period ON quarterly_financials(stock_code, fiscal_year, fiscal_quarter);",
    "CREATE INDEX IF NOT EXISTS idx_disc_events_code_date ON disclosure_events(stock_code, rcept_date);",
    "CREATE INDEX IF NOT EXISTS idx_order_backlog_code_quarter ON order_backlog_metrics(stock_code, fiscal_year, fiscal_quarter);",
    "CREATE INDEX IF NOT EXISTS idx_industry_scores_run_ind ON industry_scores(run_id, industry_id);",
    "CREATE INDEX IF NOT EXISTS idx_industry_evidence_run_ind ON industry_evidence(run_id, industry_id, factor_name);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_industry_evidence_run_ind_fact_doc ON industry_evidence(run_id, industry_id, factor_name, source_document_id);",
    "CREATE INDEX IF NOT EXISTS idx_industry_company_map_code ON industry_company_map(stock_code);",
    "CREATE INDEX IF NOT EXISTS idx_scan_journal_code_date ON scan_journal(stock_code, trading_date);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_scan_journal_id ON scan_journal(journal_id);",
    "CREATE INDEX IF NOT EXISTS idx_signal_outcomes_journal ON signal_outcomes(journal_id);",
    "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_date_task ON scheduler_runs(trading_date, task_type);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduler_runs_id ON scheduler_runs(run_id);"
]

