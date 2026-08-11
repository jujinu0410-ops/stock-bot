import logging
import sys
from config.settings import LOG_FILE_PATH

def setup_logger(name: str = "stock_system") -> logging.Logger:
    """
    콘솔 출력 및 파일 저장을 동시에 수행하는 로거를 설정하고 반환합니다.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 중복 방지를 위한 핸들러 초기화
    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s:%(filename)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 콘솔 핸들러 (Windows CP949 이모지 대응)
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    # 파일 핸들러
    file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    return logger

# 기본 공유 로거 인스턴스
logger = setup_logger()
