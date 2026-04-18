"""Google Gemini AI를 이용한 뉴스 선별·분류·번역 및 시장 분석 모듈."""

import json
import logging
import os
import re
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

REQUIRED_SECTIONS_MORNING = ["■ 핵심 이슈", "■ 시장 방향 예상", "■ 섹터별 주목 종목"]
REQUIRED_SECTIONS_EVENING = ["■ 핵심 이슈", "■ 미국 시장 방향 예상", "■ 섹터별 주목 종목 (프리마켓)"]


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


def _make_gen_config():
    """텍스트 분석용 generation_config 객체를 생성합니다. 패키지 문제 시 None 반환.

    gemini-2.5-flash는 thinking 모델이라 thinking 토큰이 output 한도를 소비합니다.
    분석 결과가 잘리지 않도록 thinking을 비활성화하고 출력 한도를 충분히 설정합니다.
    """
    try:
        from google.genai import types as genai_types
        return genai_types.GenerateContentConfig(
            max_output_tokens=4096,
            temperature=0.3,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        )
    except Exception:
        return None


def _make_json_gen_config():
    """JSON 응답 강제 generation_config 객체를 생성합니다.

    gemini-2.5-flash는 thinking 모델이라 thinking 토큰이 output 한도를 소비합니다.
    JSON 분류 작업에서는 thinking을 비활성화하여 응답 잘림을 방지합니다.
    """
    try:
        from google.genai import types as genai_types
        return genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            max_output_tokens=8192,
            temperature=0.1,
        )
    except Exception:
        return None


