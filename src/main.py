"""주식 정보 자동화 알림 메인 실행 모듈 (오전 전용)."""

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

from ai_analyzer import build_morning_briefing
from news_collector import collect_all_news
from stock_analyzer import collect_morning_stocks
from telegram_sender import send_message

KST = ZoneInfo("Asia/Seoul")

# 요일 한국어 매핑
_DAY_KR = {"Mon": "월", "Tue": "화", "Wed": "수", "Thu": "목",
           "Fri": "금", "Sat": "토", "Sun": "일"}


def get_today_str() -> str:
    """KST 기준 오늘 날짜를 '2026년 04월 10일' 형식으로 반환합니다."""
    return datetime.now(KST).strftime("%Y년 %m월 %d일")


def main() -> None:
    """전체 실행 흐름을 조율하는 메인 함수."""
    today_str = get_today_str()
    logger.info("=== 주식 정보 자동 알림 시작: %s ===", today_str)
    _run_morning(today_str)
    logger.info("=== 주식 정보 자동 알림 종료 ===")


def _run_morning(today_str: str) -> None:
    """오전 브리핑 실행 흐름."""

    # 1단계: 뉴스 수집
    raw_news = {"domestic": [], "foreign": []}
    try:
        logger.info("1단계: 경제 뉴스 수집 중...")
        raw_news = collect_all_news()
        logger.info("뉴스 수집 완료: 국내 %d건, 해외 %d건",
                    len(raw_news["domestic"]), len(raw_news["foreign"]))
    except Exception as e:
        logger.error("뉴스 수집 중 예외 발생: %s", e)

    # 2단계: 지수 + 매크로 자산 수집
    stocks = {}
    try:
        logger.info("2단계: 지수 및 매크로 자산 수집 중...")
        stocks = collect_morning_stocks()
        logger.info("시장 데이터 수집 완료: %d개 항목", len(stocks))
    except Exception as e:
        logger.error("시장 데이터 수집 중 예외 발생: %s", e)

    # 3단계: AI 오전 브리핑 분석
    briefing = None
    try:
        logger.info("3단계: AI 오전 브리핑 분석 중...")
        briefing = build_morning_briefing(
            raw_news["domestic"],
            raw_news["foreign"],
            stocks,
            today_str=today_str,
        )
        if briefing:
            logger.info("AI 분석 완료")
        else:
            logger.info("AI 분석 생략 (API 키 없거나 실패)")
    except Exception as e:
        logger.error("AI 분석 중 예외 발생: %s", e)

    # 4단계: 메시지 포맷팅 및 전송
    try:
        logger.info("4단계: 메시지 포맷팅 및 전송 중...")
        message = format_morning_message(stocks, briefing, today_str)
        success = send_message(message)
        if success:
            logger.info("텔레그램 전송 성공")
        else:
            logger.error("텔레그램 전송 실패")
    except Exception as e:
        logger.error("메시지 포맷팅·전송 중 예외 발생: %s", e)


# ---------------------------------------------------------------------------
# 메시지 포맷팅
# ---------------------------------------------------------------------------

