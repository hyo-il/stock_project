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

from ai_analyzer import analyze_market, select_and_classify_news
from config import SECTORS
from news_collector import collect_all_news
from stock_analyzer import collect_evening_stocks, collect_morning_stocks
from telegram_sender import send_message

KST = ZoneInfo("Asia/Seoul")


def get_today_str() -> str:
    """KST 기준 오늘 날짜를 '2026년 04월 10일' 형식으로 반환합니다."""
    return datetime.now(KST).strftime("%Y년 %m월 %d일")


def get_message_type() -> str:
    """KST 기준 실행 시간으로 오전/오후를 판단합니다.

    Returns:
        "morning" (14시 미만) 또는 "evening" (14시 이상)
    """
    return "morning" if datetime.now(KST).hour < 14 else "evening"


def main() -> None:
    """전체 실행 흐름을 조율하는 메인 함수."""
    today_str = get_today_str()
    message_type = get_message_type()
    logger.info("=== 주식 정보 자동 알림 시작: %s (%s) ===", today_str, message_type)

    # 뉴스 수집 (공통)
    raw_news = {"domestic": [], "foreign": []}
    try:
        logger.info("1단계: 경제 뉴스 수집 중...")
        raw_news = collect_all_news()
        logger.info("뉴스 수집 완료: 국내 %d건, 해외 %d건",
                    len(raw_news["domestic"]), len(raw_news["foreign"]))
    except Exception as e:
        logger.error("뉴스 수집 중 예외 발생: %s", e)

    if message_type == "morning":
        _run_morning(today_str, raw_news)
    else:
        _run_evening(today_str, raw_news)

    logger.info("=== 주식 정보 자동 알림 종료 ===")


def _run_morning(today_str: str, raw_news: dict) -> None:
    """오전 브리핑 실행 흐름."""
    stocks = {}
    try:
        logger.info("2단계: 주식 지수 데이터 수집 중...")
        stocks = collect_morning_stocks()
        logger.info("주식 데이터 %d개 지수 수집 완료", len(stocks))
    except Exception as e:
        logger.error("주식 데이터 수집 중 예외 발생: %s", e)

    sector_news = {s["name"]: [] for s in SECTORS}
    try:
        logger.info("3단계: AI 뉴스 선별 및 섹터 분류 중...")
        sector_news = select_and_classify_news(
            raw_news["domestic"], raw_news["foreign"],
            today_str, domestic_limit=5, foreign_limit=4,
        )
        total = sum(len(v) for v in sector_news.values())
        logger.info("뉴스 선별·분류 완료: 총 %d건", total)
    except Exception as e:
        logger.error("뉴스 선별·분류 중 예외 발생: %s", e)

    analysis = None
    try:
        logger.info("4단계: AI 시장 분석 중...")
        analysis = analyze_market(sector_news, stocks, today_str=today_str, message_type="morning")
        if analysis:
            logger.info("AI 시장 분석 완료")
        else:
            logger.info("AI 분석 생략 (API 키 없거나 실패)")
    except Exception as e:
        logger.error("AI 분석 중 예외 발생: %s", e)

    try:
        logger.info("5단계: 메시지 포맷팅 및 전송 중...")
        message = format_morning_message(sector_news, stocks, analysis, today_str)
        success = send_message(message)
        if success:
            logger.info("텔레그램 전송 성공")
        else:
            logger.error("텔레그램 전송 실패")
    except Exception as e:
        logger.error("메시지 포맷팅·전송 중 예외 발생: %s", e)


