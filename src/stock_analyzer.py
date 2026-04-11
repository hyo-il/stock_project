"""주식 지수 데이터 수집 모듈."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")


def collect_morning_stocks() -> dict:
    """오전 알림용: 국내외 주요 지수 전일 종가를 수집합니다.

    Returns:
        {"KOSPI": {"current": ..., "change": ..., "change_pct": ...}, ...}
        수집 실패 시 해당 항목은 포함되지 않습니다.
    """
    result = {}
    result.update(_collect_korean_indices())
    result.update(_collect_us_indices())
    return result


def collect_evening_stocks() -> dict:
    """오후 알림용: 미국 선물 지수 현재가를 수집합니다.

    Returns:
        {"S&P500 선물": {"price": ..., "change": ..., "pct": ...}, ...}
        수집 실패 시 해당 항목은 포함되지 않습니다.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance 패키지가 설치되지 않았습니다.")
        return {}

    futures_map = {
        "S&P500 선물": "ES=F",
        "NASDAQ 선물": "NQ=F",
        "다우 선물": "YM=F",
    }
    result = {}
    for name, symbol in futures_map.items():
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1d", interval="1m")
            if data.empty:
                logger.warning("%s 데이터 없음", name)
                continue
            price = float(data["Close"].iloc[-1])
            prev = float(data["Open"].iloc[0])
            change = round(price - prev, 2)
            pct = round((change / prev) * 100, 2) if prev else 0.0
            result[name] = {"price": round(price, 2), "change": change, "pct": pct}
            logger.info("%s 수집 완료: %.2f (%.2f%%)", name, price, pct)
        except Exception as e:
            logger.warning("%s 선물 데이터 수집 실패: %s", name, e)

    return result


# 하위 호환 별칭
collect_stock_data = collect_morning_stocks


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
            df = fdr.DataReader(ticker, start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"))
            if df.empty or len(df) < 2:
                logger.warning("%s 데이터가 충분하지 않습니다.", name)
                continue

            current = float(df["Close"].iloc[-1])
            previous = float(df["Close"].iloc[-2])
            change = round(current - previous, 2)
            change_pct = round((change / previous) * 100, 2)

            result[name] = {"current": round(current, 2), "change": change, "change_pct": change_pct}
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
            data = yf.Ticker(ticker)
            df = data.history(period="5d")
            if df.empty or len(df) < 2:
                logger.warning("%s 데이터가 충분하지 않습니다.", name)
                continue

            current = float(df["Close"].iloc[-1])
            previous = float(df["Close"].iloc[-2])
            change = round(current - previous, 2)
            change_pct = round((change / previous) * 100, 2)

            result[name] = {"current": round(current, 2), "change": change, "change_pct": change_pct}
            logger.info("%s 수집 완료: %.2f (%.2f%%)", name, current, change_pct)
        except Exception as e:
            logger.warning("%s 수집 실패: %s", name, e)

    return result
