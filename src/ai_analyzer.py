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
    earnings: Optional[list] = None,
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
            key_issues      : [{"icon","category","title","why_important","swing_point"}, ...]  3개
            leading_sectors : [{"emoji","name","stars","reason","stocks_kr","stocks_us"}, ...]  2~3개
            swing_check     : {"phase", "catalysts": [...], "risks": [...]}
            weekly_schedule : [{"date","event","detail","importance"}, ...]  3~5개
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
    index_keys = ["SP500", "NASDAQ", "DOW"]
    macro_keys = ["GOLD", "DXY", "US10Y", "USDKRW"]

    def _fmt(k, v):
        sign = "+" if v["change"] >= 0 else ""
        date_str = v.get("data_date", "")
        date_suffix = f" [{date_str} 종가]" if date_str else ""
        return f"  {name_map.get(k, k)}: {v['current']:,.4f} ({sign}{v['change_pct']:.2f}%){date_suffix}"

    indices_text = "\n".join(_fmt(k, stocks[k]) for k in index_keys if k in stocks)
    macro_text   = "\n".join(_fmt(k, stocks[k]) for k in macro_keys if k in stocks)

    # ── 실적 발표 텍스트 ─────────────────────────────────────────────────
    if earnings:
        earnings_lines = [
            f"  {e['name_kr']}({e['ticker']}) — {e['earnings_date']}"
            f"{' EPS추정 ' + e['eps_estimate'] if e['eps_estimate'] != '-' else ''}"
            for e in earnings
        ]
        earnings_text = "\n".join(earnings_lines)
    else:
        earnings_text = "(실적 발표 예정 없음 또는 수집 실패)"

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

[이번 주 주요 기업 실적 발표 (yfinance 실제 데이터)]
{earnings_text}

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
      "category": "분류 (예: 실적, 지정학, 통화정책, 무역, 경제지표, 에너지, 기술)",
      "title": "이슈 제목 (한국어, 간결하게)",
      "why_important": "왜 중요한지 — 투자 초보자도 이해할 수 있는 1문장",
      "swing_point": "스윙 트레이딩 관점 포인트 — 어떤 섹터/종목에 어떤 영향인지 구체적으로"
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
      "detail": "기업명+예상EPS/이전값 등 구체 정보 (없으면 빈 문자열)",
      "importance": 1
    }}
  ]
}}

[작성 규칙]
- 데이터 기준일 인식: 위 [주요 지수]·[매크로 자산]에 표시된 [MM/DD(요일) 종가] 라벨을 반드시 인식하고,
  현재 시점(브리핑 작성 시각)이 해당 종가 이후임을 전제로 어조를 조정하세요.
  예: "강세를 보이고 있습니다" → "금요일 강세 마감", "오르는 중입니다" → "전 거래일 상승 마감".
  미국 지수가 금요일 종가라면 "주말 휴장 후 월요일 개장 주목" 식으로 표현 가능.
- key_issues: 정확히 3개, 시장 영향력 큰 순서로 배열.
  ★ 실적 발표 뉴스(개별 기업 어닝/가이던스)는 최우선으로 1번 또는 2번 슬롯에 배치.
  why_important는 초보자도 이해할 수 있게 평이한 표현 사용.
  금융 전문용어가 불가피하면 괄호로 짧게 풀이 병기 (예: "FOMC(미 연준 통화정책 회의)", "EPS(주당순이익)").
  swing_point는 "어떤 섹터의 어떤 종목군이 수혜/타격"인지 구체적으로 명시.
- leading_sectors: 2~3개, 오늘 뉴스에서 실제 움직임이 확인되는 섹터만 선정 (고정 섹터 없음).
  ★ 1~2주 단기 이벤트(행사, 컨퍼런스, 단기 테마)나 계절성 요인만으로 섹터를 선정하지 말 것.
  ★ 실적(어닝), 정책 변화, 금리·환율 등 최소 3개월 이상 지속 가능한 펀더멘털 근거가 있는 섹터만 선정.
- weekly_schedule: 오늘 이후 이번 주 남은 날짜 기준 3~5개, importance는 1(일반)·2(중요)·3(매우중요).
  ★ [이번 주 주요 기업 실적 발표] 섹션에 제공된 실제 데이터를 최우선으로 반영할 것.
    반드시 해당 기업명·날짜·EPS 추정치를 그대로 사용하고 importance=3으로 설정.
  ★ 공휴일, 시장 개장/휴장 안내, 연준 위원 연설 일정 등 투자 판단에 직접 영향이 없는 항목은 제외.
  ★ 포함 대상: 주요 경제지표(CPI, PCE, 고용보고서, GDP 등), 연준 FOMC 회의, 주요 기업 실적 발표.
  실적 발표 일정은 detail에 "EPS추정 $X.XX" 형식으로 포함. 알 수 없으면 빈 문자열.
  지표 발표는 detail에 "예상치 X.X% vs 이전 Y.Y%" 식 포함. 알 수 없으면 빈 문자열.
