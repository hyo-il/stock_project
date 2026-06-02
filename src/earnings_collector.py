"""주요 기업 실적 발표 일정 수집 모듈 (yfinance 기반)."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")


def _yf_earnings_dates_safe(ticker: str, per_call_timeout: float = 10.0):
    """yfinance Ticker.earnings_dates 호출에 스레드 타임아웃을 강제합니다.

    수집 실패 시 None 을 반환하여 호출부에서 skip 처리하도록 합니다.
    """
    import concurrent.futures
    import yfinance as yf

    def _call():
        return yf.Ticker(ticker).earnings_dates

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_call)
        try:
            return fut.result(timeout=per_call_timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("yfinance %s earnings_dates 타임아웃 (%.1fs)", ticker, per_call_timeout)
            return None
        except Exception as e:
            logger.warning("yfinance %s earnings_dates 예외: %s", ticker, e)
            return None


# 주요 미국 대형주 티커 → 한국어 기업명 매핑
MAJOR_US_TICKERS = {
    # 빅테크 / AI
    "AAPL":  "애플",
    "GOOGL": "알파벳(구글)",
    "MSFT":  "마이크로소프트",
    "AMZN":  "아마존",
    "META":  "메타",
    "NVDA":  "엔비디아",
    "TSLA":  "테슬라",
    # 반도체
    "AVGO":  "브로드컴",
    "AMD":   "AMD",
    "INTC":  "인텔",
    "QCOM":  "퀄컴",
    # 금융
    "JPM":   "JP모건",
    "GS":    "골드만삭스",
    "BAC":   "뱅크오브아메리카",
    # 기타 대형주
    "NFLX":  "넷플릭스",
    "ORCL":  "오라클",
    "CRM":   "세일즈포스",
    "V":     "비자",
}


def collect_upcoming_earnings(days_ahead: int = 7) -> list:
    """향후 N일 이내 주요 기업 실적 발표 일정을 수집합니다.

    yfinance의 earnings_dates를 사용하여 미래 실적 발표 예정일만 필터링합니다.
    (Reported EPS가 NaN인 행 = 아직 발표 전)

    Args:
        days_ahead: 오늘부터 몇 일 앞까지 조회할지 (기본 7일)

    Returns:
        [
            {
                "ticker":       str,   # 예: "AAPL"
                "name_kr":      str,   # 예: "애플"
                "earnings_date": str,  # 예: "05/02(금)"
                "eps_estimate": str,   # 예: "$1.57" 또는 "-" (없을 경우)
            },
            ...
        ]
        실적 발표 예정일이 없거나 수집 실패 시 빈 리스트 반환.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance 패키지가 설치되지 않았습니다.")
        return []

    today = datetime.now(KST).date()
    end_date = today + timedelta(days=days_ahead)

    _WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

    def _fmt_date(d) -> str:
        try:
            if hasattr(d, "date"):
                d = d.date()
            return f"{d.month:02d}/{d.day:02d}({_WEEKDAY_KR[d.weekday()]})"
        except Exception:
            return ""

    import time as _time
    results = []
    _loop_start = _time.monotonic()
    _GLOBAL_TIMEOUT = 90.0  # 전체 18개 티커 처리 상한 (초)

    for ticker, name_kr in MAJOR_US_TICKERS.items():
        # 전체 상한 도달 시 남은 티커 스킵
        if _time.monotonic() - _loop_start > _GLOBAL_TIMEOUT:
            logger.warning("earnings 전체 상한 %ss 초과 — 남은 티커 스킵", _GLOBAL_TIMEOUT)
            break

        try:
            ed = _yf_earnings_dates_safe(ticker, per_call_timeout=10.0)
            if ed is None or ed.empty:
                continue

            # Reported EPS가 NaN인 행 = 미래 실적 발표 예정
            future = ed[ed["Reported EPS"].isna()].copy()
            if future.empty:
                continue

            # 날짜 인덱스를 date 타입으로 변환 후 범위 필터
            future.index = future.index.tz_localize(None) if future.index.tz else future.index
            future_dates = future.index.date

            mask = (future_dates >= today) & (future_dates <= end_date)
            upcoming = future[mask]
            if upcoming.empty:
                continue

            for dt_idx in upcoming.index:
                eps_est = upcoming.loc[dt_idx, "EPS Estimate"]
                try:
                    eps_str = f"${float(eps_est):.2f}" if eps_est == eps_est else "-"
                except Exception:
                    eps_str = "-"

                d_obj = dt_idx.date() if hasattr(dt_idx, "date") else dt_idx
                results.append({
                    "ticker":        ticker,
                    "name_kr":       name_kr,
                    "earnings_date": _fmt_date(d_obj),
                    "eps_estimate":  eps_str,
                })
                logger.info("실적 예정: %s(%s) %s EPS추정 %s", ticker, name_kr, _fmt_date(d_obj), eps_str)

        except Exception as e:
            logger.warning("%s 실적 수집 실패: %s", ticker, e)

    # 날짜 오름차순 정렬 (earnings_date 문자열 기준 — MM/DD 포맷이므로 사전순 정렬 가능)
    results.sort(key=lambda x: x["earnings_date"])
    logger.info("실적 발표 예정 수집 완료: %d건", len(results))
    return results
