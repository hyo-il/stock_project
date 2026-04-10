"""주식 정보 자동화 알림 메인 실행 모듈."""

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

sys.path.insert(0, "src")

from ai_analyzer import analyze_market, classify_news_by_sector
from config import SECTORS
from news_collector import collect_news
from stock_analyzer import collect_stock_data
from telegram_sender import send_message

KST = ZoneInfo("Asia/Seoul")


def get_today_str() -> str:
    """KST 기준 오늘 날짜를 '2026년 04월 10일' 형식으로 반환합니다."""
    return datetime.now(KST).strftime("%Y년 %m월 %d일")


def get_time_period() -> str:
    """현재 시간대에 따라 '오전' 또는 '오후'를 반환합니다."""
    return "오전" if datetime.now(KST).hour < 12 else "오후"


def main() -> None:
    """전체 실행 흐름을 조율하는 메인 함수.

    1. 뉴스 수집 → 2. 주식 데이터 수집 → 3. AI 뉴스 섹터 분류·번역
    → 4. AI 시장 분석 → 5. 메시지 포맷팅 → 6. 텔레그램 전송
    """
    today_str = get_today_str()
    time_period = get_time_period()
    logger.info("=== 주식 정보 자동 알림 시작: %s %s ===", today_str, time_period)

    all_news_raw = []
    try:
        logger.info("1단계: 경제 뉴스 수집 중...")
        all_news_raw = collect_news()
        logger.info("뉴스 %d건 수집 완료 (AI 분류 전)", len(all_news_raw))
    except Exception as e:
        logger.error("뉴스 수집 중 예외 발생: %s", e)

    stocks = {}
    try:
        logger.info("2단계: 주식 데이터 수집 중...")
        stocks = collect_stock_data()
        logger.info("주식 데이터 %d개 지수 수집 완료", len(stocks))
    except Exception as e:
        logger.error("주식 데이터 수집 중 예외 발생: %s", e)

    sector_news = {s["name"]: [] for s in SECTORS}
    try:
        logger.info("3단계: AI 뉴스 섹터 분류 및 번역 중...")
        sector_news = classify_news_by_sector(all_news_raw, today_str=today_str)
        total = sum(len(v) for v in sector_news.values())
        logger.info("뉴스 섹터 분류 완료: 총 %d건", total)
    except Exception as e:
        logger.error("뉴스 분류·번역 중 예외 발생: %s", e)
        sector_news[SECTORS[0]["name"]] = all_news_raw

    analysis = None
    try:
        logger.info("4단계: AI 시장 분석 중...")
        analysis = analyze_market(sector_news, stocks, today_str=today_str)
        if analysis:
            logger.info("AI 시장 분석 완료")
        else:
            logger.info("AI 분석 생략 (API 키 없거나 실패)")
    except Exception as e:
        logger.error("AI 분석 중 예외 발생: %s", e)

    try:
        logger.info("5단계: 메시지 포맷팅 중...")
        message = format_message(sector_news, stocks, analysis, today_str, time_period)
    except Exception as e:
        logger.error("메시지 포맷팅 중 예외 발생: %s", e)
        message = "⚠️ 메시지 포맷팅 중 오류가 발생했습니다.\n\n⚠️ 본 정보는 투자 권유가 아니며, 투자 판단의 책임은 본인에게 있습니다."

    try:
        logger.info("6단계: 텔레그램 전송 중...")
        success = send_message(message)
        if success:
            logger.info("텔레그램 전송 성공")
        else:
            logger.error("텔레그램 전송 실패")
    except Exception as e:
        logger.error("텔레그램 전송 중 예외 발생: %s", e)

    logger.info("=== 주식 정보 자동 알림 종료 ===")


def format_message(
    sector_news: dict,
    stocks: dict,
    analysis,
    today_str: str,
    time_period: str,
) -> str:
    """텔레그램 전송용 HTML 메시지를 포맷팅합니다.

    Args:
        sector_news: 섹터별 뉴스 딕셔너리 (config.SECTORS 기준)
        stocks: 주식 지수 딕셔너리
        analysis: AI 분석 결과 문자열 (없으면 섹션 생략)
        today_str: KST 기준 날짜 문자열 (예: '2026년 04월 10일')
        time_period: '오전' 또는 '오후'

    Returns:
        HTML 태그가 포함된 텔레그램 메시지 문자열
    """
    # 헤더 + 모니터링 섹터 줄
    sector_line = " · ".join(f"{s['emoji']} {s['name']}" for s in SECTORS)
    lines = [
        f"📊 <b>주식 시장 브리핑</b> ({today_str} {time_period})",
        f"📌 모니터링 섹터: {sector_line}",
        "",
    ]

    # 국내 시장
    korean = {k: v for k, v in stocks.items() if k in ("KOSPI", "KOSDAQ")}
    if korean:
        lines.append("📈 <b>국내 시장</b>")
        name_map = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
        for key in ("KOSPI", "KOSDAQ"):
            if key in korean:
                info = korean[key]
                arrow = "▲" if info["change"] >= 0 else "▼"
                sign = "+" if info["change"] >= 0 else ""
                lines.append(
                    f"• {name_map[key]}: {info['current']:,.2f} "
                    f"{arrow} {sign}{info['change']:,.2f} ({sign}{info['change_pct']:.2f}%)"
                )
        lines.append("")

    # 해외 시장
    us = {k: v for k, v in stocks.items() if k in ("SP500", "NASDAQ", "DOW")}
    if us:
        lines.append("🌐 <b>해외 시장 (전일 종가)</b>")
        us_names = {"SP500": "S&amp;P 500", "NASDAQ": "나스닥", "DOW": "다우존스"}
        for key in ("SP500", "NASDAQ", "DOW"):
            if key in us:
                info = us[key]
                arrow = "▲" if info["change"] >= 0 else "▼"
                sign = "+" if info["change"] >= 0 else ""
                lines.append(
                    f"• {us_names[key]}: {info['current']:,.2f} "
                    f"{arrow} {sign}{info['change']:,.2f} ({sign}{info['change_pct']:.2f}%)"
                )
        lines.append("")

    # 섹터별 뉴스 블록 — 뉴스 없는 섹터도 항상 표시
    for sector in SECTORS:
        name = sector["name"]
        emoji = sector["emoji"]
        news_list = sector_news.get(name, [])

        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"{emoji} <b>{name} 섹터</b>")
        lines.append("📰 주요 뉴스")

        if news_list:
            for i, item in enumerate(news_list, 1):
                prefix = "[해외]" if item.get("is_foreign") else "[국내]"
                source = item.get("source", "")
                source_str = f" — {source}" if source else ""
                lines.append(f"{i}. {prefix} {item['title']}{source_str}")
        else:
            lines.append("관련 이슈 없음")

        lines.append("")

    # AI 분석
    if analysis:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("📊 <b>AI 분석 요약</b>")
        safe_analysis = (
            analysis
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("**", "")
            .replace("##", "")
            .replace("*", "•")
        )
        lines.append(safe_analysis)
        lines.append("")

    # 면책 문구
    lines.append("⚠️ 본 정보는 투자 권유가 아니며, 투자 판단의 책임은 본인에게 있습니다.")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