- 투자 권유 표현 절대 금지 ("매수하세요", "추천합니다" 등).
- 인사말·서문·결론 문구 금지."""

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


def build_afternoon_briefing(
    domestic_news: list,
    stocks: dict,
    earnings: Optional[list] = None,
    today_str: Optional[str] = None,
) -> Optional[dict]:
    """오후 브리핑 전체를 단일 Gemini 호출로 분석합니다.

    오후 브리핑은 당일 국내 장 마감 결과를 중심으로 분석합니다.
    미국 지수는 전일 종가 참고용으로만 사용합니다.

    Args:
        domestic_news: 국내 뉴스 리스트
        stocks:        collect_afternoon_stocks() 반환값 (KOSPI, KOSDAQ, VIX)
        earnings:      collect_upcoming_earnings() 반환값 (선택)
        today_str:     KST 기준 날짜 문자열 (없으면 자동 생성)

    Returns:
        분석 결과 dict. 키:
            market_summary   : 오늘 국내 시장 총평 (1~2문장)
            vix_comment      : VIX 수준 해설 (1문장)
            portfolio_note   : 패시브 포트폴리오 한줄 참고
            key_issues       : [{"icon","category","title","why_important","swing_point"}, ...]  3개
            leading_sectors  : [{"emoji","name","stars","reason","stocks_kr","stocks_us"}, ...]  2~3개
            swing_check      : {"phase", "catalysts": [...], "risks": [...]}
            weekly_schedule  : [{"date","event","detail","importance"}, ...]  3~5개
        실패 시 None 반환.
    """
    if today_str is None:
        today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")

    client = _get_gemini_client()
    if client is None:
        return None

    # ── 지수 데이터 텍스트 ──────────────────────────────────────────────
    name_map = {
        "KOSPI": "코스피", "KOSDAQ": "코스닥",
        "VIX":   "VIX 공포지수",
    }

    def _fmt(k, v):
        sign = "+" if v["change"] >= 0 else ""
        date_str = v.get("data_date", "")
        date_suffix = f" [{date_str} 종가]" if date_str else ""
        return f"  {name_map.get(k, k)}: {v['current']:,.4f} ({sign}{v['change_pct']:.2f}%){date_suffix}"

    kr_text  = "\n".join(_fmt(k, stocks[k]) for k in ["KOSPI", "KOSDAQ"] if k in stocks)
    vix_info = stocks.get("VIX", {})

    vix_level = ""
    if vix_info:
        vix_val = vix_info.get("current", 0)
        if vix_val >= 30:
            vix_level = f"  VIX: {vix_val:.2f} — 공포 구간 (30 이상)"
        elif vix_val >= 20:
            vix_level = f"  VIX: {vix_val:.2f} — 주의 구간 (20~30)"
        else:
            vix_level = f"  VIX: {vix_val:.2f} — 안정 구간 (20 미만)"

    # ── 실적 텍스트 ─────────────────────────────────────────────────────
    if earnings:
        earnings_lines = [
            f"  {e['name_kr']}({e['ticker']}) — {e['earnings_date']}"
            f"{' EPS추정 ' + e['eps_estimate'] if e['eps_estimate'] != '-' else ''}"
            for e in earnings
        ]
        earnings_text = "\n".join(earnings_lines)
    else:
        earnings_text = "(실적 발표 예정 없음 또는 수집 실패)"

    # ── 뉴스 텍스트 ─────────────────────────────────────────────────────
    domestic_text = "\n".join(
        f"[국내] {n['title']} ({n.get('source', '')})"
        for n in domestic_news[:30]
    ) or "(국내 뉴스 없음)"

    # ── 프롬프트 ────────────────────────────────────────────────────────
    prompt = f"""오늘 날짜는 {today_str}입니다.

당신은 10년 이상 경력의 한국 주식 시장 전문 애널리스트입니다.
오늘 국내 장 마감 데이터와 뉴스를 분석하여 오후 투자 브리핑을 작성하세요.

[투자자 프로필]
- 패시브 포트폴리오: S&P500 ETF 55%, 미국배당다우존스 ETF 25%, 국고채10년 ETF 10%, 금 ETF 10% (장기 분기 리밸런싱)
- 스윙 트레이딩: 국내(KOSPI 대형주) + 미국(S&P500 상위) 대상, 1~3개월 보유

[오늘 국내 지수 (당일 마감)]
{kr_text or "  (데이터 없음)"}

