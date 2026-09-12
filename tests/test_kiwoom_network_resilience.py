"""
Unit tests for Kiwoom API Network Resilience, Retry Logic, and Fail-Closed Failure Alerting.
Covers 21 essential resilience, no-amplification, non-retryable exception, TEST_MODE email suppression, and isolation requirements.
"""
import hashlib
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import requests
import urllib3

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.api.kiwoom_api import KiwoomAPIClient
from src.notifications.gmail_notifier import GmailNotifier

OPERATIONAL_DB_PATH = BASE_DIR / "data" / "stock_system.db"
OPERATIONAL_JSON_PATH = BASE_DIR / "config" / "portfolio_holdings.json"
OPERATIONAL_LOG_PATH = BASE_DIR / "logs" / "stock_system.log"


def sha256_file(path: Path) -> str:
    if not path.exists():
        return "NOT_EXIST"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TestKiwoomNetworkResilience(unittest.TestCase):
    """키움 API 네트워크 일시 장애 내성 및 Fail-Closed 실패 알림 단위 테스트."""

    @classmethod
    def setUpClass(cls):
        cls.pre_db_hash = sha256_file(OPERATIONAL_DB_PATH)
        cls.pre_json_hash = sha256_file(OPERATIONAL_JSON_PATH)

    def _get_client(self):
        client = KiwoomAPIClient(
            app_key="TEST_APP_KEY_12345",
            app_secret="TEST_APP_SECRET_67890",
            account_no="12345678-01",
            use_mock=False,
        )
        client.RETRY_DELAYS = [0, 0, 0]  # 테스트 속도를 위해 딜레이 0으로 설정
        return client

    def test_01_dns_fail_1_then_success_attempt_2(self):
        """1. DNS 1회 실패 -> 2회차 성공"""
        client = self._get_client()

        mock_ok_resp = MagicMock()
        mock_ok_resp.status_code = 200
        mock_ok_resp.json.return_value = {
            "token": "SUCCESS_TOKEN_ATTEMPT_2",
            "return_code": 0,
            "expires_dt": "2026-08-29 16:00:00",
        }

        with patch(
            "requests.post",
            side_effect=[
                socket.gaierror(-3, "Temporary failure in name resolution"),
                mock_ok_resp,
            ],
        ) as mock_post:
            token = client.get_access_token()
            self.assertEqual(token, "SUCCESS_TOKEN_ATTEMPT_2")
            self.assertEqual(client.access_token, "SUCCESS_TOKEN_ATTEMPT_2")
            self.assertEqual(mock_post.call_count, 2)

    def test_02_dns_fail_2_then_success_attempt_3(self):
        """2. DNS 2회 실패 -> 3회차 성공"""
        client = self._get_client()

        mock_ok_resp = MagicMock()
        mock_ok_resp.status_code = 200
        mock_ok_resp.json.return_value = {
            "access_token": "SUCCESS_TOKEN_ATTEMPT_3",
            "return_code": 0,
        }

        with patch(
            "requests.post",
            side_effect=[
                socket.gaierror(-3, "getaddrinfo failed"),
                urllib3.exceptions.NameResolutionError("api.kiwoom.com", None, None),
                mock_ok_resp,
            ],
        ) as mock_post:
            token = client.get_access_token()
            self.assertEqual(token, "SUCCESS_TOKEN_ATTEMPT_3")
            self.assertEqual(client.access_token, "SUCCESS_TOKEN_ATTEMPT_3")
            self.assertEqual(mock_post.call_count, 3)

    def test_03_dns_fail_all_3_attempts_fail_closed(self):
        """3. 3회 모두 DNS 실패 -> Fail-Closed (토큰 None 반환 및 API_UNAVAILABLE)"""
        client = self._get_client()

        with patch(
            "requests.post",
            side_effect=[
                socket.gaierror(-3, "Temporary failure in name resolution"),
                socket.gaierror(-3, "Temporary failure in name resolution"),
                socket.gaierror(-3, "Temporary failure in name resolution"),
            ],
        ) as mock_post:
            token = client.get_access_token()
            self.assertIsNone(token)
            self.assertIsNone(client.access_token)
            self.assertEqual(mock_post.call_count, 3)

    def test_04_timeout_retry_success(self):
        """4. Timeout retry (1회 타임아웃 후 2회차 성공)"""
        client = self._get_client()

        mock_ok_resp = MagicMock()
        mock_ok_resp.status_code = 200
        mock_ok_resp.json.return_value = {"token": "TIMEOUT_RETRY_TOKEN", "return_code": 0}

        with patch(
            "requests.post",
            side_effect=[
                requests.exceptions.Timeout("Connection timed out"),
                mock_ok_resp,
            ],
        ) as mock_post:
            token = client.get_access_token()
            self.assertEqual(token, "TIMEOUT_RETRY_TOKEN")
            self.assertEqual(mock_post.call_count, 2)

    def test_05_connection_error_retry_success(self):
        """5. ConnectionError retry (1회 연결 에러 후 2회차 성공)"""
        client = self._get_client()

        mock_ok_resp = MagicMock()
        mock_ok_resp.status_code = 200
        mock_ok_resp.json.return_value = {"token": "CONN_RETRY_TOKEN", "return_code": 0}

        with patch(
            "requests.post",
            side_effect=[
                requests.exceptions.ConnectionError("Connection refused"),
                mock_ok_resp,
            ],
        ) as mock_post:
            token = client.get_access_token()
            self.assertEqual(token, "CONN_RETRY_TOKEN")
            self.assertEqual(mock_post.call_count, 2)

    def test_06_http_401_no_retry(self):
        """6. HTTP 401 Unauthorized -> 재시도 없이 즉시 실패 (call_count = 1)"""
        client = self._get_client()

        mock_401_resp = MagicMock()
        mock_401_resp.status_code = 401
        mock_401_resp.text = "Unauthorized: Invalid AppKey"

        with patch("requests.post", return_value=mock_401_resp) as mock_post:
            token = client.get_access_token()
            self.assertIsNone(token)
            self.assertEqual(mock_post.call_count, 1)

    def test_07_http_403_no_retry(self):
        """7. HTTP 403 Forbidden -> 재시도 없이 즉시 실패 (call_count = 1)"""
        client = self._get_client()

        mock_403_resp = MagicMock()
        mock_403_resp.status_code = 403
        mock_403_resp.text = "Forbidden"

        with patch("requests.post", return_value=mock_403_resp) as mock_post:
            token = client.get_access_token()
            self.assertIsNone(token)
            self.assertEqual(mock_post.call_count, 1)

    def test_08_malformed_json_response_no_retry(self):
        """8. malformed response -> 재시도 없이 즉시 실패 (call_count = 1)"""
        client = self._get_client()

        mock_bad_resp = MagicMock()
        mock_bad_resp.status_code = 200
        mock_bad_resp.json.side_effect = ValueError("Invalid JSON")

        with patch("requests.post", return_value=mock_bad_resp) as mock_post:
            token = client.get_access_token()
            self.assertIsNone(token)
            self.assertEqual(mock_post.call_count, 1)

    def test_09_final_failure_no_normal_report_generated(self):
        """9. 최종 실패 시 정상 리포트 미생성 및 Fail-Closed 예외 발생 검증"""
        client = self._get_client()
        with patch.object(client, "get_access_token", return_value=None):
            with self.assertRaises(RuntimeError) as cm:
                client.get_account_positions()
            self.assertIn("KIWOOM_API_UNAVAILABLE", str(cm.exception))

    def test_10_final_failure_generates_failure_alert(self):
        """10. 최종 실패 시 실패 알림 메일 생성 및 전송 시도 검증 (Production mode fixture)"""
        notifier = GmailNotifier()
        with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "0"}):
            with patch.object(notifier, "send_email", return_value=True) as mock_send:
                success = notifier.send_failure_alert(
                    session_name="15:35 장마감",
                    failed_step="PORTFOLIO_SYNC",
                    error_reason="KIWOOM_API_UNAVAILABLE",
                    date_str_korean="8월 28일 15:35",
                )
                self.assertTrue(success)
                self.assertEqual(mock_send.call_count, 1)
                call_kwargs = mock_send.call_args.kwargs
                self.assertIn("[StockBot 실행 실패] 8월 28일 15:35 15:35 장마감 리포트", call_kwargs["subject"])
                self.assertIn("PORTFOLIO_SYNC", call_kwargs["html_content"])
                self.assertIn("KIWOOM_API_UNAVAILABLE", call_kwargs["html_content"])
                self.assertIn("Fail-Closed", call_kwargs["html_content"])
                self.assertIsNone(call_kwargs.get("attachments"))

    def test_11_failure_alert_contains_no_price_or_strategy_data(self):
        """11. 실패 알림에 투자 가격/전략 값 없음 검증 (오염/추정 데이터 원천 차단)"""
        notifier = GmailNotifier()
        with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "0"}):
            with patch.object(notifier, "send_email", return_value=True) as mock_send:
                notifier.send_failure_alert(
                    session_name="15:35 장마감",
                    failed_step="PORTFOLIO_SYNC",
                    error_reason="KIWOOM_API_UNAVAILABLE",
                    date_str_korean="8월 28일",
                )
                call_kwargs = mock_send.call_args.kwargs
                html = call_kwargs["html_content"]

                forbidden_financial_terms = [
                    "손절가", "목표가", "추적선", "평균단가", "현재가", "평가손익",
                    "권고수량", "매수신호", "매도신호", "P0", "A0",
                    "ratchet", "stop_price", "f_score", "t_score", "종목코드"
                ]
                for term in forbidden_financial_terms:
                    self.assertNotIn(
                        term, html,
                        f"실패 알림 HTML에 금지된 투자 정보 키워드 '{term}'가 포함되어 있습니다."
                    )

    def test_12_no_mock_or_cached_account_fallback_on_failure(self):
        """12. mock/cached account fallback 없음 (실계좌 키 활성 시 Fallback 금지)"""
        client = self._get_client()
        self.assertTrue(client.is_valid_key())

        with patch.object(client, "get_access_token", return_value=None):
            with patch.object(client, "_get_mock_account_positions") as mock_fallback:
                with self.assertRaises(RuntimeError):
                    client.get_account_positions()
                mock_fallback.assert_not_called()

    def test_13_retry_does_not_mutate_client_state_unexpectedly(self):
        """13. retry가 상태를 mutate하지 않음 (실패 시 access_token이 None으로 유지됨)"""
        client = self._get_client()
        client.access_token = None

        with patch(
            "requests.post",
            side_effect=[
                socket.gaierror(-3, "Temporary failure"),
                requests.exceptions.Timeout("Timeout"),
                requests.exceptions.ConnectionError("Refused"),
            ],
        ):
            token = client.get_access_token()
            self.assertIsNone(token)
            self.assertIsNone(client.access_token)

    def test_14_operational_files_unchanged_during_test(self):
        """14. 운영 DB/JSON 테스트 오염 없음 검증 (SHA-256 불변)"""
        post_db_hash = sha256_file(OPERATIONAL_DB_PATH)
        post_json_hash = sha256_file(OPERATIONAL_JSON_PATH)

        if self.pre_db_hash != "NOT_EXIST":
            self.assertEqual(
                self.pre_db_hash, post_db_hash,
                f"운영 DB가 테스트 실행 중 변경되었습니다! (PRE: {self.pre_db_hash}, POST: {post_db_hash})"
            )
        if self.pre_json_hash != "NOT_EXIST":
            self.assertEqual(
                self.pre_json_hash, post_json_hash,
                f"운영 JSON이 테스트 실행 중 변경되었습니다! (PRE: {self.pre_json_hash}, POST: {post_json_hash})"
            )

    def test_15_generic_urllib3_httperror_no_retry(self):
        """15. generic urllib3 HTTPError -> 재시도 대상에서 제외되어 즉시 실패 (call_count = 1)"""
        client = self._get_client()

        with patch("requests.post", side_effect=urllib3.exceptions.HTTPError("Generic HTTP error")) as mock_post:
            token = client.get_access_token()
            self.assertIsNone(token)
            self.assertEqual(mock_post.call_count, 1)

    def test_16_account_layer_does_not_repeat_token_retries_on_failure(self):
        """16. token 3회 실패 후 account layer가 다시 token을 반복하지 않고 즉시 Fail-Closed (총 토큰 요청 = 3회, 계좌 요청 = 0회)"""
        client = self._get_client()

        with patch("requests.post", side_effect=socket.gaierror(-3, "DNS fail")) as mock_post:
            with self.assertRaises(RuntimeError) as cm:
                client.get_account_positions()
            self.assertIn("KIWOOM_API_UNAVAILABLE", str(cm.exception))
            self.assertEqual(mock_post.call_count, 3)

    def test_17_account_request_transient_failure_retries_internally_max_3(self):
        """17. account request 일시 장애 시 계좌 레이어 자체에서 최대 3회 재시도 후 성공"""
        client = self._get_client()
        client.access_token = "VALID_PRE_CACHED_TOKEN"

        mock_ok_resp = MagicMock()
        mock_ok_resp.status_code = 200
        mock_ok_resp.json.return_value = {
            "acnt_evlt_remn_indv_tot": [
                {
                    "stk_cd": "000490",
                    "stk_nm": "대동",
                    "rmnd_qty": "10",
                    "pur_pric": "7800",
                    "cur_prc": "8100",
                }
            ]
        }

        with patch(
            "requests.post",
            side_effect=[
                socket.gaierror(-3, "DNS lookup fail"),
                requests.exceptions.Timeout("Read timeout"),
                mock_ok_resp,
            ],
        ) as mock_post:
            positions = client.get_account_positions()
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0]["stock_code"], "000490")
            self.assertEqual(mock_post.call_count, 3)

    def test_18_account_endpoint_http_401_403_no_retry(self):
        """18. account endpoint HTTP 401/403 -> 재시도 없이 1회 요청 후 즉시 예외 발생"""
        client = self._get_client()
        client.access_token = "VALID_PRE_CACHED_TOKEN"

        mock_403_resp = MagicMock()
        mock_403_resp.status_code = 403
        mock_403_resp.text = "Forbidden kt00018"

        with patch("requests.post", return_value=mock_403_resp) as mock_post:
            with self.assertRaises(RuntimeError) as cm:
                client.get_account_positions()
            self.assertIn("상태코드 403", str(cm.exception))
            self.assertEqual(mock_post.call_count, 1)

    def test_19_test_mode_suppresses_send_failure_alert_transport(self):
        """19. STOCKBOT_TEST_MODE=1 환경에서 send_failure_alert는 실제 send_email/SMTP를 호출하지 않고 억제함"""
        notifier = GmailNotifier()
        with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "1"}):
            with patch.object(notifier, "send_email") as mock_send_email:
                result = notifier.send_failure_alert(
                    session_name="15:35 장마감",
                    failed_step="WATCHLIST_SCAN",
                    error_reason="보유종목 평가 실패: DB상 1개 존재하나 평가 결과 0개 검출",
                    date_str_korean="8월 29일",
                )
                self.assertFalse(result)
                mock_send_email.assert_not_called()

    def test_20_test_mode_suppresses_send_email_transport(self):
        """20. STOCKBOT_TEST_MODE=1 환경에서 send_email은 실제 SMTP 서버 연결을 호출하지 않고 억제함"""
        notifier = GmailNotifier()
        with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "1"}):
            with patch("smtplib.SMTP_SSL") as mock_smtp:
                result = notifier.send_email(
                    subject="Test Subject",
                    html_content="<p>Test Content</p>",
                )
                self.assertFalse(result)
                mock_smtp.assert_not_called()

    def test_21_production_mode_preserves_send_failure_alert_path(self):
        """21. 운영 모드(STOCKBOT_TEST_MODE=0)에서는 send_failure_alert가 send_email transport를 정상 호출함"""
        notifier = GmailNotifier()
        with patch.dict(os.environ, {"STOCKBOT_TEST_MODE": "0"}):
            with patch.object(notifier, "send_email", return_value=True) as mock_send:
                result = notifier.send_failure_alert(
                    session_name="15:35 장마감",
                    failed_step="PORTFOLIO_SYNC",
                    error_reason="KIWOOM_API_UNAVAILABLE",
                    date_str_korean="8월 28일",
                )
                self.assertTrue(result)
                self.assertEqual(mock_send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
