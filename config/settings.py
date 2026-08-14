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

    # 손절 및 트레일링 손절 배수 (2단계 래칫)
    "initial_stop_multiple": 2.0,
    "profit_progress_threshold": 1.0,  # +1.0 ATR 진행 시 1.5 ATR 손절로 강화
    "trailing_stop_multiple": 1.5,

    # 익절 트레일링 활성 및 하락 실행폭
    "profit_activation_multiple": 3.0,
    "normal_profit_trail_multiple": 0.8,

    # 손실 축소 및 비상 모드 트레일링폭
    "recovery_trail_min": 0.2,
    "recovery_trail_max": 0.4,
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
    """
}

# INDEX DEFINITIONS FOR SPEED OPTIMIZATION
INDEX_SCHEMAS = [
    "CREATE INDEX IF NOT EXISTS idx_kiwoom_daily_code_date ON kiwoom_daily(stock_code, stk_date);",
    "CREATE INDEX IF NOT EXISTS idx_dart_financials_code ON dart_financials(stock_code);",
    "CREATE INDEX IF NOT EXISTS idx_trading_signals_code_date ON trading_signals(stock_code, analysis_date);",
    "CREATE INDEX IF NOT EXISTS idx_position_lots_cycle ON position_lots(position_cycle_id, stock_code);"
]
