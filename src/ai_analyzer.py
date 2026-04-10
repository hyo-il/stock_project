"""Google Gemini AI를 이용한 뉴스 섹터 분류·번역 및 시장 분석 모듈."""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from config import SECTORS

load_dotenv()

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
MODEL_NAME = "gemini-2.5-flash"

_SECTOR_NAMES = [s["name"] for s in SECTORS]


def _get_gemini_client():
    """Gemini API 클라이언트를 반환합니다. API 키 없거나 패키지 미설치 시 None 반환."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.info("GEMINI_API_KEY가 없습니다. AI 기능을 건너뜁니다.")
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except ImportError:
        logger.warning("google-genai 패키지가 설치되지 않았습니다.")
        return None


def _parse_json_response(raw: str):
    """Gemini 응답에서 JSON을 파싱합니다. 코드블록 래핑 및 흔한 형식 오류를 자동 처리."""
    import re
    raw = raw.strip()
    # 코드블록 제거
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    # 트레일링 콤마 제거: ,} 또는 ,]
    raw = re.sub(r",\s*([\}\]])", r"\1", raw)
    return json.loads(raw)


def classify_news_by_sector(
    all_news: list,
    today_str: str,
) -> Dict[str, List[dict]]:
    """전체 뉴스를 섹터별로 분류하고 해외 뉴스는 번역합니다.

    개수 제한 없이 관련 뉴스를 모두 포함합니다.
    Gemini API 키가 없으면 전체 뉴스를 첫 번째 섹터에 폴백합니다.

    Args:
        all_news: 국내+해외 통합 뉴스 리스트 (is_foreign 플래그 포함)
        today_str: KST 기준 날짜 문자열

    Returns:
        {섹터명: [뉴스 딕셔너리, ...], ...} 형태의 섹터별 뉴스 딕셔너리
    """
    empty_result = {s: [] for s in _SECTOR_NAMES}

    if not all_news:
        return empty_result

    client = _get_gemini_client()
    if client is None:
        fallback = {s: [] for s in _SECTOR_NAMES}
        fallback[_SECTOR_NAMES[0]] = all_news
        return fallback

    # 프롬프트용 섹터 정의 문자열 (config에서 동적 생성)
    sector_defs = "\n".join(f"- {s['name']}: {s['keywords']}" for s in SECTORS)

    # 프롬프트 JSON 응답 예시 (config에서 동적 생성, index는 정수)
    json_example = (
        "{\n"
        + ",\n".join(
            f'  "{s}": [{{"index": 0, "translated_title": "제목 예시"}}]'
            for s in _SECTOR_NAMES
        )
        + "\n}"
    )

    news_for_prompt = [
        {
            "index": i,
            "title": item["title"],
            "source": item.get("source", ""),
            "is_foreign": item.get("is_foreign", False),
        }
        for i, item in enumerate(all_news)
    ]

    prompt = f"""오늘 날짜는 {today_str}입니다.

아래 뉴스 목록을 {len(SECTORS)}개 섹터로 분류하고 해외 뉴스는 번역해주세요.

섹터 정의:
{sector_defs}

규칙:
- 하나의 뉴스는 가장 관련성 높은 섹터 하나에만 분류
- 어느 섹터에도 해당하지 않으면 제외 (개수 제한 없음, 해당 뉴스 모두 포함)
- is_foreign이 true인 뉴스는 제목을 자연스러운 한국어로 번역

[뉴스 목록]
{json.dumps(news_for_prompt, ensure_ascii=False)}

