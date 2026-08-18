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
        cont-yn 및 next-key (헤더 및 본문 동시 탐지)를 활용한 전 페이지 연속조회 지원 및 운영 가드레일 적용
        """
        import os
        is_ci = os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"
        commit_sha = os.getenv("GITHUB_SHA", "UNKNOWN")[:8]
        event_name = os.getenv("GITHUB_EVENT_NAME", "LOCAL")

        if not self.is_valid_key():
            if is_ci:
                logger.critical(f"[Kiwoom API] 🛑 [운영 Fallback 금지] Commit: {commit_sha} | Event: {event_name} | 운영 환경(CI)에서 키움 API키가 유효하지 않습니다. 작업을 중단합니다.")
                raise RuntimeError("운영 환경(CI)에서 키움 API키 미설정 또는 유효하지 않음 (Fallback 금지)")
            logger.info(f"[Kiwoom API] [Data Source: MOCK] Commit: {commit_sha} | Event: {event_name} | 키움 API키 미설정으로 로컬 mock/JSON 잔고 사용")
            return self._get_mock_account_positions()

        token = self.access_token or self.get_access_token()
        if not token or token == "MOCK_ACCESS_TOKEN":
            if is_ci or self.is_valid_key():
                logger.critical(f"[Kiwoom API] 🛑 [운영 Fallback 금지] Commit: {commit_sha} | Event: {event_name} | 실계좌 접근 토큰 발급에 실패했습니다.")
                raise RuntimeError("키움 실계좌 OAuth 접근 토큰 발급 실패 (운영 Fallback 금지)")
            logger.warning("[Kiwoom API] 토큰 수집 실패로 포트폴리오 구성 파일 잔고 사용")
            return self._get_mock_account_positions()

        url = f"{self.BASE_URL}/api/dostk/acnt"
        clean_acnt = self.account_no.replace("-", "").strip()
        masked_acnt = clean_acnt[:4] + "****" + clean_acnt[-2:] if len(clean_acnt) >= 6 else "****"

        all_collected_positions = []
        seen_next_keys = set()
        next_key = ""
        page = 1
        MAX_PAGES = 20

        while True:
            headers = {
                "Authorization": f"Bearer {token}",
                "api-id": "kt00018",
                "content-type": "application/json"
            }
            body = {
                "acnt_no": clean_acnt,
                "qry_tp": "0",
                "dmst_stex_tp": "KRX"
            }

            if next_key:
                headers["cont-yn"] = "Y"
                headers["next-key"] = next_key
                body["cont_yn"] = "Y"
                body["next_key"] = next_key

            try:
                res = requests.post(url, headers=headers, json=body, timeout=10)
            except Exception as e:
                logger.error(f"[Kiwoom API] [Data Source: KIWOOM_LIVE] Commit: {commit_sha} | Event: {event_name} | API: kt00018 | Page {page} | 네트워크 예외 발생: {e}")
                raise RuntimeError(f"[Kiwoom API] 잔고 연속조회 중 네트워크 예외 발생 (페이지 {page}): {e}")

            if res.status_code != 200:
                logger.error(f"[Kiwoom API] [Data Source: KIWOOM_LIVE] Commit: {commit_sha} | Event: {event_name} | API: kt00018 | Page {page} | HTTP {res.status_code} 실패: {res.text}")
                raise RuntimeError(f"[Kiwoom API] 잔고 연속조회 실패 (페이지 {page}, 상태코드 {res.status_code}): {res.text}")

            try:
                data = res.json()
            except Exception as e_json:
                logger.error(f"[Kiwoom API] [Data Source: KIWOOM_LIVE] Page {page} JSON 파싱 실패: {e_json}")
                raise RuntimeError(f"[Kiwoom API] Page {page} JSON 파싱 실패: {e_json}")

            top_keys = list(data.keys()) if isinstance(data, dict) else []
            items = data.get("acnt_evlt_remn_indv_tot", []) if isinstance(data, dict) else []

            page_positions = []
            for item in items:
                raw_code = item.get("stk_cd", "").replace("A", "").strip().zfill(6)
                name = item.get("stk_nm", "").strip()
                qty = int(item.get("rmnd_qty", 0))
                if qty <= 0:
                    continue
                avg_p = float(item.get("pur_pric", 0.0))
                cur_p = int(item.get("cur_prc", 0)) if item.get("cur_prc") else int(item.get("pred_close_pric", 0))
                inv_amt = float(item.get("pur_amt", 0.0))
                pnl_rt = float(item.get("prft_rt", 0.0))

                page_positions.append({
                    "stock_code": raw_code,
                    "stock_name": name,
                    "quantity": qty,
                    "avg_buy_price": avg_p,
                    "current_price": cur_p,
                    "total_invested": inv_amt,
                    "eval_pnl_pct": pnl_rt
                })

            all_collected_positions.extend(page_positions)
            page_codes = [p["stock_code"] for p in page_positions]
            cum_codes = [p["stock_code"] for p in all_collected_positions]

            # 연속조회 헤더 및 본문 동시 탐지
            header_cont_yn = (res.headers.get("cont-yn") or res.headers.get("cont_yn") or res.headers.get("tr_cont") or "").strip().upper()
            body_cont_yn = str(data.get("cont-yn") or data.get("cont_yn") or data.get("tr_cont") or "").strip().upper()
            cont_yn = "Y" if ("Y" in (header_cont_yn, body_cont_yn)) else ("N" if ("N" in (header_cont_yn, body_cont_yn)) else "N")

            header_next_key = (res.headers.get("next-key") or res.headers.get("next_key") or res.headers.get("tr_cont_key") or "").strip()
            body_next_key = str(data.get("next-key") or data.get("next_key") or data.get("ctx_area_fk100") or data.get("ctx_area_nk100") or "").strip()
            res_next_key = header_next_key or body_next_key

            logger.info(
                f"[Kiwoom API Raw Audit] Commit: {commit_sha} | Event: {event_name} | Source: KIWOOM_LIVE | "
                f"API: kt00018 | Page: {page} | HTTP: {res.status_code} | Acnt: {masked_acnt} | "
                f"TopKeys: {top_keys} | PageItems: {len(page_positions)}개 {page_codes} | "
                f"ContYN: [Header='{header_cont_yn}', Body='{body_cont_yn}', Effective='{cont_yn}'] | "
                f"NextKey: [HeaderPresent={bool(header_next_key)}, BodyPresent={bool(body_next_key)}, EffectiveLen={len(res_next_key)}] | "
                f"CumulativeItems: {len(all_collected_positions)}개 {cum_codes}"
            )

            if cont_yn == "Y":
                if not res_next_key:
                    logger.error(f"[Kiwoom API] 연속조회 오류: cont-yn='Y'이나 next-key가 누락되었습니다. (페이지 {page})")
                    raise RuntimeError(f"[Kiwoom API] 연속조회 오류: cont-yn='Y'이나 next-key가 누락되었습니다. (페이지 {page})")

                if res_next_key in seen_next_keys:
                    logger.error(f"[Kiwoom API] 연속조회 오류: 동일한 next-key('{res_next_key}')가 반복되어 무한루프가 감지되었습니다. (페이지 {page})")
                    raise RuntimeError(f"[Kiwoom API] 연속조회 오류: 동일한 next-key('{res_next_key}')가 반복되어 무한루프가 감지되었습니다. (페이지 {page})")

                seen_next_keys.add(res_next_key)
                page += 1

                if page > MAX_PAGES:
                    logger.error(f"[Kiwoom API] 연속조회 오류: 최대 허용 페이지 수({MAX_PAGES})를 초과했습니다.")
                    raise RuntimeError(f"[Kiwoom API] 연속조회 오류: 최대 허용 페이지 수({MAX_PAGES})를 초과했습니다.")

                next_key = res_next_key
            else:
                break

        # 종목코드 중복 제거 (순서 보존)
        unique_positions = {}
        for p in all_collected_positions:
            code = p["stock_code"]
            unique_positions[code] = p
        positions = list(unique_positions.values())

        if (is_ci or self.is_valid_key()) and len(positions) == 0:
            logger.critical(f"[Kiwoom API] 🛑 [운영 Fallback 금지] 실계좌에서 수집된 종목이 0개입니다. (Acnt: {masked_acnt})")
            raise RuntimeError("키움 실계좌 잔고가 비정상적으로 0개 수집됨 (운영 Fallback 금지)")

        logger.info(f"[Kiwoom API] 키움 REST API kt00018 실시간 계좌 잔고 전 페이지 수집 완료! (총 {page}페이지 수집, 중복제거 후 {len(positions)}개 종목: {[p['stock_code'] for p in positions]})")
        return positions

    def _get_mock_account_positions(self) -> List[Dict[str, Any]]:
        """
        키움 REST API 연동 전/로컬 테스트 구동 시 config/portfolio_holdings.json 파일에서 
        사용자의 최신 실제 보유 종목(대동 000490 신규 매수 포함)을 읽어와 반환합니다.
        """
        import json
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / "portfolio_holdings.json"
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    holdings = json.load(f)
                    logger.info(f"[Kiwoom API] [Data Source: LOCAL_JSON_FALLBACK] config/portfolio_holdings.json 기반 로컬 잔고 {len(holdings)}개 로드 성공")
                    return holdings
            except Exception as e:
                logger.error(f"[Kiwoom API] portfolio_holdings.json 읽기 오류: {e}")
        return []
