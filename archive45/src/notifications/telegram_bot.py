import requests
from typing import Dict, Any, Optional
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from src.utils.logger import logger

class TelegramNotifier:
    """
    텔레그램 Bot API를 활용해 매매 신호 및 일일 분석 보고서를 전송하는 클래스입니다.
    """
    def __init__(self, bot_token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send_message(self, text: str) -> bool:
        """
        텔레그램으로 텍스트 메시지 전송 (예외 발생 시 로그 남기고 예외 전파 방가)
        """
        if not self.bot_token or self.bot_token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
            logger.warning("[Telegram] 텔레그램 Bot Token이 설정되지 않아 메시지 전송을 건너뜁니다.")
            return False

        if not self.chat_id or self.chat_id == "YOUR_TELEGRAM_CHAT_ID_HERE":
            logger.warning("[Telegram] 텔레그램 Chat ID가 설정되지 않아 메시지 전송을 건너뜁니다.")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        try:
            response = requests.post(self.api_url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("[Telegram] 메시지 전송 성공")
                return True
            else:
                logger.error(f"[Telegram] 전송 실패 (상태코드: {response.status_code}): {response.text}")
                return False
        except Exception as e:
            logger.error(f"[Telegram] 메시지 전송 중 네트워크/API 오류 발생: {e}", exc_info=True)
            return False

    @staticmethod
    def format_signal_message(signal: Dict[str, Any]) -> str:
        """
        사용자 요구사항에 맞춘 텔레그램 매매신호 메시지 포맷팅 함수
        """
        stock_name = signal.get("stock_name", "미상")
        stock_code = signal.get("stock_code", "000000")
        signal_type = signal.get("signal_type", "신호 없음")
        recommended_amount = signal.get("recommended_amount", "0만 원")
        
        f_score = signal.get("f_score", 0.0)
        t_score_raw = signal.get("t_score_raw", 0.0)
        t_score_converted = signal.get("t_score_converted", 0.0)
        score_stage = signal.get("final_score", 0.0)
        completeness = signal.get("data_completeness", 0.0)
        
        stop_loss_price = signal.get("stop_loss_price", 0)
        expected_loss_pct = signal.get("expected_loss_pct", 0.0)
        reason = signal.get("reason", "사유 미기재")

        # T점수 부호 표시 (+/-)
        raw_sign = "+" if t_score_raw > 0 else ""
        t_raw_str = f"{raw_sign}{t_score_raw:.1f}점"

        message = (
            f"<b>[스윙 투자 매매신호 알림]</b>\n"
            f"- 종목명(코드): <b>{stock_name} ({stock_code})</b>\n"
            f"- 신호 유형: <b>{signal_type}</b> (추천금액: {recommended_amount})\n"
            f"- F점수: {f_score:.1f}점 / T점수: {t_raw_str} (환산 {t_score_converted:.1f}점) / 종합점수: <b>{score_stage:.1f}점</b>\n"
            f"- 데이터 완성도: {completeness:.0f}%\n"
            f"- 추천 손절가: <b>{stop_loss_price:,}원</b> (예상 손절폭 {expected_loss_pct:.1f}%)\n"
            f"- 매수 근거: {reason}"
        )
        return message
