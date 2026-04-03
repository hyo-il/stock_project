"""텔레그램 메시지 전송 모듈."""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096


def send_message(text: str) -> bool:
    """텔레그램으로 메시지를 전송합니다.

    메시지가 4,096자를 초과하면 자동으로 분할하여 순서대로 전송합니다.

    Args:
        text: 전송할 메시지 텍스트 (HTML 태그 사용 가능)

    Returns:
        전송 성공 여부 (전체 메시지 전송 성공 시 True)
    """
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.error("TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 환경 변수가 설정되지 않았습니다.")
        return False

    url = TELEGRAM_API_URL.format(token=token)
    chunks = _split_message(text)
    success = True

    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("텔레그램 메시지 전송 성공 (%d/%d)", i + 1, len(chunks))
        except requests.RequestException as e:
            logger.error("텔레그램 메시지 전송 실패 (%d/%d): %s", i + 1, len(chunks), e)
            success = False

    return success


def _split_message(text: str) -> list[str]:
    """메시지를 4,096자 이하의 청크로 분할합니다.

    Args:
        text: 분할할 메시지 텍스트

    Returns:
        분할된 메시지 리스트
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    chunks = []
    while text:
        if len(text) <= MAX_MESSAGE_LENGTH:
            chunks.append(text)
            break
        split_pos = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
        if split_pos == -1:
            split_pos = MAX_MESSAGE_LENGTH
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    return chunks
