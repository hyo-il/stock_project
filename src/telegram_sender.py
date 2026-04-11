"""텔레그램 메시지 전송 모듈."""

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MAX_LEN = 4000  # 텔레그램 4,096자 한도에서 여유 96자 확보


def send_message(text: str) -> bool:
    """텔레그램으로 메시지를 전송합니다.

    4,000자 초과 시 자동 분할하여 순서대로 전송합니다.
    연속 전송 간 0.5초 딜레이를 적용합니다.

    Args:
        text: 전송할 메시지 텍스트 (HTML 태그 사용 가능)

    Returns:
        전체 메시지 전송 성공 여부
    """
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.error("TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 환경 변수가 설정되지 않았습니다.")
        return False

    chunks = _split_message(text)
    success = True

    for i, chunk in enumerate(chunks):
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.ok:
            logger.info("텔레그램 메시지 전송 성공 (%d/%d)", i + 1, len(chunks))
        else:
            logger.error("텔레그램 전송 실패 (%d/%d): %s %s", i + 1, len(chunks), resp.status_code, resp.text)
            success = False

        if i < len(chunks) - 1:
            time.sleep(0.5)

    return success


def _split_message(text: str) -> list:
    """메시지를 MAX_LEN 이하의 청크로 분할합니다.

    줄바꿈 경계에서 분할하여 HTML 태그가 깨지지 않도록 합니다.
    """
    if len(text) <= MAX_LEN:
        return [text]

    chunks = []
    while text:
        if len(text) <= MAX_LEN:
            chunks.append(text)
            break
        split_pos = text.rfind("\n", 0, MAX_LEN)
        if split_pos == -1:
            split_pos = MAX_LEN
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    return chunks
