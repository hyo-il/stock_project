"""주식 지수 및 매크로 자산 데이터 수집 모듈."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _format_data_date(date_obj) -> str:
    """yfinance/FDR index 값에서 'MM/DD(요일)' 문자열을 생성합니다."""
    try:
        d = date_obj.date() if hasattr(date_obj, "date") else date_obj
        return f"{d.month:02d}/{d.day:02d}({_WEEKDAY_KR[d.weekday()]})"
    except Exception:
        return ""


def collect_morning_stocks() -> dict:
    """오전 알림용: 국내외 주요 지수 + 매크로 자산 데이터를 수집합니다.

    Returns:
        {
            "KOSPI":  {"current": float, "change": float, "change_pct": float, "data_date": str},
            "KOSDAQ": {"current": float, "change": float, "change_pct": float, "data_date": str},
            "SP500":  {"current": float, "change": float, "change_pct": float, "data_date": str},
            "NASDAQ": {"current": float, "change": float, "change_pct": float, "data_date": str},
            "DOW":    {"current": float, "change": float, "change_pct": float, "data_date": str},
            "GOLD":   {"current": float, "change": float, "change_pct": float, "data_date": str},
            "DXY":    {"current": float, "change": float, "change_pct": float, "data_date": str},
            "US10Y":  {"current": float, "change": float, "change_pct": float, "data_date": str},
            "USDKRW": {"current": float, "change": float, "change_pct": float, "data_date": str},
        }
        data_date: 실제 데이터 기준일 문자열 (예: "04/25(금)"). 수집 실패 시 해당 항목 제외.
    """
    result = {}
    result.update(_collect_korean_indices())
    result.update(_collect_us_indices())
    result.update(_collect_macro_assets())
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
    indices = {"SP500": "^GSPC", "NASDAQ": "^IXIC", "DOW": "^DJI"}

    for name, ticker in indices.items():
        try:
            df = yf.Ticker(ticker).history(period="5d")
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
    }

    result = {}
    for name, ticker in assets_map.items():
        try:
            df = yf.Ticker(ticker).history(period="5d")
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
        df = yf.Ticker("^VIX").history(period="5d")
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
