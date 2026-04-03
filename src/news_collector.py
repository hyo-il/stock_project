"""경제 뉴스 RSS 수집 모듈."""

import logging
from datetime import datetime

import feedparser

logger = logging.getLogger(__name__)

PRIMARY_RSS_URL = "https://www.yonhapnewstv.co.kr/category/news/economy/feed/"
FALLBACK_RSS_URL = "https://world.kbs.co.kr/rss/rss_economy.xml"


def collect_news(limit: int = 7) -> list[dict]:
    """경제 뉴스 RSS에서 최신 뉴스를 수집합니다.

    1순위: 연합뉴스 경제 RSS, 실패 시 KBS 경제 RSS로 폴백합니다.
    뉴스 제목, 링크, 발행일만 수집합니다 (저작권 준수).

    Args:
        limit: 수집할 최대 뉴스 수

    Returns:
        뉴스 딕셔너리 리스트. 각 항목은 title, link, published 키를 가집니다.
    """
    news = _fetch_rss(PRIMARY_RSS_URL, limit)
    if not news:
        logger.warning("연합뉴스 RSS 수집 실패, KBS RSS로 폴백합니다.")
        news = _fetch_rss(FALLBACK_RSS_URL, limit)

    if not news:
        logger.error("모든 뉴스 RSS 수집에 실패했습니다.")

    return news


def _fetch_rss(url: str, limit: int) -> list[dict]:
    """RSS URL에서 뉴스를 파싱하여 반환합니다.

    Args:
        url: RSS 피드 URL
        limit: 수집할 최대 뉴스 수

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
            })

        logger.info("뉴스 %d건 수집 완료 (%s)", len(news), url)
        return news

    except Exception as e:
        logger.warning("RSS 수집 실패 (%s): %s", url, e)
        return []


def _parse_date(entry: object) -> str:
    """RSS 엔트리에서 발행일을 파싱합니다.

    Args:
        entry: feedparser 엔트리 객체

    Returns:
        'YYYY-MM-DD' 형식의 날짜 문자열. 파싱 실패 시 오늘 날짜 반환.
    """
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            t = entry.published_parsed
            return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
    except Exception:
        pass
    return datetime.today().strftime("%Y-%m-%d")
