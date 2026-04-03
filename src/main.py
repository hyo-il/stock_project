"""주식 정보 자동화 알림 메인 실행 모듈."""

import logging
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

sys.path.insert(0, "src")

from ai_analyzer import analyze_with_ai
from news_collector import collect_news
from stock_analyzer import collect_stock_data
from telegram_sender import send_message


def main() -> None:
    """전체 실행 흐름을 조율하는 메인 함수.

    1. 뉴스 수집 → 2. 주식 데이터 수집 → 3. AI 분석 → 4. 메시지 포맷팅 → 5. 텔레그램 전송
    """
    logger.info("=== 주식 정보 자동 알림 시작 ===")

    news = []
    try:
        logger.info("1단계: 경제 뉴스 수집 중...")
        news = collect_news(limit=7)
        logger.info("뉴스 %d건 수집 완료", len(news))
    except Exception as e:
        logger.error("뉴스 수집 중 예외 발생: %s", e)

    stocks = {}
    try:
        logger.info("2단계: 주식 데이터 수집 중...")
        stocks = collect_stock_data()
        logger.info("주식 데이터 %d개 지수 수집 완료", len(stocks))
    except Exception as e:
        logger.error("주식 데이터 수집 중 예외 발생: %s", e)

    analysis = None
    try:
        logger.info("3단계: AI 분석 중...")
        analysis = analyze_with_ai(news, stocks)
        if analysis:
            logger.info("AI 분석 완료")
        else:
            logger.info("AI 분석 생략 (API 키 없거나 실패)")
    except Exception as e:
        logger.error("AI 분석 중 예외 발생: %s", e)

    try:
        logger.info("4단계: 메시지 포맷팅 중...")
        message = format_message(news, stocks, analysis)
    except Exception as e:
        logger.error("메시지 포맷팅 중 예외 발생: %s", e)
        message = "⚠️ 메시지 포맷팅 중 오류가 발생했습니다.\n\n⚠️ 본 정보는 투자 권유가 아니며, 투자 판단의 책임은 본인에게 있습니다."

    try:
        logger.info("5단계: 텔레그램 전송 중...")
        success = send_message(message)
        if success:
            logger.info("텔레그램 전송 성공")
        else:
            logger.error("텔레그램 전송 실패")
    except Exception as e:
        logger.error("텔레그램 전송 중 예외 발생: %s", e)

    logger.info("=== 주식 정보 자동 알림 종료 ===")


def format_message(news: list, stocks: dict, analysis: str = None) -> str:
    """텔레그램 전송용 HTML 메시지를 포맷팅합니다.

    Args:
        news: 뉴스 딕셔너리 리스트
        stocks: 주식 지수 딕셔너리
        analysis: AI 분석 결과 문자열 (없으면 섹션 생략)

    Returns:
        HTML 태그가 포함된 텔레그램 메시지 문자열
    """
    today = datetime.today().strftime("%Y-%m-%d")
    lines = [f'📊 <b>오늘의 주식 시장 브리핑</b> ({today})', ""]

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

    # 뉴스
    if news:
        lines.append("📰 <b>오늘의 주요 경제 이슈</b>")
        for i, item in enumerate(news, 1):
            lines.append(f"{i}. {item['title']}")
        lines.append("")

    # AI 분석
    if analysis:
        lines.append("🤖 <b>AI 분석 요약</b>")
        # HTML 특수문자 이스케이프 처리 후 추가
        safe_analysis = (
            analysis
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("**", "")   # Markdown 굵게 제거
            .replace("##", "")   # Markdown 헤더 제거
            .replace("*", "•")   # Markdown 리스트 변환
        )
        lines.append(safe_analysis)
        lines.append("")

    # 면책 문구
    lines.append("⚠️ 본 정보는 투자 권유가 아니며, 투자 판단의 책임은 본인에게 있습니다.")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
