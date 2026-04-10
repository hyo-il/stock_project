"""경제 뉴스 RSS 수집 모듈 (국내 + 해외)."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

DOMESTIC_RSS = [
    {"url": "https://www.yonhapnewstv.co.kr/category/news/economy/feed/", "source": "연합뉴스"},
    {"url": "https://www.etnews.com/rss/rss.xml", "source": "전자신문"},
]

FOREIGN_RSS = [
    {"url": "https://feeds.reuters.com/reuters/technologyNews", "source": "Reuters"},
    {"url": "https://www.cnbc.com/id/19854910/device/rss/rss.html", "source": "CNBC"},
]


def collect_news() -> list:
    """국내외 경제 뉴스 RSS에서 뉴스를 수집합니다.

    국내 RSS와 해외 RSS를 모두 수집하여 is_foreign 플래그와 함께 반환합니다.
    AI 선별 전 원시 데이터를 반환하므로 5개 이상 수집합니다.

    Returns:
        뉴스 딕셔너리 리스트. 각 항목은 title, link, published, source, is_foreign 키를 가집니다.
    """
    domestic = _collect_from_sources(DOMESTIC_RSS, per_source_limit=8, is_foreign=False)
    if not domestic:
        logger.warning("국내 RSS 수집 실패, KBS RSS로 폴백합니다.")
        fallback = _fetch_rss("https://world.kbs.co.kr/rss/rss_economy.xml", limit=8, source="KBS", is_foreign=False)
        domestic = fallback

    foreign = _collect_from_sources(FOREIGN_RSS, per_source_limit=5, is_foreign=True)

    total = domestic + foreign
    logger.info("전체 뉴스 수집 완료: 국내 %d건, 해외 %d건", len(domestic), len(foreign))
    return total


def _collect_from_sources(sources: list, per_source_limit: int, is_foreign: bool) -> list:
    """여러 RSS 소스에서 뉴스를 수집합니다.

    Args:
        sources: RSS 소스 딕셔너리 리스트 (url, source 키)
        per_source_limit: 소스당 최대 수집 수
        is_foreign: 해외 뉴스 여부 플래그

    Returns:
        수집된 뉴스 리스트
    """
    all_news = []
    for src in sources:
        news = _fetch_rss(src["url"], limit=per_source_limit, source=src["source"], is_foreign=is_foreign)
        all_news.extend(news)
    return all_news


def _fetch_rss(url: str, limit: int, source: str, is_foreign: bool) -> list:
    """RSS URL에서 뉴스를 파싱하여 반환합니다.

    Args:
        url: RSS 피드 URL
        limit: 수집할 최대 뉴스 수
        source: 뉴스 소스 이름
        is_foreign: 해외 뉴스 여부

    Returns:
        뉴스 딕셔너리 리스트. 수집 실패 시 빈 리스트 반환.
    """
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            logger.warning("RSS 파싱 오류 (%s): %s", url, feed.bozo_exception)
            return []

        news = []
        for entry in feed.entries[:limit]:
            published = _parse_date(entry)
            news.append({
                "title": entry.get("title", "제목 없음").strip(),
                "link": entry.get("link", ""),
                "published": published,
                "source": source,
                "is_foreign": is_foreign,
            })

        logger.info("뉴스 %d건 수집 완료 (%s)", len(news), source)
        return news

    except Exception as e:
        logger.warning("RSS 수집 실패 (%s): %s", url, e)
        return []


def _parse_date(entry: object) -> str:
    """RSS 엔트리에서 발행일을 파싱합니다.

    Args:
        entry: feedparser 엔트리 객체

    Returns:
        'YYYY-MM-DD' 형식의 날짜 문자열. 파싱 실패 시 KST 오늘 날짜 반환.
    """
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            t = entry.published_parsed
            return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
    except Exception:
        pass
    return datetime.now(KST).strftime("%Y-%m-%d")
