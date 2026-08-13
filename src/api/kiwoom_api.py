import requests
from typing import Dict, Any, List, Optional
from config.settings import KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_ACCOUNT_NO, KIWOOM_USE_MOCK
from src.utils.logger import logger

class KiwoomAPIClient:
    """
    키움 REST API (Test/Real) 클라이언트입니다.
    계좌 잔고/보유 종목 조회, 일봉 시세 및 수급(외국인/기관) 데이터 수집을 담당합니다.
    """
    BASE_URL = "https://api.kiwoom.com"  # 키움 REST API 정식 엔드포인트

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
                res_data = res.json()
                self.access_token = res_data.get("token") or res_data.get("access_token")
                logger.info("[Kiwoom API] OAuth 2.0 실시간 접근 토큰 발급 성공")
                return self.access_token
            else:
                logger.error(f"[Kiwoom API] 토큰 발급 실패 (상태코드 {res.status_code}): {res.text}")
                return None
        except Exception as e:
            logger.error(f"[Kiwoom API] 토큰 요청 중 예외 발생: {e}", exc_info=True)
            return None

    def get_account_positions(self) -> List[Dict[str, Any]]:
        """
        사용자 실계좌 보유 종목 잔고 100% 실시간 조회 (TR: kt00018 계좌평가잔고내역요청)
        """
        if not self.is_valid_key():
            logger.info("[Kiwoom API] 키움 API키 미설정으로 포트폴리오 구성 파일 잔고를 사용합니다.")
            return self._get_mock_account_positions()

        token = self.access_token or self.get_access_token()
        if not token:
            logger.warning("[Kiwoom API] 토큰 수집 실패로 포트폴리오 구성 파일 잔고 사용")
            return self._get_mock_account_positions()

        url = f"{self.BASE_URL}/api/dostk/acnt"
        headers = {
            "Authorization": f"Bearer {token}",
            "api-id": "kt00018",
            "content-type": "application/json"
        }
        clean_acnt = self.account_no.replace("-", "").strip()
        body = {
            "acnt_no": clean_acnt,
            "qry_tp": "0",
            "dmst_stex_tp": "KRX"
        }

        import time
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                res = requests.post(url, headers=headers, json=body, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    items = data.get("acnt_evlt_remn_indv_tot", [])
                    positions = []
                    for item in items:
                        raw_code = item.get("stk_cd", "").replace("A", "").strip()
                        name = item.get("stk_nm", "").strip()
                        qty = int(item.get("rmnd_qty", 0))
                        if qty <= 0:
                            continue
                        avg_p = float(item.get("pur_pric", 0.0))
                        cur_p = int(item.get("cur_prc", 0)) if item.get("cur_prc") else int(item.get("pred_close_pric", 0))
                        inv_amt = float(item.get("pur_amt", 0.0))
                        pnl_rt = float(item.get("prft_rt", 0.0))

                        positions.append({
                            "stock_code": raw_code,
                            "stock_name": name,
                            "quantity": qty,
                            "avg_buy_price": avg_p,
                            "current_price": cur_p,
                            "total_invested": inv_amt,
                            "eval_pnl_pct": pnl_rt
                        })

                    if len(positions) > 0 or attempt == max_retries:
                        logger.info(f"[Kiwoom API] 키움 REST API kt00018 실시간 계좌 잔고 {len(positions)}개 종목 수집 완료! (시도 {attempt}/{max_retries})")
                        return positions
                    else:
                        logger.warning(f"[Kiwoom API] 실계좌 수집 0개 수신, 1초 후 재시도... (시도 {attempt}/{max_retries})")
                        time.sleep(1)
                else:
                    logger.warning(f"[Kiwoom API] 잔고 조회 실패 (상태코드 {res.status_code}): {res.text}")
                    if attempt < max_retries:
                        time.sleep(1)
            except Exception as e:
                logger.error(f"[Kiwoom API] 잔고 조회 중 예외 발생 (시도 {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(1)

        return self._get_mock_account_positions()

    def _get_mock_account_positions(self) -> List[Dict[str, Any]]:
        """
        키움 REST API 연동 전/헤드리스 구동 시 config/portfolio_holdings.json 파일에서 
        사용자의 최신 실제 보유 종목(대동 000490 신규 매수 포함)을 읽어와 동기화합니다.
        """
        import json
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / "portfolio_holdings.json"
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    holdings = json.load(f)
                    logger.info(f"[Kiwoom API] config/portfolio_holdings.json 기반 최신 계좌 보유종목 {len(holdings)}개 로드 성공")
                    return holdings
            except Exception as e:
                logger.error(f"[Kiwoom API] portfolio_holdings.json 읽기 오류: {e}")
        return []
