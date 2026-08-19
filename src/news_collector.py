"""경제 뉴스 RSS 수집 모듈 (국내 + 해외)."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# v1.5.18: 소스 전면 교체.
# 기존 7개 피드 중 5개가 응답 불능 상태였음 (2026-08-19 실측):
#   - feeds.reuters.com/* 3종 → DNS 해석 실패 (Reuters 는 공개 RSS 를 종료함)
#   - www.etnews.com/rss/rss.xml → 빈 응답
#   - world.kbs.co.kr 폴백 → 빈 응답 (안전망이 함께 죽어 있었음)
# 살아있는 소스로 교체하고 국내/해외 각 4개로 이중화.
DOMESTIC_RSS = [
    {"url": "https://www.yonhapnewstv.co.kr/category/news/economy/feed/", "source": "연합뉴스"},
    {"url": "https://www.mk.co.kr/rss/30100041/", "source": "매일경제"},
    {"url": "https://www.mk.co.kr/rss/50200011/", "source": "매경 증권"},
    # etnews 는 rss.etnews.com 으로 이전됨. Section903 = 증권·기업 (901/904 는 일반·연예 혼재)
    {"url": "https://rss.etnews.com/Section903.xml", "source": "전자신문"},
]

FOREIGN_RSS = [
    # Tech / AI반도체 중심
    {"url": "https://www.cnbc.com/id/19854910/device/rss/rss.html", "source": "CNBC Tech"},
    # 매크로·연준·금리·환율
    {"url": "https://www.cnbc.com/id/20910258/device/rss/rss.html", "source": "CNBC Economy"},
    # 증권·금융·기업
    {"url": "https://www.cnbc.com/id/10000664/device/rss/rss.html", "source": "CNBC Finance"},
    # 종합 (지정학 포함)
    {"url": "https://finance.yahoo.com/news/rssindex", "source": "Yahoo Finance"},
]

# 국내 RSS 가 전부 실패했을 때만 사용하는 폴백
DOMESTIC_FALLBACK = {"url": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=02", "source": "SBS 경제"}


def collect_all_news(per_source: int = 20) -> dict:
    """국내·해외 뉴스를 분리하여 수집합니다.

    Args:
        per_source: 각 RSS 소스에서 수집할 최대 뉴스 수

    Returns:
        {"domestic": [뉴스 dict 리스트], "foreign": [뉴스 dict 리스트]}
    """
    domestic = _collect_from_sources(DOMESTIC_RSS, per_source_limit=per_source, is_foreign=False)
    if not domestic:
        logger.warning("국내 RSS 전체 실패, %s 로 폴백합니다.", DOMESTIC_FALLBACK["source"])
        domestic = _fetch_rss(
            DOMESTIC_FALLBACK["url"],
            limit=per_source, source=DOMESTIC_FALLBACK["source"], is_foreign=False,
        )

    foreign = _collect_from_sources(FOREIGN_RSS, per_source_limit=per_source, is_foreign=True)

    # v1.5.18: 피드가 조용히 죽어도 알아채지 못했던 이력이 있어 경고를 남긴다.
    if not domestic:
        logger.error("국내 뉴스를 한 건도 수집하지 못했습니다 (폴백 포함 전부 실패).")
    if not foreign:
        logger.error("해외 뉴스를 한 건도 수집하지 못했습니다.")

    logger.info("전체 뉴스 수집: 국내 %d건, 해외 %d건", len(domestic), len(foreign))
    return {"domestic": domestic, "foreign": foreign}


def _collect_from_sources(sources: list, per_source_limit: int, is_foreign: bool) -> list:
    """여러 RSS 소스에서 뉴스를 수집합니다."""
    all_news = []
    for src in sources:
        news = _fetch_rss(src["url"], limit=per_source_limit, source=src["source"], is_foreign=is_foreign)
        all_news.extend(news)
    return all_news


def _fetch_rss(url: str, limit: int, source: str, is_foreign: bool) -> list:
    """RSS URL에서 뉴스를 파싱하여 반환합니다."""
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
    """RSS 엔트리에서 발행일을 파싱합니다."""
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            t = entry.published_parsed
            return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
    except Exception:
        pass
    return datetime.now(KST).strftime("%Y-%m-%d")
