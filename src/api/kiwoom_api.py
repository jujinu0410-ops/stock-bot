import requests
from typing import Dict, Any, List, Optional
from config.settings import KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_ACCOUNT_NO, KIWOOM_USE_MOCK
from src.utils.logger import logger

class KiwoomAPIClient:
    """
    키움 REST API (Test/Real) 클라이언트입니다.
    계좌 잔고/보유 종목 조회, 일봉 시세 및 수급(외국인/기관) 데이터 수집을 담당합니다.
    """
    BASE_URL = "https://openapi.kiwoom.com"  # 키움 REST API 엔드포인트

    def __init__(self,
                 app_key: str = KIWOOM_APP_KEY,
                 app_secret: str = KIWOOM_APP_SECRET,
                 account_no: str = KIWOOM_ACCOUNT_NO,
                 use_mock: bool = KIWOOM_USE_MOCK):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no
        self.use_mock = use_mock
        self.access_token = None

    def is_valid_key(self) -> bool:
        return bool(self.app_key and self.app_key != "YOUR_KIWOOM_APP_KEY_HERE" and not self.use_mock)

    def get_access_token(self) -> Optional[str]:
        """키움 REST API OAuth 2.0 접근 토큰 발급"""
        if not self.is_valid_key():
            return "MOCK_ACCESS_TOKEN"

        url = f"{self.BASE_URL}/oauth2/token"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret
        }
        try:
            res = requests.post(url, headers=headers, json=body, timeout=10)
            if res.status_code == 200:
                self.access_token = res.json().get("access_token")
                logger.info("[Kiwoom API] OAuth 2.0 토큰 발급 성공")
                return self.access_token
            else:
                logger.error(f"[Kiwoom API] 토큰 발급 실패 (상태코드 {res.status_code}): {res.text}")
                return None
        except Exception as e:
            logger.error(f"[Kiwoom API] 토큰 요청 중 예외 발생: {e}", exc_info=True)
            return None

    def get_account_positions(self) -> List[Dict[str, Any]]:
        """
        사용자 실계좌/모의계좌 보유 종목 잔고 조회
        반환 항목: stock_code, stock_name, quantity, avg_buy_price, current_price, total_invested, eval_pnl_pct
        """
        if not self.is_valid_key():
            logger.info("[Kiwoom API] 모의/테스트 모드로 계좌 보유 종목 샘플 데이터를 반환합니다.")
            return self._get_mock_account_positions()

        url = f"{self.BASE_URL}/api/v1/account/positions"
        headers = {
            "Authorization": f"Bearer {self.access_token or self.get_access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        params = {"account_no": self.account_no}

        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            if res.status_code == 200:
                positions = []
                for item in res.json().get("output", []):
                    positions.append({
                        "stock_code": item.get("pdno"),
                        "stock_name": item.get("prdt_name"),
                        "quantity": int(item.get("hldg_qty", 0)),
                        "avg_buy_price": float(item.get("pchs_avg_pric", 0.0)),
                        "current_price": int(item.get("prpr", 0)),
                        "total_invested": float(item.get("pchs_amt", 0.0)),
                        "eval_pnl_pct": float(item.get("evlu_pfls_rt", 0.0))
                    })
                return positions
            else:
                logger.warning(f"[Kiwoom API] 계좌 잔고 조회 실패 (상태코드 {res.status_code}), 샘플 잔고 사용")
                return self._get_mock_account_positions()
        except Exception as e:
            logger.error(f"[Kiwoom API] 계좌 잔고 조회 예외: {e}", exc_info=True)
            return self._get_mock_account_positions()

    def _get_mock_account_positions(self) -> List[Dict[str, Any]]:
        """
        키움 API 연동 전/실패 시 기본적으로 빈 리스트를 반환하여 사용자의 실제 보유 종목이 오염되지 않도록 합니다.
        """
        return []
