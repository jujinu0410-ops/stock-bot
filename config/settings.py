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
            FOREIGN KEY (stock_code) REFERENCES stock_info(stock_code)
        );
    """
}

# INDEX DEFINITIONS FOR SPEED OPTIMIZATION
INDEX_SCHEMAS = [
    "CREATE INDEX IF NOT EXISTS idx_kiwoom_daily_code_date ON kiwoom_daily(stock_code, stk_date);",
    "CREATE INDEX IF NOT EXISTS idx_dart_financials_code ON dart_financials(stock_code);",
    "CREATE INDEX IF NOT EXISTS idx_trading_signals_code_date ON trading_signals(stock_code, analysis_date);"
]