def format_morning_message(stocks: dict, briefing, today_str: str) -> str:
    """오전 브리핑 텔레그램 HTML 메시지를 포맷팅합니다.

    구조:
        헤더
        ① 시장 기조 + 포트폴리오 참고 한줄
        ② 주요 지수 & 매크로 자산
        ③ 핵심 이슈 3선
        ④ 오늘의 주도 섹터
        ⑤ 스윙 트레이딩 체크포인트
        ⑥ 이번 주 주요 일정
        면책 문구
    """
    now_kst = datetime.now(KST)
    day_kr = _DAY_KR.get(now_kst.strftime("%a"), "")
    lines = [f"📊 <b>{today_str}({day_kr}) 오전 브리핑</b>", ""]

    # ── ① 시장 기조 ──────────────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    if briefing:
        regime = briefing.get("market_regime", "")
        regime_icon = {"Risk-On": "✅", "Risk-Off": "⚠️", "혼조": "🔶"}.get(regime, "📍")
        summary = briefing.get("regime_summary", "")
        portfolio_note = briefing.get("portfolio_note", "")

        lines.append(f"{regime_icon} <b>{_safe_html(regime)}</b>")
        if summary:
            lines.append(_safe_html(summary))
        if portfolio_note:
            lines.append(f"📌 포트폴리오: {_safe_html(portfolio_note)}")
    else:
        lines.append("📍 시장 기조 분석 불가")
    lines.append("")

    # ── ② 주요 지수 & 매크로 자산 ─────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📈 <b>주요 지수</b>")

    index_cfg = [
        ("KOSPI",  "🇰🇷 KOSPI "),
        ("KOSDAQ", "🇰🇷 KOSDAQ"),
        ("SP500",  "🇺🇸 S&amp;P500"),
        ("NASDAQ", "🇺🇸 NASDAQ"),
        ("DOW",    "🇺🇸 DOW   "),
    ]
    for key, label in index_cfg:
        if key in stocks:
            info = stocks[key]
            arrow = "▲" if info["change"] >= 0 else "▼"
            sign = "+" if info["change"] >= 0 else ""
            lines.append(
                f"{label}  {info['current']:>10,.2f}  "
                f"{arrow} {sign}{info['change_pct']:.2f}%"
            )

    lines.append("")
    lines.append("💹 <b>매크로 자산</b>")

    # GOLD, DXY, USDKRW는 소수점 2자리, US10Y는 3자리 + %p 표기
    macro_cfg = [
        ("GOLD",   "🥇 금(달러) ", 2, ""),
        ("DXY",    "💵 달러인덱스", 3, ""),
        ("US10Y",  "📈 미10년물  ", 3, "%"),
        ("USDKRW", "💱 원/달러  ", 2, "원"),
    ]
    for key, label, decimals, unit in macro_cfg:
        if key in stocks:
            info = stocks[key]
            current = info["current"]
            change = info["change"]
            change_pct = info["change_pct"]
            arrow = "▲" if change >= 0 else "▼"
            sign = "+" if change >= 0 else ""
            fmt = f"{{:>10,.{decimals}f}}"

            # US10Y는 변화량을 %p(퍼센트포인트)로 표기
            if key == "US10Y":
                lines.append(
                    f"{label}  {fmt.format(current)}{unit}  "
                    f"{arrow} {sign}{change:.3f}%p"
                )
            else:
                lines.append(
                    f"{label}  {fmt.format(current)}{unit}  "
                    f"{arrow} {sign}{change_pct:.2f}%"
                )
    lines.append("")

    # AI 분석 없는 경우 조기 종료
    if not briefing:
        lines.append("⚠️ AI 분석을 불러올 수 없습니다.")
        lines.append("")
        lines.append("⚠️ 본 정보는 투자 권유가 아니며 투자 판단의 책임은 본인에게 있습니다.")
        return "\n".join(lines)

    # ── ③ 핵심 이슈 ──────────────────────────────────────────────────────
    key_issues = briefing.get("key_issues", [])
    if key_issues:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔍 <b>핵심 이슈</b>")
        for issue in key_issues:
            icon = issue.get("icon", "•")
            category = issue.get("category", "")
            title = _safe_html(issue.get("title", ""))
            impact = _safe_html(issue.get("impact", ""))
            lines.append(f"{icon} [{category}] {title}")
            if impact:
                lines.append(f"  → {impact}")
        lines.append("")

    # ── ④ 오늘의 주도 섹터 ───────────────────────────────────────────────
    leading_sectors = briefing.get("leading_sectors", [])
    if leading_sectors:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🚀 <b>오늘의 주도 섹터</b>")
        for sector in leading_sectors:
            emoji = sector.get("emoji", "")
            name = _safe_html(sector.get("name", ""))
            stars = sector.get("stars", "")
            reason = _safe_html(sector.get("reason", ""))
            stocks_kr = _safe_html(sector.get("stocks_kr", ""))
            stocks_us = _safe_html(sector.get("stocks_us", ""))

            lines.append(f"{emoji} <b>{name}</b> {stars}")
            if reason:
                lines.append(f"  {reason}")
            if stocks_kr:
                lines.append(f"  🇰🇷 {stocks_kr}")
            if stocks_us:
                lines.append(f"  🇺🇸 {stocks_us}")
        lines.append("")

    # ── ⑤ 스윙 트레이딩 체크포인트 ─────────────────────────────────────
    swing = briefing.get("swing_check", {})
    if swing:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🎯 <b>스윙 체크포인트</b>")

        phase = swing.get("phase", "")
        if phase:
            lines.append(f"📍 {_safe_html(phase)}")

        catalysts = swing.get("catalysts", [])
        if catalysts:
            lines.append("⚡ 주목 촉매:")
            for c in catalysts:
                lines.append(f"  • {_safe_html(c)}")

        risks = swing.get("risks", [])
        if risks:
            lines.append("🚨 주요 리스크:")
            for r in risks:
                lines.append(f"  • {_safe_html(r)}")
        lines.append("")

    # ── ⑥ 이번 주 주요 일정 ─────────────────────────────────────────────
    schedule = briefing.get("weekly_schedule", [])
    if schedule:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("📅 <b>이번 주 주요 일정</b>")
        star_map = {1: "", 2: " ⭐", 3: " ⭐⭐"}
        for item in schedule:
            date = item.get("date", "")
            event = _safe_html(item.get("event", ""))
            importance = item.get("importance", 1)
            stars_str = star_map.get(importance, "")
            lines.append(f"• {date} {event}{stars_str}")
        lines.append("")

    lines.append("⚠️ 본 정보는 투자 권유가 아니며 투자 판단의 책임은 본인에게 있습니다.")
    return "\n".join(lines)


def _safe_html(text: str) -> str:
    """텍스트를 텔레그램 HTML 모드에 안전하게 이스케이프합니다."""
    if not isinstance(text, str):
        text = str(text)
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