def _parse_json_response(raw: str):
    """Gemini 응답에서 JSON을 파싱합니다.

    코드블록 래핑, JS 주석, 트레일링 콤마, 앞뒤 불필요한 텍스트 및
    Extra data 오류를 자동 처리합니다.
    """
    raw = raw.strip()

    # 코드블록 제거
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{") or candidate.startswith("["):
                raw = candidate
                break

    raw = raw.strip()

    # JavaScript 스타일 주석 제거
    raw = re.sub(r"//[^\n]*", "", raw)
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)

    # 트레일링 콤마 제거: ,} 또는 ,]
    raw = re.sub(r",\s*([\}\]])", r"\1", raw)
    raw = raw.strip()

    # 첫 번째 완전한 JSON 객체/배열만 추출 (Extra data 방지)
    start_char = raw[0] if raw else ""
    if start_char in ("{", "["):
        end_char = "}" if start_char == "{" else "]"
        depth = 0
        in_string = False
        escape = False
        end_idx = len(raw)
        for i, ch in enumerate(raw):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == start_char:
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break
        raw = raw[:end_idx]

    return json.loads(raw)


def _fill_missing_sections(text: str, required: list) -> str:
    """누락된 필수 섹션을 '정보 없음'으로 채웁니다."""
    for section in required:
        if section not in text:
            text += f"\n\n{section}\n정보 없음"
    return text


def select_and_classify_news(
    domestic_news: list,
    foreign_news: list,
    today_str: str,
    domestic_limit: int = 5,
    foreign_limit: int = 4,
) -> Dict[str, List[dict]]:
    """시장 영향도 기준으로 국내·해외 뉴스를 선별하고 섹터별로 분류합니다.

    해외 뉴스 제목은 한국어로 번역합니다.
    GEMINI_API_KEY 없으면 빈 딕셔너리를 반환합니다.

    Args:
        domestic_news: 국내 뉴스 후보 리스트
        foreign_news: 해외 뉴스 후보 리스트
        today_str: KST 기준 날짜 문자열
        domestic_limit: 선별할 국내 뉴스 수
        foreign_limit: 선별할 해외 뉴스 수

    Returns:
        {섹터명: [뉴스 딕셔너리, ...], ...} — API 키 없으면 빈 dict
    """
    empty_result = {s: [] for s in _SECTOR_NAMES}

    client = _get_gemini_client()
    if client is None:
        return empty_result

    # 프롬프트용 섹터 정의 문자열
    sector_defs = "\n".join(f"- {s['name']}: {s['keywords']}" for s in SECTORS)

    # JSON 응답 예시 (동적 생성)
    json_example = (
        "{\n"
        + ",\n".join(
            f'  "{s}": [{{"index": 0, "is_foreign": false, "translated_title": "제목 예시", "source": "출처"}}]'
            for s in _SECTOR_NAMES
        )
        + "\n}"
    )

    domestic_for_prompt = [
        {"index": i, "title": n["title"], "source": n.get("source", "")}
        for i, n in enumerate(domestic_news)
    ]
    foreign_for_prompt = [
        {"index": i, "title": n["title"], "source": n.get("source", "")}
        for i, n in enumerate(foreign_news)
    ]

    count_rule = ""
    if domestic_limit > 0:
        count_rule += f"- 국내 뉴스: 시장 영향도 상위 {domestic_limit}개 선택\n"
    if foreign_limit > 0:
        count_rule += f"- 해외 뉴스: 시장 영향도 상위 {foreign_limit}개 선택\n"

    prompt = f"""오늘 날짜는 {today_str}입니다.

당신은 주식 시장 전문 편집자입니다.
아래 국내·해외 뉴스 후보에서 오늘 주식시장에 가장 큰 영향을 줄 뉴스를 선별하고 섹터별로 분류해주세요.

[선별 규칙]
{count_rule}- 각 섹터({' · '.join(_SECTOR_NAMES)})에 최소 1개 이상 배분되도록 선택
- 어느 섹터에도 해당하지 않는 뉴스는 선택하지 않음
- 해외 뉴스 제목은 자연스러운 한국어로 번역

[섹터 정의]
{sector_defs}

[국내 뉴스 후보]
{json.dumps(domestic_for_prompt, ensure_ascii=False)}

[해외 뉴스 후보]
{json.dumps(foreign_for_prompt, ensure_ascii=False)}

JSON 형식으로만 응답하고 다른 설명은 절대 추가하지 마세요.
국내 뉴스는 is_foreign: false, 해외 뉴스는 is_foreign: true로 표기하세요:
{json_example}"""

    try:
        json_config = _make_json_gen_config()
        kwargs = {"model": MODEL_NAME, "contents": prompt}
        if json_config:
            kwargs["config"] = json_config
        response = client.models.generate_content(**kwargs)
        classified = _parse_json_response(response.text)

        result = {s: [] for s in _SECTOR_NAMES}
        for sector_name in _SECTOR_NAMES:
            for item in classified.get(sector_name, []):
                idx = item.get("index")
                is_foreign = item.get("is_foreign", False)
                source_list = foreign_news if is_foreign else domestic_news
                if idx is None or idx >= len(source_list):
                    continue
                news_item = source_list[idx].copy()
                news_item["title"] = item.get("translated_title", news_item["title"])
                news_item["is_foreign"] = is_foreign
                result[sector_name].append(news_item)

        counts = ", ".join(f"{s} {len(result[s])}" for s in _SECTOR_NAMES)
        total = sum(len(v) for v in result.values())
        logger.info("AI 뉴스 선별·분류 완료: 총 %d건 (%s)", total, counts)
        return result

    except Exception as e:
        logger.warning("AI 뉴스 선별·분류 실패: %s", e)
        try:
            logger.debug("Gemini 원시 응답 (첫 500자): %s", response.text[:500])
        except Exception:
            pass
        return empty_result


def analyze_market(
    sector_news: Dict[str, List[dict]],
    stocks: dict,
    today_str: Optional[str] = None,
    message_type: str = "morning",
) -> Optional[str]:
    """섹터별 뉴스와 지수 데이터로 시장을 분석합니다.

    필수 섹션이 누락되면 1회 재시도 후 '정보 없음'으로 채워 반환합니다.
    GEMINI_API_KEY 환경 변수가 없으면 None을 반환합니다.

    Args:
        sector_news: select_and_classify_news 반환값 (섹터별 뉴스 딕셔너리)
        stocks: 오전=지수 딕셔너리, 오후=선물 딕셔너리
        today_str: KST 기준 날짜 문자열 (없으면 자동 생성)
        message_type: "morning" 또는 "evening"

    Returns:
        AI 분석 결과 문자열, 또는 분석 불가 시 None
    """
    if today_str is None:
        today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")

    client = _get_gemini_client()
    if client is None:
        return None

    required_sections = (
        REQUIRED_SECTIONS_MORNING if message_type == "morning"
        else REQUIRED_SECTIONS_EVENING
    )
    prompt = (
        _build_morning_prompt(sector_news, stocks, today_str)
        if message_type == "morning"
        else _build_evening_prompt(sector_news, stocks, today_str)
    )

    gen_config = _make_gen_config()
    kwargs = {"model": MODEL_NAME, "contents": prompt}
    if gen_config:
        kwargs["config"] = gen_config

    result = None
    for attempt in range(2):
        try:
            response = client.models.generate_content(**kwargs)
            result = response.text.strip()
            missing = [s for s in required_sections if s not in result]
            if not missing:
                logger.info("Gemini 시장 분석 완료 (%d자)", len(result))
                return result
            logger.warning("[시도 %d] 누락된 섹션: %s. 재시도합니다.", attempt + 1, missing)
        except Exception as e:
            logger.error("[시도 %d] Gemini 호출 오류: %s", attempt + 1, e)

    logger.error("Gemini 분석 2회 모두 실패. 가용 내용으로 전송합니다.")
    if result:
        return _fill_missing_sections(result, required_sections)
    return None


def _news_text_for_prompt(news_list: list) -> str:
    """뉴스 리스트를 프롬프트용 텍스트로 변환합니다."""
    if not news_list:
        return "(관련 뉴스 없음)"
    return "\n".join(
        f"- {'[해외] ' if item.get('is_foreign') else '[국내] '}{item['title']} ({item.get('source', '')})"
        for item in news_list
    )


def _build_morning_prompt(sector_news: dict, stocks: dict, today_str: str) -> str:
    """오전 시장 분석 프롬프트를 생성합니다."""
    news_sections = "\n\n".join(
        f"[{s['name']} 뉴스]\n{_news_text_for_prompt(sector_news.get(s['name'], []))}"
        for s in SECTORS
    )
    sector_format = "\n".join(
        f'{s["name"]}: 기업명 [거래소] — 선정 이유  (없으면 "없음")'
        for s in SECTORS
    )
    name_map = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "SP500": "S&P 500", "NASDAQ": "나스닥", "DOW": "다우존스"}
    stocks_text = "\n".join(
        f"- {name_map.get(k, k)}: {v['current']:,.2f} ({'+'if v['change'] >= 0 else ''}{v['change_pct']:.2f}%)"
        for k, v in stocks.items()
    )

    return f"""오늘 날짜는 {today_str}입니다.

당신은 한국 주식 시장 분석 전문가입니다.
아래 데이터를 바탕으로 시장을 분석해주세요.

[절대 규칙]
- 인사말·서문·결론 문구 절대 사용 금지
- 반드시 아래 3개 섹션을 모두 포함할 것:
    ■ 핵심 이슈
    ■ 시장 방향 예상
    ■ 섹터별 주목 종목
- 각 섹션 제목은 정확히 위 표기대로 사용
- 3개 섹션 중 하나라도 누락하면 응답이 유효하지 않음
- 투자 권유 표현 사용 금지

[출력 형식]
■ 핵심 이슈
(오늘의 핵심 경제 이슈 2~3줄 요약)

■ 시장 방향 예상
(단기 시장 방향 예상 1~2줄)

■ 섹터별 주목 종목
{sector_format}

[종목 선정 규칙]
- 주도기업 우선, 불명확 시 시가총액 상위 기업 순
- AI반도체는 글로벌 기업(엔비디아, TSMC, ASML 등) 또는 국내 반도체 기업(삼성전자, SK하이닉스 등) 중심
- 거래소 표기 필수: [KOSPI], [KOSDAQ], [NASDAQ], [NYSE]
- 반드시 모든 섹터를 표기할 것. 추천할 종목이 없으면 "없음" (섹터 줄 자체 생략 금지)

{news_sections}

[주요 지수]
{stocks_text}"""


