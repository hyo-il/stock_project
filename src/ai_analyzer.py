"""Google Gemini AI를 이용한 오전 브리핑 분석 모듈.

단일 Gemini 호출로 아래 항목을 JSON으로 생성합니다:
  - 시장 기조 (Risk-On / Risk-Off / 혼조)
  - 포트폴리오 참고 한줄
  - 핵심 이슈 3선 (스윙 트레이딩 관점 영향 포함)
  - 오늘의 주도 섹터 (AI 자유 선정, 고정 섹터 없음)
  - 스윙 트레이딩 체크포인트
  - 이번 주 주요 경제 일정
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
MODEL_NAME = "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Gemini 클라이언트 / 설정 헬퍼
# ---------------------------------------------------------------------------

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


def _make_json_gen_config():
    """JSON 응답 강제 generation_config.

    gemini-2.5-flash는 thinking 모델 → thinking_budget=0 필수.
    미설정 시 thinking 토큰이 output 한도를 소비하여 응답이 잘림.
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

    코드블록 래핑, JS 주석, 트레일링 콤마, Extra data 오류를 자동 처리합니다.
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


# ---------------------------------------------------------------------------
# 메인 분석 함수
# ---------------------------------------------------------------------------

def build_morning_briefing(
    domestic_news: list,
    foreign_news: list,
    stocks: dict,
    today_str: Optional[str] = None,
) -> Optional[dict]:
    """오전 브리핑 전체를 단일 Gemini 호출로 분석합니다.

    Args:
        domestic_news: 국내 뉴스 리스트 (news_collector 반환값)
        foreign_news:  해외 뉴스 리스트 (news_collector 반환값)
        stocks:        collect_morning_stocks() 반환값
        today_str:     KST 기준 날짜 문자열 (없으면 자동 생성)

    Returns:
        분석 결과 dict. 키:
            market_regime   : "Risk-On" | "Risk-Off" | "혼조"
            regime_summary  : 기조 요약 1~2문장
            portfolio_note  : 패시브 포트폴리오 한줄 참고
            key_issues      : [{"icon","category","title","impact"}, ...]  3개
            leading_sectors : [{"emoji","name","stars","reason","stocks_kr","stocks_us"}, ...]  2~3개
            swing_check     : {"phase", "catalysts": [...], "risks": [...]}
            weekly_schedule : [{"date","event","importance"}, ...]  3~5개
        실패 시 None 반환.
    """
    if today_str is None:
        today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")

    client = _get_gemini_client()
    if client is None:
        return None

    # ── 지수/자산 데이터 텍스트 ──────────────────────────────────────────
    name_map = {
        "KOSPI": "코스피", "KOSDAQ": "코스닥",
        "SP500": "S&P500", "NASDAQ": "나스닥", "DOW": "다우존스",
        "GOLD": "금(달러/온스)", "DXY": "달러인덱스",
        "US10Y": "미10년물금리(%)", "USDKRW": "원/달러",
    }
    index_keys = ["KOSPI", "KOSDAQ", "SP500", "NASDAQ", "DOW"]
    macro_keys = ["GOLD", "DXY", "US10Y", "USDKRW"]

    def _fmt(k, v):
        sign = "+" if v["change"] >= 0 else ""
        return f"  {name_map.get(k, k)}: {v['current']:,.4f} ({sign}{v['change_pct']:.2f}%)"

    indices_text = "\n".join(_fmt(k, stocks[k]) for k in index_keys if k in stocks)
    macro_text   = "\n".join(_fmt(k, stocks[k]) for k in macro_keys if k in stocks)

    # ── 뉴스 텍스트 ─────────────────────────────────────────────────────
    domestic_text = "\n".join(
        f"[국내] {n['title']} ({n.get('source', '')})"
        for n in domestic_news[:30]
    ) or "(국내 뉴스 없음)"

    foreign_text = "\n".join(
        f"[해외] {n['title']} ({n.get('source', '')})"
        for n in foreign_news[:40]
    ) or "(해외 뉴스 없음)"

    # ── 프롬프트 ────────────────────────────────────────────────────────
    prompt = f"""오늘 날짜는 {today_str}입니다.

당신은 10년 이상 경력의 한국 주식 시장 전문 애널리스트입니다.
아래 시장 데이터와 뉴스를 종합하여 오전 투자 브리핑을 작성하세요.

[투자자 프로필]
- 패시브 포트폴리오: S&P500 ETF 55%, 미국배당다우존스 ETF 25%, 국고채10년 ETF 10%, 금 ETF 10% (장기 분기 리밸런싱)
- 스윙 트레이딩: 국내(KOSPI 대형주) + 미국(S&P500 상위) 대상, 1~3개월 보유

[주요 지수 (전일 종가)]
{indices_text or "  (데이터 없음)"}

[매크로 자산 (전일 종가)]
{macro_text or "  (데이터 없음)"}

[오늘 국내 뉴스]
{domestic_text}

[오늘 해외 뉴스]
{foreign_text}

아래 JSON 스키마를 정확히 따라 응답하세요. JSON 이외의 텍스트는 절대 포함하지 마세요:

{{
  "market_regime": "Risk-On 또는 Risk-Off 또는 혼조",
  "regime_summary": "오늘 시장 기조를 1~2문장으로 요약",
  "portfolio_note": "패시브 포트폴리오 4개 자산 중 오늘 특이 동향 한줄 (예: 금 ETF 강세 유지 / 채권 관망)",
  "key_issues": [
    {{
      "icon": "🔴 또는 🟡 또는 🟢 (🔴=하락 리스크, 🟡=중립/혼조, 🟢=상승 모멘텀)",
      "category": "분류 (예: 지정학, 통화정책, 실적, 무역, 경제지표, 에너지, 기술)",
      "title": "이슈 제목 (한국어, 간결하게)",
      "impact": "스윙 트레이딩 관점 영향 1문장 (어떤 섹터/종목에 어떤 영향)"
    }}
  ],
  "leading_sectors": [
    {{
      "emoji": "섹터 특성에 맞는 이모지",
      "name": "섹터명",
      "stars": "★★★ 또는 ★★☆ 또는 ★☆☆",
      "reason": "오늘 이 섹터가 주도하는 근거 (뉴스 기반, 1~2문장)",
      "stocks_kr": "국내 주목 대형주 종목명 (없으면 빈 문자열)",
      "stocks_us": "미국 주목 대형주 종목명 (없으면 빈 문자열)"
    }}
  ],
  "swing_check": {{
    "phase": "현재 시장 국면 한줄 (예: 하락 추세 속 기술적 반등 시도)",
    "catalysts": ["향후 1~2주 내 주요 촉매제 (날짜 포함)", "..."],
    "risks": ["주요 하방 리스크", "..."]
  }},
  "weekly_schedule": [
    {{
      "date": "MM/DD(요일)",
      "event": "경제지표·실적·정책회의 등 일정명",
      "importance": 1
    }}
  ]
}}

[작성 규칙]
- key_issues: 정확히 3개, 시장 영향력 큰 순서로 배열
- leading_sectors: 2~3개, 오늘 뉴스에서 실제 움직임이 확인되는 섹터만 선정 (고정 섹터 없음)
- weekly_schedule: 오늘 이후 이번 주 남은 날짜 기준 3~5개, importance는 1(일반)·2(중요)·3(매우중요)
- 투자 권유 표현 절대 금지 ("매수하세요", "추천합니다" 등)
- 인사말·서문·결론 문구 금지"""

    # ── Gemini 호출 (최대 2회 시도) ─────────────────────────────────────
    json_config = _make_json_gen_config()
    kwargs = {"model": MODEL_NAME, "contents": prompt}
    if json_config:
        kwargs["config"] = json_config

    last_response_text = None
    for attempt in range(2):
        try:
            response = client.models.generate_content(**kwargs)
            last_response_text = response.text
            result = _parse_json_response(response.text)

            # 필수 키 검증
            required = ["market_regime", "key_issues", "leading_sectors", "swing_check", "weekly_schedule"]
            missing = [k for k in required if k not in result]
            if missing:
                logger.warning("[시도 %d] 누락된 키: %s. 재시도합니다.", attempt + 1, missing)
                continue

            logger.info("오전 브리핑 AI 분석 완료 (%d자)", len(response.text))
            return result

        except Exception as e:
            logger.error("[시도 %d] Gemini 오전 브리핑 분석 실패: %s", attempt + 1, e)
            if last_response_text:
                logger.debug("응답 원시 텍스트 (첫 500자): %s", last_response_text[:500])

    logger.error("Gemini 분석 2회 모두 실패.")
    return None