[시장 공포 지수]
{vix_level or "  (데이터 없음)"}

[이번 주 주요 기업 실적 발표 (yfinance 실제 데이터)]
{earnings_text}

[오늘 국내 뉴스]
{domestic_text}

아래 JSON 스키마를 정확히 따라 응답하세요. JSON 이외의 텍스트는 절대 포함하지 마세요:

{{
  "market_summary": "오늘 국내 시장(코스피·코스닥) 마감 총평 1~2문장",
  "vix_comment": "현재 VIX 수준이 스윙 트레이딩에 갖는 의미 1문장",
  "portfolio_note": "패시브 포트폴리오 4개 자산 중 오늘 주목할 동향 한줄",
  "key_issues": [
    {{
      "icon": "🔴 또는 🟡 또는 🟢",
      "category": "분류 (예: 실적, 지정학, 통화정책, 무역, 경제지표, 에너지, 기술)",
      "title": "이슈 제목 (한국어, 간결하게)",
      "why_important": "왜 중요한지 — 투자 초보자도 이해할 수 있는 1문장. 전문용어는 괄호로 풀이 병기",
      "swing_point": "스윙 트레이딩 관점 — 어떤 섹터/종목군에 어떤 영향인지 구체적으로"
    }}
  ],
  "leading_sectors": [
    {{
      "emoji": "섹터 특성에 맞는 이모지",
      "name": "섹터명",
      "stars": "★★★ 또는 ★★☆ 또는 ★☆☆",
      "reason": "오늘 이 섹터가 주목받는 근거 (뉴스/실적 기반, 1~2문장)",
      "stocks_kr": "국내 주목 대형주 종목명 (없으면 빈 문자열)",
      "stocks_us": "미국 주목 대형주 종목명 (없으면 빈 문자열)"
    }}
  ],
  "swing_check": {{
    "phase": "현재 국내 시장 국면 한줄",
    "catalysts": ["향후 1~2주 내 주요 촉매제 (날짜 포함)", "..."],
    "risks": ["주요 하방 리스크", "..."]
  }},
  "weekly_schedule": [
    {{
      "date": "MM/DD(요일)",
      "event": "경제지표·실적·정책회의 등 일정명",
      "detail": "구체 정보 (없으면 빈 문자열)",
      "importance": 1
    }}
  ]
}}

[작성 규칙]
- market_summary: 오늘 코스피·코스닥 등락과 주요 원인을 과거형으로 서술.
- vix_comment: VIX 수치({vix_info.get('current', 'N/A')})를 언급하며 안정/주의/공포 여부와 스윙 트레이딩 시사점 서술.
- key_issues: 정확히 3개. 실적 발표 뉴스는 최우선 1번 슬롯.
  why_important는 초보자도 이해할 수 있게 평이한 표현 사용.
  금융 전문용어 사용 시 괄호로 짧게 풀이 병기 (예: "FOMC(미 연준 통화정책 회의)", "EPS(주당순이익)").
  ROE는 자기자본이익률, EPS는 주당순이익으로 정확히 사용.
- leading_sectors: 2~3개. 1~2주 단기 이벤트나 테마 섹터 선정 금지.
  최소 3개월 이상 지속 가능한 실적·정책·펀더멘털 근거가 있는 섹터만 선정.
- weekly_schedule: 오늘 이후 이번 주 남은 날짜 기준 3~5개.
  ★ [이번 주 주요 기업 실적 발표] 섹션의 실제 데이터를 최우선 반영. importance=3 설정.
  ★ 공휴일, 시장 개장/휴장 안내, 연준 위원 단순 연설은 제외.
  ★ 포함 대상: 주요 경제지표, FOMC 회의, 주요 기업 실적 발표.
  실적 발표는 detail에 "EPS추정 $X.XX" 포함. 경제지표는 "예상치 X.X% vs 이전 Y.Y%".
- 투자 권유 표현 절대 금지.
- 인사말·서문·결론 문구 금지."""

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

            required = ["market_summary", "key_issues", "leading_sectors", "swing_check", "weekly_schedule"]
            missing = [k for k in required if k not in result]
            if missing:
                logger.warning("[시도 %d] 누락된 키: %s. 재시도합니다.", attempt + 1, missing)
                continue

            logger.info("오후 브리핑 AI 분석 완료 (%d자)", len(response.text))
            return result

        except Exception as e:
            logger.error("[시도 %d] Gemini 오후 브리핑 분석 실패: %s", attempt + 1, e)
            if last_response_text:
                logger.debug("응답 원시 텍스트 (첫 500자): %s", last_response_text[:500])

    logger.error("Gemini 오후 브리핑 분석 2회 모두 실패.")
    return None
