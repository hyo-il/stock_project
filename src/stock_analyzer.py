"""주식 지수 데이터 수집 모듈."""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def collect_stock_data() -> dict:
    """국내외 주요 주식 지수 데이터를 수집합니다.

    국내: 코스피(KS11), 코스닥(KQ11) — FinanceDataReader 사용
    해외: S&P 500(^GSPC), 나스닥(^IXIC), 다우존스(^DJI) — yfinance 사용

    Returns:
        각 지수의 현재가, 전일 대비 변동폭, 변동률을 담은 딕셔너리.
        수집 실패 시 해당 항목은 포함되지 않습니다.
    """
    result = {}
    result.update(_collect_korean_indices())
    result.update(_collect_us_indices())
    return result


def _collect_korean_indices() -> dict:
    """FinanceDataReader로 코스피, 코스닥 지수를 수집합니다.

    Returns:
        KOSPI, KOSDAQ 데이터 딕셔너리
    """
    try:
        import FinanceDataReader as fdr
    except ImportError:
        logger.warning("FinanceDataReader 패키지가 설치되지 않았습니다.")
        return {}

    result = {}
    indices = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}
    end_date = datetime.today()
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

            result[name] = {
                "current": round(current, 2),
                "change": change,
                "change_pct": change_pct,
            }
            logger.info("%s 수집 완료: %.2f (%.2f%%)", name, current, change_pct)
        except Exception as e:
            logger.warning("%s 수집 실패: %s", name, e)

    return result


def _collect_us_indices() -> dict:
    """yfinance로 S&P 500, 나스닥, 다우존스 지수를 수집합니다.

    Returns:
        SP500, NASDAQ, DOW 데이터 딕셔너리
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance 패키지가 설치되지 않았습니다.")
        return {}

    result = {}
    indices = {
        "SP500": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW": "^DJI",
    }

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

            result[name] = {
                "current": round(current, 2),
                "change": change,
                "change_pct": change_pct,
            }
            logger.info("%s 수집 완료: %.2f (%.2f%%)", name, current, change_pct)
        except Exception as e:
            logger.warning("%s 수집 실패: %s", name, e)

    return result