JSON 형식으로만 응답하고 다른 설명은 절대 추가하지 마세요:
{json_example}"""

    try:
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        classified = _parse_json_response(response.text)

        result = {s: [] for s in _SECTOR_NAMES}
        for sector_name in _SECTOR_NAMES:
            for item in classified.get(sector_name, []):
                idx = item.get("index")
                if idx is None or idx >= len(all_news):
                    continue
                news_item = all_news[idx].copy()
                news_item["title"] = item.get("translated_title", news_item["title"])
                result[sector_name].append(news_item)

        counts = ", ".join(f"{s} {len(result[s])}" for s in _SECTOR_NAMES)
        total = sum(len(v) for v in result.values())
        logger.info("AI 뉴스 섹터 분류 완료: 총 %d건 (%s)", total, counts)
        return result

    except Exception as e:
        logger.warning("AI 뉴스 분류 실패, 폴백 사용: %s", e)
        fallback = {s: [] for s in _SECTOR_NAMES}
        fallback[_SECTOR_NAMES[0]] = all_news
        return fallback


def analyze_market(
    sector_news: Dict[str, List[dict]],
    stocks: dict,
    today_str: Optional[str] = None,
) -> Optional[str]:
    """섹터별 뉴스와 지수 데이터로 시장을 분석합니다.

    GEMINI_API_KEY 환경 변수가 없으면 None을 반환합니다.

    Args:
        sector_news: classify_news_by_sector 반환값 (섹터별 뉴스 딕셔너리)
        stocks: 주식 지수 딕셔너리
        today_str: KST 기준 날짜 문자열 (없으면 자동 생성)

    Returns:
        AI 분석 결과 문자열, 또는 분석 불가 시 None
    """
    if today_str is None:
        today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")

    client = _get_gemini_client()
    if client is None:
        return None

    def _news_text(news_list: list) -> str:
        if not news_list:
            return "(관련 뉴스 없음)"
        return "\n".join(
            f"- {'[해외] ' if item.get('is_foreign') else '[국내] '}{item['title']} ({item.get('source', '')})"
            for item in news_list
        )

    # 섹터별 뉴스 섹션 (config에서 동적 생성)
    news_sections = "\n\n".join(
        f"[{s['name']} 뉴스]\n{_news_text(sector_news.get(s['name'], []))}"
        for s in SECTORS
    )

    # 출력 형식의 종목 예시 (config에서 동적 생성)
    sector_format = "\n".join(
        f'{s["name"]}: 기업명 [거래소] — 선정 이유  (없으면 "없음")'
        for s in SECTORS
    )

    name_map = {
        "KOSPI": "코스피",
        "KOSDAQ": "코스닥",
        "SP500": "S&P 500",
        "NASDAQ": "나스닥",
        "DOW": "다우존스",
    }
    stocks_text = "\n".join(
        f"- {name_map.get(k, k)}: {v['current']:,.2f} "
        f"({'+'if v['change'] >= 0 else ''}{v['change_pct']:.2f}%)"
        for k, v in stocks.items()
    )

    prompt = f"""오늘 날짜는 {today_str}입니다.

당신은 한국 주식 시장 분석 전문가입니다.
아래 데이터를 바탕으로 시장을 분석해주세요.

[절대 규칙 — 반드시 준수]
- "안녕하세요", "공유드립니다", "살펴보겠습니다", "마치겠습니다" 등
  인사말·서문·결론 문구 절대 사용 금지
- 출력 형식의 첫 줄 "■ 핵심 이슈"로 바로 시작할 것
- 투자 권유 표현 사용 금지

[출력 형식 — 이 형식 그대로 출력]
■ 핵심 이슈
(오늘의 핵심 경제 이슈 2~3줄 요약)

■ 시장 방향 예상
(단기 시장 방향 예상 1~2줄)

■ 섹터별 주목 종목
{sector_format}

[종목 선정 규칙]
- 주도기업 우선 (해당 이슈를 실질적으로 이끄는 기업)
- 주도기업 불명확 시 해당 섹터 시가총액 상위 기업 순
- AI반도체는 글로벌 기업(엔비디아, TSMC, ASML 등) 또는 국내 반도체 기업(삼성전자, SK하이닉스 등) 중심
- 거래소 표기 필수: [KOSPI], [KOSDAQ], [NASDAQ], [NYSE]
- 반드시 모든 섹터를 표기할 것. 추천할 종목이 없으면 "없음"으로 표기 (섹터 줄 자체 생략 금지)

{news_sections}

[주요 지수]
{stocks_text}
"""

    try:
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        result = response.text.strip()
        logger.info("Gemini 시장 분석 완료 (%d자)", len(result))
        return result
    except Exception as e:
        logger.warning("Gemini 시장 분석 실패: %s", e)
        return None
