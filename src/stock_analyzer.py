"""주식 지수 및 매크로 자산 데이터 수집 모듈."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _yf_history_safe(ticker: str, period: str = "5d", per_call_timeout: float = 10.0):
    """yfinance Ticker.history 호출을 별도 스레드로 실행해 타임아웃을 강제합니다.

    yfinance 는 timeout 인자를 공개 API 로 노출하지 않으므로
    concurrent.futures 로 감싸 per_call_timeout 초 안에 종료되지 않으면
    예외를 발생시키고 빈 DataFrame 을 반환합니다.
    """
    import concurrent.futures
    import pandas as pd
    import yfinance as yf

    def _call():
        return yf.Ticker(ticker).history(period=period)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_call)
        try:
            return fut.result(timeout=per_call_timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("yfinance %s history 타임아웃 (%.1fs)", ticker, per_call_timeout)
            return pd.DataFrame()


def _format_data_date(date_obj) -> str:
    """yfinance/FDR index 값에서 'MM/DD(요일)' 문자열을 생성합니다."""
    try:
        d = date_obj.date() if hasattr(date_obj, "date") else date_obj
        return f"{d.month:02d}/{d.day:02d}({_WEEKDAY_KR[d.weekday()]})"
    except Exception:
        return ""


def collect_morning_stocks() -> dict:
    """오전 알림용: 국내외 주요 지수 + 매크로 자산 + 에너지 데이터를 수집합니다.

    Returns:
        {
            "KOSPI":       {"current", "change", "change_pct", "data_date"},
            "KOSDAQ":      {"current", "change", "change_pct", "data_date"},
            "SP500":       {"current", "change", "change_pct", "data_date"},
            "NASDAQ":      {"current", "change", "change_pct", "data_date"},
            "DOW":         {"current", "change", "change_pct", "data_date"},
            "RUSSELL2000": {"current", "change", "change_pct", "data_date"},
            "GOLD":        {"current", "change", "change_pct", "data_date"},
            "DXY":         {"current", "change", "change_pct", "data_date"},
            "US2Y":        {"current", "change", "change_pct", "data_date"},
            "US10Y":       {"current", "change", "change_pct", "data_date"},
            "USDKRW":      {"current", "change", "change_pct", "data_date"},
            "WTI":         {"current", "change", "change_pct", "data_date"},
            "NATGAS":      {"current", "change", "change_pct", "data_date"},
        }
        수집 실패 항목은 결과에서 제외.
    """
    result = {}
    result.update(_collect_korean_indices())
    result.update(_collect_us_indices())
    result.update(_collect_macro_assets())
    result.update(_fetch_us2y_from_fred())   # FRED API (API 키 불필요)
    return result


def _collect_korean_indices() -> dict:
    """FinanceDataReader로 코스피, 코스닥 지수를 수집합니다."""
    try:
        import FinanceDataReader as fdr
    except ImportError:
        logger.warning("FinanceDataReader 패키지가 설치되지 않았습니다.")
        return {}

    result = {}
    indices = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}
    end_date = datetime.now(KST)
    start_date = end_date - timedelta(days=7)

    for name, ticker in indices.items():
        try:
            df = fdr.DataReader(
                ticker,
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
            )
            if df.empty or len(df) < 2:
                logger.warning("%s 데이터가 충분하지 않습니다.", name)
                continue

            current = float(df["Close"].iloc[-1])
            previous = float(df["Close"].iloc[-2])
            change = round(current - previous, 2)
            change_pct = round((change / previous) * 100, 2)

            result[name] = {
                "current": round(current, 2),
                "change": change,
                "change_pct": change_pct,
                "data_date": _format_data_date(df.index[-1]),
            }
            logger.info("%s 수집 완료: %.2f (%.2f%%)", name, current, change_pct)
        except Exception as e:
            logger.warning("%s 수집 실패: %s", name, e)

    return result


def _collect_us_indices() -> dict:
    """yfinance로 S&P 500, 나스닥, 다우존스 지수를 수집합니다."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance 패키지가 설치되지 않았습니다.")
        return {}

    result = {}
    indices = {
        "SP500":       "^GSPC",
        "NASDAQ":      "^IXIC",
        "DOW":         "^DJI",
        "RUSSELL2000": "^RUT",
    }

    for name, ticker in indices.items():
        try:
            df = _yf_history_safe(ticker, period="5d")
            if df.empty or len(df) < 2:
                logger.warning("%s 데이터가 충분하지 않습니다.", name)
                continue

            current = float(df["Close"].iloc[-1])
            previous = float(df["Close"].iloc[-2])
            change = round(current - previous, 2)
            change_pct = round((change / previous) * 100, 2)

            result[name] = {
                "current": round(current, 2),
                "change": change,
                "change_pct": change_pct,
                "data_date": _format_data_date(df.index[-1]),
            }
            logger.info("%s 수집 완료: %.2f (%.2f%%)", name, current, change_pct)
        except Exception as e:
            logger.warning("%s 수집 실패: %s", name, e)

    return result