def _run_evening(today_str: str, raw_news: dict) -> None:
    """오후 미국 시장 프리뷰 실행 흐름."""
    futures = {}
    try:
        logger.info("2단계: 미국 선물 지수 수집 중...")
        futures = collect_evening_stocks()
        logger.info("선물 지수 %d개 수집 완료", len(futures))
    except Exception as e:
        logger.error("선물 지수 수집 중 예외 발생: %s", e)

    sector_news = {s["name"]: [] for s in SECTORS}
    try:
        logger.info("3단계: AI 해외 뉴스 선별 및 섹터 분류 중...")
        sector_news = select_and_classify_news(
            [], raw_news["foreign"],
            today_str, domestic_limit=0, foreign_limit=6,
        )
        total = sum(len(v) for v in sector_news.values())
        logger.info("뉴스 선별·분류 완료: 총 %d건", total)
    except Exception as e:
        logger.error("뉴스 선별·분류 중 예외 발생: %s", e)

    analysis = None
    try:
        logger.info("4단계: AI 미국 시장 분석 중...")
        analysis = analyze_market(sector_news, futures, today_str=today_str, message_type="evening")
        if analysis:
            logger.info("AI 시장 분석 완료")
        else:
            logger.info("AI 분석 생략 (API 키 없거나 실패)")
    except Exception as e:
        logger.error("AI 분석 중 예외 발생: %s", e)

    try:
        logger.info("5단계: 메시지 포맷팅 및 전송 중...")
        message = format_evening_message(sector_news, futures, analysis, today_str)
        success = send_message(message)
        if success:
            logger.info("텔레그램 전송 성공")
        else:
            logger.error("텔레그램 전송 실패")
    except Exception as e:
        logger.error("메시지 포맷팅·전송 중 예외 발생: %s", e)


def format_morning_message(
    sector_news: dict,
    stocks: dict,
    analysis,
    today_str: str,
) -> str:
    """오전 브리핑 텔레그램 HTML 메시지를 포맷팅합니다."""
    sector_line = " · ".join(f"{s['emoji']} {s['name']}" for s in SECTORS)
    lines = [
        f"📊 <b>주식 시장 브리핑</b> ({today_str} 오전)",
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

    # 섹터별 뉴스 블록
    _append_sector_news_blocks(lines, sector_news)

    # AI 분석
    if analysis:
        lines += ["━━━━━━━━━━━━━━━", "📊 <b>AI 분석 요약</b>", _safe_html(analysis), ""]

    lines.append("⚠️ 본 정보는 투자 권유가 아니며, 투자 판단의 책임은 본인에게 있습니다.")
    return "\n".join(lines)


def format_evening_message(
    sector_news: dict,
    futures: dict,
    analysis,
    today_str: str,
) -> str:
    """오후 미국 시장 프리뷰 텔레그램 HTML 메시지를 포맷팅합니다."""
    sector_line = " · ".join(f"{s['emoji']} {s['name']}" for s in SECTORS)
    lines = [
        f"🌙 <b>미국 시장 프리뷰</b> ({today_str} 오후)",
        f"📌 모니터링 섹터: {sector_line}",
        "",
    ]

    # 미국 선물 동향
    if futures:
        lines.append("📊 <b>미국 선물 동향</b> (현재 기준)")
        for name, data in futures.items():
            arrow = "▲" if data["change"] >= 0 else "▼"
            lines.append(
                f"• {name}: {data['price']:,.2f} {arrow} "
                f"{data['change']:+.2f} ({data['pct']:+.2f}%)"
            )
        lines.append("")

    # 섹터별 해외 뉴스 블록
    _append_sector_news_blocks(lines, sector_news, label_suffix=" 해외 동향")

    # AI 분석
    if analysis:
        lines += ["━━━━━━━━━━━━━━━", "🔍 <b>AI 분석 — 오늘 밤 미국 시장</b>", _safe_html(analysis), ""]

    lines.append("⚠️ AI 분석은 뉴스 기반 참고 정보입니다. 실제 매매 판단은 본인 책임입니다.")
    return "\n".join(lines)


def _append_sector_news_blocks(lines: list, sector_news: dict, label_suffix: str = " 섹터") -> None:
    """섹터별 뉴스 블록을 lines에 추가합니다. 뉴스 없는 섹터도 항상 표시합니다."""
    for sector in SECTORS:
        name = sector["name"]
        emoji = sector["emoji"]
        news_list = sector_news.get(name, [])

        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"{emoji} <b>{name}{label_suffix}</b>")
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


def _safe_html(text: str) -> str:
    """AI 분석 텍스트를 텔레그램 HTML에 안전하게 이스케이프합니다."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("**", "")
        .replace("##", "")
        .replace("*", "•")
    )


if __name__ == "__main__":
    main()
