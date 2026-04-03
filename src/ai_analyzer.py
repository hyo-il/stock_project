"""Google Gemini AI를 이용한 시장 분석 모듈."""

import logging
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-2.5-flash"


def analyze_with_ai(news: list, stocks: dict) -> Optional[str]:
    """Google Gemini AI로 뉴스와 주식 데이터를 분석합니다.

    GEMINI_API_KEY 환경 변수가 없거나 빈 문자열이면 None을 반환합니다.

    Args:
        news: 뉴스 딕셔너리 리스트 (title, link, published 키)
        stocks: 주식 지수 딕셔너리 (current, change, change_pct 키)

    Returns:
        AI 분석 결과 문자열, 또는 분석 불가 시 None
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.info("GEMINI_API_KEY가 없습니다. AI 분석을 건너뜁니다.")
        return None

    try:
        from google import genai
    except ImportError:
        logger.warning("google-genai 패키지가 설치되지 않았습니다.")
        return None

    try:
        client = genai.Client(api_key=api_key)
        prompt = _build_prompt(news, stocks)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
        )
        result = response.text.strip()
        logger.info("Gemini AI 분석 완료 (%d자)", len(result))
        return result

    except Exception as e:
        logger.warning("Gemini AI 분석 실패: %s", e)
        return None


def _build_prompt(news: list, stocks: dict) -> str:
    """Gemini AI에 전달할 프롬프트를 구성합니다.

    Args:
        news: 뉴스 딕셔너리 리스트
        stocks: 주식 지수 딕셔너리

    Returns:
        완성된 프롬프트 문자열
    """
    news_lines = "\n".join(
        f"- {item['title']}" for item in news
    )

    stock_lines = []
    name_map = {
        "KOSPI": "코스피",
        "KOSDAQ": "코스닥",
        "SP500": "S&P 500",
        "NASDAQ": "나스닥",
        "DOW": "다우존스",
    }
    for key, info in stocks.items():
        display_name = name_map.get(key, key)
        sign = "+" if info["change"] >= 0 else ""
        stock_lines.append(
            f"- {display_name}: {info['current']:,.2f} ({sign}{info['change_pct']:.2f}%)"
        )
    stock_text = "\n".join(stock_lines)

    return f"""당신은 한국 주식 시장 분석 전문가입니다.
오늘의 경제 뉴스 헤드라인과 주요 지수 데이터를 참고하여 아래 세 가지를 한국어로 간결하게 분석해주세요.

1. 오늘 시장의 핵심 이슈 요약 (2~3줄)
2. 단기 시장 방향 예상
3. 주요 이슈로 영향받을 예상 섹터 또는 종목 3~5개 (이유 포함)

[뉴스 헤드라인]
{news_lines}

[주요 지수]
{stock_text}"""
