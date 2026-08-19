"""Google Gemini AI를 이용한 오전 브리핑 분석 모듈.

단일 Gemini 호출로 아래 항목을 JSON으로 생성합니다:
  - 시장 기조 (Risk-On / Risk-Off / 혼조)
  - 포트폴리오 참고 한줄
  - 핵심 테마 3선 (1~3개월 지속 펀더멘털 테마, duration 포함)
  - 현재 주도 섹터 (AI 자유 선정, 고정 섹터 없음)
  - 스윙 트레이딩 체크포인트
  - 향후 60일 주요 일정 (this_week/this_month/next_2_months)
  - 액션 포인트 (패시브 비중 조정 + 스윙 후보)
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
MODEL_NAME = "gemini-3.6-flash"
MAX_AI_ATTEMPTS = 3  # v1.5.12: 2 → 3 (503 high demand spike 대응)


# ---------------------------------------------------------------------------
# Gemini 클라이언트 / 설정 헬퍼
# ---------------------------------------------------------------------------

def _ai_retry_backoff(attempt: int) -> None:
    """v1.5.12: Gemini 재시도 사이 선형 백오프. 시도1 실패 후 5초, 시도2 실패 후 10초.

    503 (high demand spike) 등 일시 장애 흡수용. 마지막 시도 뒤에는 대기하지 않음.
    Google 공식 권고 "Spikes in demand are usually temporary. Please try again later." 대응.
    """
    if attempt < MAX_AI_ATTEMPTS - 1:
        wait_seconds = 5 * (attempt + 1)
        logger.info("Gemini 재시도 대기 %d초", wait_seconds)
        time.sleep(wait_seconds)


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

    gemini-3.x 는 thinking 토큰이 max_output_tokens 를 잠식하지 않으므로
    thinking_config 를 지정하지 않는다. (2.5 계열의 thinking_budget=0 은
    3.x 에서 INVALID_ARGUMENT 400 을 유발하므로 절대 되살리지 말 것.)
    """
    try:
        from google.genai import types as genai_types
        return genai_types.GenerateContentConfig(
            response_mime_type="application/json",
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


def _filter_upcoming_by_leading_sectors(briefing: dict) -> dict:
    """upcoming_schedule 의 실적 항목 중 leading_sectors 에 속하지 않는 것을 제거합니다.

    v1.5.10 도입. AI 작성 규칙 "주도섹터만 포함" 의 준수 강제용 사후 필터.

    Rules (v1.5.15 갱신):
        - leading_sectors[i].name 과 정확히 일치하는 sector_kr 만 보존
        - sector_kr 이 빈 문자열인 항목(경제지표·FOMC 등) 은 보존
        - 티커가 .KS/.KQ (국내 종목) 이면 leading_sectors 무관 항상 보존
          → 사용자 편입 정책(주의사항 27번) 직접 구현.
        - 그 외 (비주도섹터 실적) 는 제거

    Returns:
        필터링된 briefing dict (원본 mutate).
    """
    if not isinstance(briefing, dict):
        return briefing

    leading_set = set()
    for ls in briefing.get('leading_sectors', []) or []:
        name = (ls.get('name') or '').strip()
        if name:
            leading_set.add(name)

    if not leading_set:
        logger.info("[필터] leading_sectors 비어 있음 — upcoming_schedule 필터 생략")
        return briefing

    us = briefing.get('upcoming_schedule') or {}
    if not isinstance(us, dict):
        return briefing

    removed_total = 0
    for slot in list(us.keys()):
        items = us.get(slot, []) or []
        kept = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ticker = (item.get('ticker') or '').strip()
            sector = (item.get('sector_kr') or '').strip()

            # v1.5.15: 국내 종목 whitelist — 사용자 편입 정책상 항상 보존.
            # EXTENDED_TICKERS 에 편입된 종목은 사용자가 명시 승인한 것이므로
            # leading_sectors 필터 우회.
            if ticker.endswith('.KS') or ticker.endswith('.KQ'):
                kept.append(item)
                continue

            if not sector:
                # 경제지표·FOMC 등 sector_kr 없음 → 보존
                kept.append(item)
            elif sector in leading_set:
                kept.append(item)
            else:
                removed_total += 1
                logger.info("[필터] %s 제거: %s [%s] (주도섹터 외)",
                            slot, item.get('event', ''), sector)
        us[slot] = kept

    if removed_total:
        logger.info("[필터] upcoming_schedule 사후 필터: %d건 제거 (leading=%s)",
                    removed_total, sorted(leading_set))
    return briefing


def _reassign_upcoming_slots_by_days(briefing: dict, today) -> dict:
    """upcoming_schedule 항목을 실제 날짜(MM/DD) 기반 days_until 로 재배치.

    v1.5.16 도입. AI 가 D+N 값을 준수하지 않는 경우 대응하는 안전망.
    각 항목의 date (MM/DD 형식) 를 파싱해 today 대비 며칠 후 계산 후:
        · 0~7일: this_week
        · 8~30일: this_month
        · 31~60일: next_2_months
        · 그 외: this_month 에 폴백 (파싱 실패) 또는 삭제 (>60일)

    Args:
        briefing: AI 응답 dict
        today: KST 기준 오늘 (datetime.date)

    Returns:
        재배치된 briefing (원본 mutate).
    """
    import re
    from datetime import date as _date

    if not isinstance(briefing, dict):
        return briefing

    us = briefing.get('upcoming_schedule') or {}
    if not isinstance(us, dict):
        return briefing

    # 기존 모든 슬롯에서 항목 수집
    all_items = []
    for slot in ['this_week', 'this_month', 'next_2_months']:
        items = us.get(slot, []) or []
        all_items.extend(items)

    def _calc_days(item):
        """item['date'] (MM/DD 형식) → 오늘 대비 며칠 후. 실패 시 None."""
        date_str = item.get('date', '') if isinstance(item, dict) else ''
        m = re.match(r'(\d{1,2})/(\d{1,2})', date_str)
        if not m:
            return None
        mm, dd = int(m.group(1)), int(m.group(2))
        try:
            candidate = _date(today.year, mm, dd)
            # MM 이 오늘보다 이전이면 다음 해로 추정 (연도 경계)
            if candidate < today:
                candidate = _date(today.year + 1, mm, dd)
            return (candidate - today).days
        except ValueError:
            return None

    new_slots = {'this_week': [], 'this_month': [], 'next_2_months': []}
    for item in all_items:
        if not isinstance(item, dict):
            continue
        days = _calc_days(item)
        if days is None:
            # 파싱 실패 시 안전하게 this_month 로 폴백
            new_slots['this_month'].append(item)
            continue
        if 0 <= days <= 7:
            new_slots['this_week'].append(item)
        elif 8 <= days <= 30:
            new_slots['this_month'].append(item)
        elif 31 <= days <= 60:
            new_slots['next_2_months'].append(item)
        # 60일 초과는 삭제 (수집 기간 밖)

    us['this_week'] = new_slots['this_week']
    us['this_month'] = new_slots['this_month']
    us['next_2_months'] = new_slots['next_2_months']

    logger.info(
        "[재배치] this_week=%d, this_month=%d, next_2_months=%d",
        len(new_slots['this_week']),
        len(new_slots['this_month']),
        len(new_slots['next_2_months']),
    )
    return briefing


def _cap_and_sort_by_sector_priority(briefing: dict) -> dict:
    """섹터별 상한 적용 + 주도섹터 별점 순 우선 정렬.

    v1.5.16 도입.
    - 각 슬롯 내에서 (leading_sector 별점 내림차순, 날짜 오름차순) 정렬
    - 섹터별 상한 적용:
        · this_week / this_month: 동일 섹터 최대 3개
        · next_2_months: 동일 섹터 최대 2개
    - 슬롯 총 상한(6/5/5) 도 함께 적용
    - sector_kr 이 빈 문자열(경제지표·FOMC) 은 섹터 상한 적용 없이 보존

    별점 매핑:
        ★★★ = 3, ★★☆ = 2, ★☆☆ = 1 (leading_sectors[i].stars 문자열의 ★ 개수)
        leading_sectors 에 없는 섹터 = 0 (하위 우선순위)
    """
    if not isinstance(briefing, dict):
        return briefing

    us = briefing.get('upcoming_schedule') or {}
    if not isinstance(us, dict):
        return briefing

    # leading_sectors 별점 매핑
    star_map = {}
    for ls in briefing.get('leading_sectors', []) or []:
        name = (ls.get('name') or '').strip()
        stars = ls.get('stars', '') or ''
        if name:
            star_map[name] = stars.count('★')

    slot_config = {
        'this_week':     {'sector_cap': 3, 'total_cap': 6},
        'this_month':    {'sector_cap': 3, 'total_cap': 5},
        'next_2_months': {'sector_cap': 2, 'total_cap': 5},
    }

    for slot, cfg in slot_config.items():
        items = us.get(slot, []) or []

        def _sort_key(item):
            sector = (item.get('sector_kr') or '').strip()
            date_str = item.get('date', '')
            priority = star_map.get(sector, 0)
            return (-priority, date_str)

        items_sorted = sorted(
            [i for i in items if isinstance(i, dict)], key=_sort_key
        )

        sector_count = {}
        kept = []
        for item in items_sorted:
            if len(kept) >= cfg['total_cap']:
                break
            sector = (item.get('sector_kr') or '').strip()
            if not sector:
                # 경제지표·FOMC 등은 섹터 상한 미적용
                kept.append(item)
                continue
            count = sector_count.get(sector, 0)
            if count < cfg['sector_cap']:
                kept.append(item)
                sector_count[sector] = count + 1

        us[slot] = kept

    logger.info(
        "[정렬·상한] this_week=%d, this_month=%d, next_2_months=%d",
        len(us.get('this_week', [])),
        len(us.get('this_month', [])),
        len(us.get('next_2_months', [])),
    )
    return briefing


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
            key_themes      : [{"icon","category","title","why_important","duration","swing_point"}, ...]  3개
            leading_sectors : [{"emoji","name","stars","reason","stocks_kr","stocks_us"}, ...]  2~3개
            swing_check     : {"phase", "catalysts": [...], "risks": [...]}
            upcoming_schedule : {"this_week":[...], "this_month":[...], "next_2_months":[...]}  (각 항목 date/event/ticker/sector_kr/detail/importance)
            portfolio_adjustment : {"passive_note": str, "swing_candidates": [...]}
        실패 시 None 반환.
    """
    if today_str is None:
        today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")

    client = _get_gemini_client()
    if client is None:
        return None

    # ── 지수/자산 데이터 텍스트 ──────────────────────────────────────────
    name_map = {
        "KOSPI":       "코스피",
        "KOSDAQ":      "코스닥",
        "SP500":       "S&P500",
        "NASDAQ":      "나스닥100",
        "DOW":         "다우존스",
        "RUSSELL2000": "러셀2000",
        "GOLD":        "금(달러/온스)",
        "DXY":         "달러인덱스",
        "US2Y":        "미2년물금리(%)",
        "US10Y":       "미10년물금리(%)",
        "USDKRW":      "원/달러",
        "WTI":         "WTI 원유(달러/배럴)",
        "NATGAS":      "천연가스(달러/MMBtu)",
    }
    index_keys = ["SP500", "NASDAQ", "DOW", "RUSSELL2000"]
    macro_keys = ["GOLD", "DXY", "US2Y", "US10Y", "USDKRW", "WTI", "NATGAS"]

    def _fmt(k, v):
        sign = "+" if v["change"] >= 0 else ""
        date_str = v.get("data_date", "")
        date_suffix = f" [{date_str} 종가]" if date_str else ""
        return f"  {name_map.get(k, k)}: {v['current']:,.4f} ({sign}{v['change_pct']:.2f}%){date_suffix}"

    indices_text = "\n".join(_fmt(k, stocks[k]) for k in index_keys if k in stocks)
    macro_text   = "\n".join(_fmt(k, stocks[k]) for k in macro_keys if k in stocks)

    # ── 실적 발표 텍스트 ─────────────────────────────────────────────────
    if earnings:
        # v1.5.7: 직전분기 실제·분산·sanity flag·매출(노이즈 시 숨김) 반영
        earnings_lines = []
        for e in earnings:
            flag = " ⚠️" if e.get("eps_sanity_flag") else ""
            disp = e.get("eps_dispersion", "-")
            disp_part = f", {disp}" if disp and disp != "-" else ""
            rev = e.get("revenue_estimate", "-")
            rev_part = f" / 매출 {rev}" if rev and rev != "-" else ""
            # v1.5.16: D+N 명시로 AI 슬롯 배치 정확도 상승
            days_until = e.get("days_until", "?")
            earnings_lines.append(
                f"- {e['earnings_date']} [D+{days_until}] [{e['sector_kr']}] {e['name_kr']}({e['ticker']})\n"
                f"    예상 EPS {e['eps_estimate']}{flag} (직전분기 실제 {e['last_quarter_eps_actual']}"
                f", 전년동기 실제 {e['yoy_eps_actual']}{disp_part}){rev_part}"
                f" / 컨센서스 {e['recommendation']}"
            )
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

[향후 60일 주요 기업 실적 발표 예정 (yfinance 실제 데이터, 75개 대형주 풀에서 수집)]
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
  "key_themes": [
    {{
      "icon": "🔴 또는 🟡 또는 🟢 (🔴=하락 리스크, 🟡=중립/혼조, 🟢=상승 모멘텀)",
      "category": "테마 분류 (예: 실적사이클, 통화정책, 정책·규제, 지정학, 산업구조 변화)",
      "title": "테마 제목 (1~3개월 지속 가능한 펀더멘털 테마)",
      "why_important": "왜 중요한지 — 투자 초보자도 이해할 수 있는 1문장",
      "duration": "예상 지속 기간 (예: '1~2개월', '분기 전반', '연말까지')",
      "swing_point": "스윙 관점 — 어떤 섹터/종목에 어떤 영향인지 구체적으로"
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
  "upcoming_schedule": {{
    "this_week": [
      {{
        "date": "MM/DD(요일)",
        "event": "이벤트명",
        "ticker": "티커 (실적 발표인 경우, 아니면 빈 문자열)",
        "sector_kr": "섹터명 (실적 발표인 경우, 아니면 빈 문자열)",
        "detail": "예상 EPS·매출·YoY·컨센서스 등 한 줄 요약",
        "importance": 3
      }}
    ],
    "this_month": [
      {{ "date":"...","event":"...","ticker":"...","sector_kr":"...","detail":"...","importance": 2 }}
    ],
    "next_2_months": [
      {{ "date":"...","event":"...","ticker":"...","sector_kr":"...","detail":"...","importance": 1 }}
    ]
  }},
  "portfolio_adjustment": {{
    "passive_note": "패시브 4자산(S&P500 ETF / 미국배당다우존스 / 국고채10년 / 금) 비중 미세 조정 제안 한 줄. 예: '금 ETF +2%p 검토, 국고채 -2%p (변동성 상승 대응)'. 조정 불필요 시 '현 비중 유지 권장' + 1문장 근거.",
    "swing_candidates": [
      "스윙 진입 후보 1: '<종목명>(<티커>) — <진입 근거 1문장>, 진입 가격 또는 조건 1문장'",
      "스윙 진입 후보 2 (또는 빈 문자열로 0~2개 허용)"
    ]
  }}
}}

[작성 규칙]
- 데이터 기준일 인식: [주요 지수]·[매크로 자산]의 [MM/DD(요일) 종가] 라벨을 인식하고
  현재 시점이 해당 종가 이후임을 전제로 어조 조정.
- key_themes: 정확히 3개, 1~3개월 지속 가능한 펀더멘털 테마만 선정.
  ★ 당일 단발성 뉴스, 1~2주 단기 이벤트, 단순 헤드라인, 소문 제외.
  ★ 포함 대상: 실적 사이클 흐름, 통화정책 방향(연준 금리경로), 정책·규제, 지정학, 산업구조 변화(AI·전기차 등 메가트렌드).
  duration 은 반드시 명시. swing_point 는 어떤 섹터·종목에 어떤 영향인지 구체.
- leading_sectors (v1.5.15 정의 확장): 2~3개. 다음 두 기준 중 하나라도 해당하면 선정 가능.

  기준 A) 지속 가능한 상승 모멘텀 (기존)
    실적 사이클, 정책 변화, 금리·환율 등 최소 3개월 이상 지속 가능한 펀더멘털 근거.
    예: "메모리 슈퍼사이클 진입, 3개월+ 실적 개선 지속 예상"

  기준 B) 시장 집중 관심 — 방향 무관 (v1.5.15 신규)
    상승·하락 방향과 무관하게 다음 3조건 모두 만족:
    ① 향후 30일 이내 실적 발표 임박 또는 주요 정책·이벤트 초점
    ② 뉴스에서 지속적·집중적 언급 (단발성 헤드라인 아님)
    ③ 향후 1~3개월 시장 방향 결정력 있음
    예: "삼성·SK하이닉스 실적 발표 임박(30일 이내) 및 AI 반도체 공급/수요 이슈로
         시장 관심 집중, 하락 리스크지만 방향성 결정력 큼"

  ★ reason 에 어느 기준(A/B)에 해당하는지 명확히 서술.
    기준 B 선정 시 "관심 집중" 명확 근거(뉴스 다수 언급·실적 임박) 포함 필수.
  ★ 여전히 금지: 1~2주 단기 이벤트만으로, 계절성 테마만으로, 컨퍼런스만으로 선정.
  ★ 기준 B 로 선정한 하락 방향 섹터는 swing_point 에 "숏 관점" 또는 "관망·발표 후 판단" 명확히 서술.
    무조건 "매수 후보" 로 처리하지 말 것.
  ★ 총 개수 2~3개 상한 유지 (기준 B 추가로 4개+ 선정 금지).
  ★ name 은 다음 EXTENDED_TICKERS sector_kr 명칭 중에서 **정확히 일치**하게 선택 (v1.5.10):
    "빅테크/AI", "반도체", "소프트웨어", "자동차/EV", "소비재(리테일)", "음식료/생활",
    "금융", "헬스케어/제약", "산업/항공/방산", "미디어/통신", "에너지", "유틸리티/리츠"
  ★ "반도체 산업", "AI/빅테크" 같이 변형하지 말 것. Python 측 사후 필터가 정확 매칭만 함.
  ★ 위 명칭에 해당하지 않는 새 섹터를 선정할 경우, upcoming_schedule 의 해당 섹터 항목이 사후 필터로 모두 제거됨.
- upcoming_schedule: 향후 60일 주요 일정을 다음 3개 슬롯으로 분류 (v1.5.16 규칙 명확화).
  ★ 각 실적 항목 옆에 표시된 [D+N] 값을 그대로 사용해 슬롯 배치:
    · this_week: **D+0 ~ D+7** (오늘~7일 후), importance=3
    · this_month: **D+8 ~ D+30** (8~30일 후), importance=2
    · next_2_months: **D+31 ~ D+60** (31~60일 후), importance=1
  ★ 예: [D+6] 인 실적은 this_week 슬롯에, [D+21] 인 실적은 this_month 슬롯에.
  ★ AI 가 날짜를 자체 계산하지 말 것. 반드시 제공된 [D+N] 사용.
  ★ 슬롯 상한은 AI 측 참고값이며 Python 사후 처리에서 재조정됨.
  ★ leading_sectors 의 sector_kr 과 일치하는 실적만 포함. 그 외 섹터 실적은 생략.
  ★ 슬롯 한도 초과 시 임박 날짜 우선.
  ★ 실적 발표 항목의 event 필드는 반드시 "<회사명> 실적 발표" 형식으로 작성 (예: "마이크론 실적 발표", "JP모건 실적 발표").
    회사명은 [향후 60일 주요 기업 실적 발표 예정] 섹션의 name_kr 필드 값을 그대로 사용.
    단순히 "실적 발표" 만 적으면 회사명이 누락되어 가독성이 떨어짐.
  ★ 실적 detail 형식 (압축형, v1.5.8): 다음 형식을 반드시 따를 것.
    "예상 EPS $<X.XX>[⚠️] (직전 $<Q>, YoY $<Y>[, ±<N>%]) | 컨센서스 <Buy/Hold/Sell>(<n>/<m>) [| 매출 $<R>B]"
    - 직전분기 EPS 는 "직전 $X.XX" 로 짧게 (직전분기 실제 → 직전).
    - 전년동기 EPS 는 "YoY $X.XX" 로 짧게 (전년동기 실제 → YoY).
    - 분산은 30% 이상일 때만 ", ±N%" 추가, 30% 미만이면 생략.
    - 컨센서스 사이 공백 제거: "Buy(39/44)" (구버전 "Buy (39/44)" 보다 1자 절감).
    - 매출은 "-" 가 아닐 때만 " | 매출 $X.XB" 형식으로 끝에 추가, "-" 이면 통째로 생략.
    - 구분자는 슬래시 "/" 가 아닌 파이프 "|" 사용 (가독성).
    예 (정상): "예상 EPS $5.41 (직전 $5.94, YoY $4.96) | 컨센서스 Buy(12/24) | 매출 $48.72B"
    예 (sanity flag): "예상 EPS $0.12 ⚠️ (직전 $0.35, YoY $0.14, ±50%) | 컨센서스 Hold(21/38) | 매출 $11.51B"
    예 (매출 차단): "예상 EPS $20.05 (직전 $12.20, YoY $1.91, ±46%) | 컨센서스 Buy(39/44)"
  ★ ⚠️ 마크가 붙은 항목(eps_sanity_flag) 끝에 ", 신뢰도 주의" 한 어절을 detail 마지막에 추가.
    예: "예상 EPS $0.12 ⚠️ (직전 $0.35, YoY $0.14, ±50%) | 컨센서스 Hold(21/38) | 매출 $11.51B, 신뢰도 주의"
  ★ 매출이 "-" 인 경우 매출 항목 생략.
  ★ 경제지표·FOMC 포함 가능, 공휴일·단순 연설 제외.
  ★ ticker / sector_kr 은 실적인 경우에만 채움.
- swing_check.catalysts (v1.5.8 강화):
  ★ catalysts 는 upcoming_schedule (②) 와 절대 중복 금지. 같은 실적 발표 일정을 반복 노출하지 말 것.
  ★ 포함 대상: FOMC 회의, CPI/PCE/고용지표 등 경제지표 발표일, 정책 이벤트, 지정학·규제 이벤트만.
  ★ 실적 발표 일정은 ② 에 이미 표시되므로 catalysts 에서 제외.
  ★ 최대 3개. 해당 없으면 빈 리스트 []. 억지로 채우지 말 것.
- portfolio_adjustment (v1.5.8 길이 한도 강화):
  ★ passive_note 는 1문장, 80자 이내 (한국어 기준). 4자산 중 어떤 자산 ±%p 조정 또는 "현 비중 유지" 명확히.
    예 (80자 이내): "금 ETF +2%p 검토, 국고채 -2%p (변동성 상승 대응)."
    예 (현 비중 유지): "현 비중 유지 권장 — 매크로 큰 변화 없음, 분기 리밸런싱 대기."
  ★ swing_candidates 각 항목 100자 이내, 0~2개. 형식:
    "<종목명>(<티커>) — <진입 근거 1문장>, <진입 시점/조건>"
    예 (100자 이내): "마이크론(MU) — AI 메모리 수요 강세, 06/24 실적 발표 후 가이던스 확인 후 진입 검토."
  ★ ⚠️ sanity flag 된 종목(eps_sanity_flag=True)은 swing_candidates 추천 제외 또는 "추정 신뢰도 낮음" 명시.
  ★ 투자 권유 표현 금지 ("매수하세요" 등). 권유성은 "진입 후보로 검토 가능" 형태로.
- 인사말·서문·결론 문구 금지."""

    # ── Gemini 호출 (최대 3회 시도 + 선형 백오프, v1.5.12) ───────────────
    json_config = _make_json_gen_config()
    kwargs = {"model": MODEL_NAME, "contents": prompt}
    if json_config:
        kwargs["config"] = json_config

    last_response_text = None
    for attempt in range(MAX_AI_ATTEMPTS):
        try:
            response = client.models.generate_content(**kwargs)
            last_response_text = response.text
            result = _parse_json_response(response.text)

            # 필수 키 검증
            required = ["market_regime", "key_themes", "leading_sectors", "swing_check", "upcoming_schedule", "portfolio_adjustment"]
            missing = [k for k in required if k not in result]
            if missing:
                logger.warning("[시도 %d/%d] 누락된 키: %s. 재시도합니다.", attempt + 1, MAX_AI_ATTEMPTS, missing)
                _ai_retry_backoff(attempt)
                continue

            logger.info("오전 브리핑 AI 분석 완료 (%d자)", len(response.text))
            # v1.5.10: 주도섹터 사후 필터 강제
            result = _filter_upcoming_by_leading_sectors(result)

            # v1.5.16: 3단계 슬롯 후처리 (필터 → 재배치 → 상한/정렬)
            # (1) 실제 days 로 슬롯 재배치 (AI 오분류 보정)
            result = _reassign_upcoming_slots_by_days(result, datetime.now(KST).date())
            # (2) 섹터별 상한 + 주도섹터 우선 정렬
            result = _cap_and_sort_by_sector_priority(result)

            return result

        except Exception as e:
            logger.error("[시도 %d/%d] Gemini 오전 브리핑 분석 실패: %s", attempt + 1, MAX_AI_ATTEMPTS, e)
            if last_response_text:
                logger.debug("응답 원시 텍스트 (첫 500자): %s", last_response_text[:500])
            _ai_retry_backoff(attempt)

    logger.error("Gemini 분석 %d회 모두 실패.", MAX_AI_ATTEMPTS)
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
            key_themes       : [{"icon","category","title","why_important","duration","swing_point"}, ...]  3개
            leading_sectors  : [{"emoji","name","stars","reason","stocks_kr","stocks_us"}, ...]  2~3개
            swing_check      : {"phase", "catalysts": [...], "risks": [...]}
            upcoming_schedule : {"this_week":[...], "this_month":[...], "next_2_months":[...]}  (각 항목 date/event/ticker/sector_kr/detail/importance)
            portfolio_adjustment : {"passive_note": str, "swing_candidates": [...]}
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
        # v1.5.7: 직전분기 실제·분산·sanity flag·매출(노이즈 시 숨김) 반영
        earnings_lines = []
        for e in earnings:
            flag = " ⚠️" if e.get("eps_sanity_flag") else ""
            disp = e.get("eps_dispersion", "-")
            disp_part = f", {disp}" if disp and disp != "-" else ""
            rev = e.get("revenue_estimate", "-")
            rev_part = f" / 매출 {rev}" if rev and rev != "-" else ""
            # v1.5.16: D+N 명시로 AI 슬롯 배치 정확도 상승
            days_until = e.get("days_until", "?")
            earnings_lines.append(
                f"- {e['earnings_date']} [D+{days_until}] [{e['sector_kr']}] {e['name_kr']}({e['ticker']})\n"
                f"    예상 EPS {e['eps_estimate']}{flag} (직전분기 실제 {e['last_quarter_eps_actual']}"
                f", 전년동기 실제 {e['yoy_eps_actual']}{disp_part}){rev_part}"
                f" / 컨센서스 {e['recommendation']}"
            )
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

[향후 60일 주요 기업 실적 발표 예정 (yfinance 실제 데이터, 75개 대형주 풀에서 수집)]
{earnings_text}

[오늘 국내 뉴스]
{domestic_text}

아래 JSON 스키마를 정확히 따라 응답하세요. JSON 이외의 텍스트는 절대 포함하지 마세요:

{{
  "market_summary": "오늘 국내 시장(코스피·코스닥) 마감 총평 1~2문장",
  "vix_comment": "현재 VIX 수준이 스윙 트레이딩에 갖는 의미 1문장",
  "portfolio_note": "패시브 포트폴리오 4개 자산 중 오늘 주목할 동향 한줄",
  "key_themes": [
    {{
      "icon": "🔴 또는 🟡 또는 🟢 (🔴=하락 리스크, 🟡=중립/혼조, 🟢=상승 모멘텀)",
      "category": "테마 분류 (예: 실적사이클, 통화정책, 정책·규제, 지정학, 산업구조 변화)",
      "title": "테마 제목 (1~3개월 지속 가능한 펀더멘털 테마)",
      "why_important": "왜 중요한지 — 투자 초보자도 이해할 수 있는 1문장. 전문용어는 괄호로 풀이 병기",
      "duration": "예상 지속 기간 (예: '1~2개월', '분기 전반', '연말까지')",
      "swing_point": "스윙 관점 — 어떤 섹터/종목에 어떤 영향인지 구체적으로"
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
  "upcoming_schedule": {{
    "this_week": [
      {{
        "date": "MM/DD(요일)",
        "event": "이벤트명",
        "ticker": "티커 (실적 발표인 경우, 아니면 빈 문자열)",
        "sector_kr": "섹터명 (실적 발표인 경우, 아니면 빈 문자열)",
        "detail": "예상 EPS·매출·YoY·컨센서스 등 한 줄 요약",
        "importance": 3
      }}
    ],
    "this_month": [
      {{ "date":"...","event":"...","ticker":"...","sector_kr":"...","detail":"...","importance": 2 }}
    ],
    "next_2_months": [
      {{ "date":"...","event":"...","ticker":"...","sector_kr":"...","detail":"...","importance": 1 }}
    ]
  }},
  "portfolio_adjustment": {{
    "passive_note": "패시브 4자산(S&P500 ETF / 미국배당다우존스 / 국고채10년 / 금) 비중 미세 조정 제안 한 줄. 예: '금 ETF +2%p 검토, 국고채 -2%p (변동성 상승 대응)'. 조정 불필요 시 '현 비중 유지 권장' + 1문장 근거.",
    "swing_candidates": [
      "스윙 진입 후보 1: '<종목명>(<티커>) — <진입 근거 1문장>, 진입 가격 또는 조건 1문장'",
      "스윙 진입 후보 2 (또는 빈 문자열로 0~2개 허용)"
    ]
  }}
}}

[작성 규칙]
- market_summary: 오늘 코스피·코스닥 등락과 주요 원인을 과거형으로 서술.
- vix_comment: VIX 수치({vix_info.get('current', 'N/A')})를 언급하며 안정/주의/공포 여부와 스윙 트레이딩 시사점 서술.
- key_themes: 정확히 3개, 1~3개월 지속 가능한 펀더멘털 테마만 선정.
  ★ 당일 단발성 뉴스, 1~2주 단기 이벤트, 단순 헤드라인, 소문 제외.
  ★ 포함 대상: 실적 사이클 흐름, 통화정책 방향(연준 금리경로), 정책·규제, 지정학, 산업구조 변화(AI·전기차 등 메가트렌드).
  duration 은 반드시 명시. swing_point 는 어떤 섹터·종목에 어떤 영향인지 구체.
  금융 전문용어 사용 시 괄호로 짧게 풀이 병기 (예: "FOMC(미 연준 통화정책 회의)", "EPS(주당순이익)").
- leading_sectors (v1.5.15 정의 확장): 2~3개. 다음 두 기준 중 하나라도 해당하면 선정 가능.

  기준 A) 지속 가능한 상승 모멘텀 (기존)
    실적 사이클, 정책 변화, 금리·환율 등 최소 3개월 이상 지속 가능한 펀더멘털 근거.
    예: "메모리 슈퍼사이클 진입, 3개월+ 실적 개선 지속 예상"

  기준 B) 시장 집중 관심 — 방향 무관 (v1.5.15 신규)
    상승·하락 방향과 무관하게 다음 3조건 모두 만족:
    ① 향후 30일 이내 실적 발표 임박 또는 주요 정책·이벤트 초점
    ② 뉴스에서 지속적·집중적 언급 (단발성 헤드라인 아님)
    ③ 향후 1~3개월 시장 방향 결정력 있음
    예: "삼성·SK하이닉스 실적 발표 임박(30일 이내) 및 AI 반도체 공급/수요 이슈로
         시장 관심 집중, 하락 리스크지만 방향성 결정력 큼"

  ★ reason 에 어느 기준(A/B)에 해당하는지 명확히 서술.
    기준 B 선정 시 "관심 집중" 명확 근거(뉴스 다수 언급·실적 임박) 포함 필수.
  ★ 여전히 금지: 1~2주 단기 이벤트만으로, 계절성 테마만으로, 컨퍼런스만으로 선정.
  ★ 기준 B 로 선정한 하락 방향 섹터는 swing_point 에 "숏 관점" 또는 "관망·발표 후 판단" 명확히 서술.
    무조건 "매수 후보" 로 처리하지 말 것.
  ★ 총 개수 2~3개 상한 유지 (기준 B 추가로 4개+ 선정 금지).
  ★ name 은 다음 EXTENDED_TICKERS sector_kr 명칭 중에서 **정확히 일치**하게 선택 (v1.5.10):
    "빅테크/AI", "반도체", "소프트웨어", "자동차/EV", "소비재(리테일)", "음식료/생활",
    "금융", "헬스케어/제약", "산업/항공/방산", "미디어/통신", "에너지", "유틸리티/리츠"
  ★ "반도체 산업", "AI/빅테크" 같이 변형하지 말 것. Python 측 사후 필터가 정확 매칭만 함.
  ★ 위 명칭에 해당하지 않는 새 섹터를 선정할 경우, upcoming_schedule 의 해당 섹터 항목이 사후 필터로 모두 제거됨.
- upcoming_schedule: 향후 60일 주요 일정을 다음 3개 슬롯으로 분류 (v1.5.16 규칙 명확화).
  ★ 각 실적 항목 옆에 표시된 [D+N] 값을 그대로 사용해 슬롯 배치:
    · this_week: **D+0 ~ D+7** (오늘~7일 후), importance=3
    · this_month: **D+8 ~ D+30** (8~30일 후), importance=2
    · next_2_months: **D+31 ~ D+60** (31~60일 후), importance=1
  ★ 예: [D+6] 인 실적은 this_week 슬롯에, [D+21] 인 실적은 this_month 슬롯에.
  ★ AI 가 날짜를 자체 계산하지 말 것. 반드시 제공된 [D+N] 사용.
  ★ 슬롯 상한은 AI 측 참고값이며 Python 사후 처리에서 재조정됨.
  ★ leading_sectors 의 sector_kr 과 일치하는 실적만 포함. 그 외 섹터 실적은 생략.
  ★ 슬롯 한도 초과 시 임박 날짜 우선.
  ★ 실적 발표 항목의 event 필드는 반드시 "<회사명> 실적 발표" 형식으로 작성 (예: "마이크론 실적 발표", "JP모건 실적 발표").
    회사명은 [향후 60일 주요 기업 실적 발표 예정] 섹션의 name_kr 필드 값을 그대로 사용.
    단순히 "실적 발표" 만 적으면 회사명이 누락되어 가독성이 떨어짐.
  ★ 실적 detail 형식 (압축형, v1.5.8): 다음 형식을 반드시 따를 것.
    "예상 EPS $<X.XX>[⚠️] (직전 $<Q>, YoY $<Y>[, ±<N>%]) | 컨센서스 <Buy/Hold/Sell>(<n>/<m>) [| 매출 $<R>B]"
    - 직전분기 EPS 는 "직전 $X.XX" 로 짧게 (직전분기 실제 → 직전).
    - 전년동기 EPS 는 "YoY $X.XX" 로 짧게 (전년동기 실제 → YoY).
    - 분산은 30% 이상일 때만 ", ±N%" 추가, 30% 미만이면 생략.
    - 컨센서스 사이 공백 제거: "Buy(39/44)" (구버전 "Buy (39/44)" 보다 1자 절감).
    - 매출은 "-" 가 아닐 때만 " | 매출 $X.XB" 형식으로 끝에 추가, "-" 이면 통째로 생략.
    - 구분자는 슬래시 "/" 가 아닌 파이프 "|" 사용 (가독성).
    예 (정상): "예상 EPS $5.41 (직전 $5.94, YoY $4.96) | 컨센서스 Buy(12/24) | 매출 $48.72B"
    예 (sanity flag): "예상 EPS $0.12 ⚠️ (직전 $0.35, YoY $0.14, ±50%) | 컨센서스 Hold(21/38) | 매출 $11.51B"
    예 (매출 차단): "예상 EPS $20.05 (직전 $12.20, YoY $1.91, ±46%) | 컨센서스 Buy(39/44)"
  ★ ⚠️ 마크가 붙은 항목(eps_sanity_flag) 끝에 ", 신뢰도 주의" 한 어절을 detail 마지막에 추가.
    예: "예상 EPS $0.12 ⚠️ (직전 $0.35, YoY $0.14, ±50%) | 컨센서스 Hold(21/38) | 매출 $11.51B, 신뢰도 주의"
  ★ 매출이 "-" 인 경우 매출 항목 생략.
  ★ 경제지표·FOMC 포함 가능, 공휴일·단순 연설 제외.
  ★ ticker / sector_kr 은 실적인 경우에만 채움.
- swing_check.catalysts (v1.5.8 강화):
  ★ catalysts 는 upcoming_schedule (②) 와 절대 중복 금지. 같은 실적 발표 일정을 반복 노출하지 말 것.
  ★ 포함 대상: FOMC 회의, CPI/PCE/고용지표 등 경제지표 발표일, 정책 이벤트, 지정학·규제 이벤트만.
  ★ 실적 발표 일정은 ② 에 이미 표시되므로 catalysts 에서 제외.
  ★ 최대 3개. 해당 없으면 빈 리스트 []. 억지로 채우지 말 것.
- portfolio_adjustment (v1.5.8 길이 한도 강화):
  ★ passive_note 는 1문장, 80자 이내 (한국어 기준). 4자산 중 어떤 자산 ±%p 조정 또는 "현 비중 유지" 명확히.
    예 (80자 이내): "금 ETF +2%p 검토, 국고채 -2%p (변동성 상승 대응)."
    예 (현 비중 유지): "현 비중 유지 권장 — 매크로 큰 변화 없음, 분기 리밸런싱 대기."
  ★ swing_candidates 각 항목 100자 이내, 0~2개. 형식:
    "<종목명>(<티커>) — <진입 근거 1문장>, <진입 시점/조건>"
    예 (100자 이내): "마이크론(MU) — AI 메모리 수요 강세, 06/24 실적 발표 후 가이던스 확인 후 진입 검토."
  ★ ⚠️ sanity flag 된 종목(eps_sanity_flag=True)은 swing_candidates 추천 제외 또는 "추정 신뢰도 낮음" 명시.
  ★ 투자 권유 표현 금지 ("매수하세요" 등). 권유성은 "진입 후보로 검토 가능" 형태로.
- 인사말·서문·결론 문구 금지."""

    # ── Gemini 호출 (최대 3회 시도 + 선형 백오프, v1.5.12) ───────────────
    json_config = _make_json_gen_config()
    kwargs = {"model": MODEL_NAME, "contents": prompt}
    if json_config:
        kwargs["config"] = json_config

    last_response_text = None
    for attempt in range(MAX_AI_ATTEMPTS):
        try:
            response = client.models.generate_content(**kwargs)
            last_response_text = response.text
            result = _parse_json_response(response.text)

            required = ["market_summary", "key_themes", "leading_sectors", "swing_check", "upcoming_schedule", "portfolio_adjustment"]
            missing = [k for k in required if k not in result]
            if missing:
                logger.warning("[시도 %d/%d] 누락된 키: %s. 재시도합니다.", attempt + 1, MAX_AI_ATTEMPTS, missing)
                _ai_retry_backoff(attempt)
                continue

            logger.info("오후 브리핑 AI 분석 완료 (%d자)", len(response.text))
            # v1.5.10: 주도섹터 사후 필터 강제
            result = _filter_upcoming_by_leading_sectors(result)

            # v1.5.16: 3단계 슬롯 후처리 (필터 → 재배치 → 상한/정렬)
            # (1) 실제 days 로 슬롯 재배치 (AI 오분류 보정)
            result = _reassign_upcoming_slots_by_days(result, datetime.now(KST).date())
            # (2) 섹터별 상한 + 주도섹터 우선 정렬
            result = _cap_and_sort_by_sector_priority(result)

            return result

        except Exception as e:
            logger.error("[시도 %d/%d] Gemini 오후 브리핑 분석 실패: %s", attempt + 1, MAX_AI_ATTEMPTS, e)
            if last_response_text:
                logger.debug("응답 원시 텍스트 (첫 500자): %s", last_response_text[:500])
            _ai_retry_backoff(attempt)

    logger.error("Gemini 오후 브리핑 분석 %d회 모두 실패.", MAX_AI_ATTEMPTS)
    return None