def _build_evening_prompt(sector_news: dict, futures: dict, today_str: str) -> str:
    """오후 미국 시장 프리뷰 분석 프롬프트를 생성합니다."""
    sector_news_blocks = "\n\n".join(
        f"[{s['name']} 해외 뉴스]\n{_news_text_for_prompt(sector_news.get(s['name'], []))}"
        for s in SECTORS
    )
    sector_format = "\n".join(
        f'{s["name"]}: 기업명 [거래소] — 선정 이유  (이슈 없으면 "없음")'
        for s in SECTORS
    )
    futures_text = "\n".join(
        f"- {name}: {data['price']:,.2f} ({'+'if data['change'] >= 0 else ''}{data['pct']:.2f}%)"
        for name, data in futures.items()
    ) or "(데이터 없음)"

    return f"""오늘 날짜는 {today_str}입니다.

당신은 미국 주식 시장 분석 전문가입니다.
아래 해외 뉴스와 선물 지수 데이터를 바탕으로 오늘 밤 미국 시장을 분석해주세요.

[절대 규칙]
- 인사말·서문·결론 문구 절대 사용 금지
- 반드시 아래 3개 섹션을 모두 포함할 것:
    ■ 핵심 이슈
    ■ 미국 시장 방향 예상
    ■ 섹터별 주목 종목 (프리마켓)
- 각 섹션 제목은 정확히 위 표기대로 사용
- 3개 섹션 중 하나라도 누락하면 응답이 유효하지 않음
- 투자 권유 표현 사용 금지

[출력 형식]
■ 핵심 이슈
(해외 뉴스 기반 오늘 밤 핵심 이슈 2~3줄)

■ 미국 시장 방향 예상
(오늘 밤 미국 장 방향 예상 1~2줄)

■ 섹터별 주목 종목 (프리마켓)
{sector_format}

[종목 선정 규칙]
- 주도기업 우선, 불명확 시 시가총액 상위 기업 순
- AI반도체는 글로벌 기업(엔비디아, TSMC, ASML 등) 중심
- 거래소 표기 필수: [NASDAQ], [NYSE], [KOSPI], [KOSDAQ]
- 반드시 모든 섹터를 표기할 것. 이슈 없으면 "없음" (섹터 줄 자체 생략 금지)

{sector_news_blocks}

[미국 선물 지수]
{futures_text}"""