def _collect_macro_assets() -> dict:
    """yfinance로 금·달러인덱스·미국 10년물 금리·원달러 환율을 수집합니다.

    Returns:
        {
            "GOLD":   금 선물 (달러/트로이온스)
            "DXY":    달러인덱스
            "US10Y":  미국 10년물 국채 수익률 (% 단위, e.g. 4.62)
            "USDKRW": 원/달러 환율 (e.g. 1385.0)
        }
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance 패키지가 설치되지 않았습니다.")
        return {}

    assets_map = {
        "GOLD":   "GC=F",        # 금 선물
        "DXY":    "DX-Y.NYB",    # 달러인덱스
        "US10Y":  "^TNX",        # 미국 10년물 국채 수익률 (%)
        "USDKRW": "USDKRW=X",    # 원/달러 환율
        "WTI":    "CL=F",        # WTI 원유 선물
        "NATGAS": "NG=F",        # 천연가스 선물
    }

    result = {}
    for name, ticker in assets_map.items():
        try:
            df = _yf_history_safe(ticker, period="5d")
            if df.empty or len(df) < 2:
                logger.warning("%s 데이터가 충분하지 않습니다 (%s).", name, ticker)
                continue

            current = float(df["Close"].iloc[-1])
            previous = float(df["Close"].iloc[-2])
            change = round(current - previous, 4)
            change_pct = round((change / previous) * 100, 2) if previous != 0 else 0.0

            result[name] = {
                "current": round(current, 4),
                "change": change,
                "change_pct": change_pct,
                "data_date": _format_data_date(df.index[-1]),
            }
            logger.info("%s 수집 완료: %.4f (%.2f%%)", name, current, change_pct)
        except Exception as e:
            logger.warning("%s 수집 실패 (%s): %s", name, ticker, e)

    return result


def _fetch_us2y_from_fred() -> dict:
    """FRED에서 미국 2년물 국채 수익률(DGS2)을 수집합니다.

    API 키 없이 FRED 공개 CSV 엔드포인트를 사용합니다.
    수집 실패 시 빈 dict 반환.
    """
    import csv
    import io
    import requests
    from datetime import date as _date

    FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2"
    try:
        resp = requests.get(FRED_URL, timeout=10,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        reader = csv.reader(io.StringIO(resp.text))
        # 헤더 행 제외, "." 값(공휴일) 제외, 날짜순 정렬
        rows = [
            row for row in reader
            if len(row) == 2 and row[0] != "DATE" and row[1].strip() not in ("", ".")
        ]
        rows.sort(key=lambda r: r[0])  # YYYY-MM-DD 문자열 정렬

        if len(rows) < 2:
            logger.warning("US2Y(FRED) 데이터 부족: 최소 2행 필요")
            return {}

        current = float(rows[-1][1])
        previous = float(rows[-2][1])
        change = round(current - previous, 4)
        change_pct = round((change / previous) * 100, 2) if previous != 0 else 0.0

        # 데이터 기준일 파싱 (YYYY-MM-DD → MM/DD(요일))
        d = _date.fromisoformat(rows[-1][0])
        data_date = _format_data_date(d)

        logger.info("US2Y 수집 완료 (FRED): %.4f (%.4f%p)", current, change)
        return {
            "US2Y": {
                "current":    round(current, 4),
                "change":     change,
                "change_pct": change_pct,
                "data_date":  data_date,
            }
        }
    except Exception as e:
        logger.warning("US2Y FRED 수집 실패: %s", e)
        return {}


def _collect_vix() -> dict:
    """yfinance로 VIX 공포지수를 수집합니다.

    Returns:
        {"VIX": {"current": float, "change": float, "change_pct": float, "data_date": str}}
        수집 실패 시 빈 dict.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance 패키지가 설치되지 않았습니다.")
        return {}

    try:
        df = _yf_history_safe("^VIX", period="5d")
        if df.empty or len(df) < 2:
            logger.warning("VIX 데이터가 충분하지 않습니다.")
            return {}

        current = float(df["Close"].iloc[-1])
        previous = float(df["Close"].iloc[-2])
        change = round(current - previous, 2)
        change_pct = round((change / previous) * 100, 2) if previous != 0 else 0.0

        logger.info("VIX 수집 완료: %.2f (%.2f%%)", current, change_pct)
        return {
            "VIX": {
                "current":    round(current, 2),
                "change":     change,
                "change_pct": change_pct,
                "data_date":  _format_data_date(df.index[-1]),
            }
        }
    except Exception as e:
        logger.warning("VIX 수집 실패: %s", e)
        return {}


def collect_afternoon_stocks() -> dict:
    """오후 알림용: 국내 지수 + VIX 데이터를 수집합니다.

    Returns:
        {
            "KOSPI":  {"current": float, "change": float, "change_pct": float, "data_date": str},
            "KOSDAQ": {"current": float, "change": float, "change_pct": float, "data_date": str},
            "VIX":    {"current": float, "change": float, "change_pct": float, "data_date": str},
        }
        수집 실패 항목은 결과에서 제외.
    """
    result = {}
    result.update(_collect_korean_indices())
    result.update(_collect_vix())
    return result
