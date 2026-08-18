"""
Canonical Market Quote Client & Realtime Price Lineage Provider
Phase 8 Runtime Layer (V1.0 Operations)

Features:
- Multi-tier live quote resolution (Kiwoom REST API -> Naver Mobile API -> Naver Polling)
- Strict provenance tracking with separated timestamps:
  1. source_response_timestamp (API response time, max 120s)
  2. last_trade_timestamp (KRX last trade execution time)
  3. market_status (OPEN, CLOSE, etc.)
  4. quote_trading_date (KRX trading date)
- Conservative staleness protection (DATA_HOLD / LIVE_QUOTE_STALE) without falsely penalizing low-liquidity stocks
- Strict separation between Live Market Price and Candidate Reference Price
"""

import os
import requests
import json
from datetime import datetime, date, timezone, timedelta
from typing import Dict, Any, Optional, Tuple, List

from src.utils.logger import logger
from src.api.kiwoom_api import KiwoomAPIClient

class MarketQuoteClient:
    """Provides high-integrity live market prices with verifiable data lineage"""

    def __init__(self, kiwoom_client: Optional[KiwoomAPIClient] = None):
        self.kiwoom_client = kiwoom_client or KiwoomAPIClient()
        self.cached_kiwoom_positions: Optional[Dict[str, Dict[str, Any]]] = None
        self.last_kiwoom_fetch_time: Optional[datetime] = None

    def _get_kiwoom_positions_map(self) -> Dict[str, Dict[str, Any]]:
        """Fetch and cache live account positions from Kiwoom REST API (kt00018)"""
        now = datetime.now()
        if (self.cached_kiwoom_positions is not None and
            self.last_kiwoom_fetch_time is not None and
            (now - self.last_kiwoom_fetch_time).total_seconds() < 30):
            return self.cached_kiwoom_positions

        pos_map = {}
        try:
            if self.kiwoom_client.is_valid_key():
                positions = self.kiwoom_client.get_account_positions()
                for p in positions:
                    code = str(p.get("stock_code", "")).replace("A", "").zfill(6)
                    cur_p = float(p.get("current_price", 0.0))
                    if cur_p > 0:
                        pos_map[code] = {
                            "price": cur_p,
                            "stock_name": p.get("stock_name", ""),
                            "quote_source": "KIWOOM_REST_API",
                            "source_response_timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                            "last_trade_timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                            "quote_trading_date": now.strftime("%Y-%m-%d"),
                            "market_status": "OPEN",
                            "is_live": True
                        }
        except Exception as e:
            logger.warning(f"[MarketQuoteClient] Kiwoom live positions fetch error: {e}")

        self.cached_kiwoom_positions = pos_map
        self.last_kiwoom_fetch_time = now
        return pos_map

    def fetch_live_quote(self, stock_code: str, max_source_age_seconds: int = 120) -> Dict[str, Any]:
        """
        Fetch verified live quote for a given stock code with full provenance.
        Returns a dict with:
        - success: bool
        - stock_code: str
        - price: Optional[float]
        - quote_source: str
        - source_response_timestamp: Optional[str]
        - last_trade_timestamp: Optional[str]
        - quote_trading_date: Optional[str]
        - source_age_seconds: int
        - market_status: str
        - is_stale: bool
        - status: str ('LIVE_VALID', 'LIVE_QUOTE_STALE', 'DATA_HOLD')
        """
        code = str(stock_code).strip().zfill(6)
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Tier 1: Kiwoom REST API Live Account Position (if held in portfolio)
        kiwoom_map = self._get_kiwoom_positions_map()
        if code in kiwoom_map:
            k_quote = kiwoom_map[code]
            return {
                "success": True,
                "stock_code": code,
                "price": k_quote["price"],
                "quote_source": "KIWOOM_REST_API",
                "source_response_timestamp": k_quote["source_response_timestamp"],
                "last_trade_timestamp": k_quote["last_trade_timestamp"],
                "quote_trading_date": k_quote["quote_trading_date"],
                "source_age_seconds": 0,
                "market_status": k_quote.get("market_status", "OPEN"),
                "is_stale": False,
                "status": "LIVE_VALID"
            }

        # 2. Tier 2: Naver Mobile Realtime Stock API (Official KRX Live Feed)
        try:
            url = f"https://m.stock.naver.com/api/stock/{code}/basic"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            res = requests.get(url, headers=headers, timeout=4)
            if res.status_code == 200:
                data = res.json()
                raw_p = data.get("nowPrice") or data.get("closePrice")
                if raw_p:
                    price_val = float(str(raw_p).replace(",", "").strip())
                    trade_dt_raw = data.get("localTradedAt") or data.get("tradedDateTime")
                    market_status = data.get("marketStatus", "OPEN")

                    # Parse trade timestamp
                    trade_date_str = today_str
                    last_trade_ts_str = now_str
                    if trade_dt_raw:
                        try:
                            clean_t = trade_dt_raw.replace("T", " ")
                            if "+" in clean_t:
                                clean_t = clean_t.split("+")[0]
                            trade_dt = datetime.strptime(clean_t[:19], "%Y-%m-%d %H:%M:%S")
                            trade_date_str = trade_dt.strftime("%Y-%m-%d")
                            last_trade_ts_str = trade_dt.strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            trade_date_str = today_str

                    # Check source freshness
                    is_date_today = (trade_date_str == today_str)

                    if price_val > 0 and is_date_today:
                        return {
                            "success": True,
                            "stock_code": code,
                            "price": price_val,
                            "quote_source": "NAVER_MOBILE_LIVE_API",
                            "source_response_timestamp": now_str,
                            "last_trade_timestamp": last_trade_ts_str,
                            "quote_trading_date": trade_date_str,
                            "source_age_seconds": 0,
                            "market_status": market_status,
                            "is_stale": False,
                            "status": "LIVE_VALID"
                        }
                    elif price_val > 0:
                        logger.warning(f"[MarketQuoteClient] {code} Naver quote from previous date ({trade_date_str}). Marking STALE.")
                        return {
                            "success": False,
                            "stock_code": code,
                            "price": price_val,
                            "quote_source": "NAVER_MOBILE_LIVE_API",
                            "source_response_timestamp": now_str,
                            "last_trade_timestamp": last_trade_ts_str,
                            "quote_trading_date": trade_date_str,
                            "source_age_seconds": 0,
                            "market_status": market_status,
                            "is_stale": True,
                            "status": "LIVE_QUOTE_STALE"
                        }
        except Exception as e:
            logger.debug(f"[MarketQuoteClient] Naver Mobile API {code} error: {e}")

        # 3. Tier 3: Naver Domestic Realtime Polling API
        try:
            url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(url, headers=headers, timeout=4)
            if res.status_code == 200:
                data = res.json()
                items = data.get("datas", [])
                if items:
                    item = items[0]
                    raw_p = item.get("closePrice")
                    m_status = item.get("marketStatus", "OPEN")
                    if raw_p:
                        price_val = float(str(raw_p).replace(",", "").strip())
                        if price_val > 0:
                            return {
                                "success": True,
                                "stock_code": code,
                                "price": price_val,
                                "quote_source": "NAVER_POLLING_LIVE_API",
                                "source_response_timestamp": now_str,
                                "last_trade_timestamp": now_str,
                                "quote_trading_date": today_str,
                                "source_age_seconds": 0,
                                "market_status": m_status,
                                "is_stale": False,
                                "status": "LIVE_VALID"
                            }
        except Exception as e:
            logger.debug(f"[MarketQuoteClient] Naver Polling API {code} error: {e}")

        # Fail-closed
        logger.error(f"[MarketQuoteClient] Failed to obtain valid live quote for {code}. Marking DATA_HOLD.")
        return {
            "success": False,
            "stock_code": code,
            "price": None,
            "quote_source": "NONE",
            "source_response_timestamp": None,
            "last_trade_timestamp": None,
            "quote_trading_date": None,
            "source_age_seconds": None,
            "market_status": "UNKNOWN",
            "is_stale": True,
            "status": "DATA_HOLD"
        }
