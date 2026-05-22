"""주식 정보 자동화 알림 메인 실행 모듈 (오전/오후)."""

import logging
import os
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

from ai_analyzer import build_afternoon_briefing, build_morning_briefing
from earnings_collector import collect_upcoming_earnings
from news_collector import collect_all_news
from stock_analyzer import collect_afternoon_stocks, collect_morning_stocks
from telegram_sender import send_message

KST = ZoneInfo("Asia/Seoul")

_DAY_KR = {"Mon": "월", "Tue": "화", "Wed": "수", "Thu": "목",
           "Fri": "금", "Sat": "토", "Sun": "일"}


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------

def get_today_str() -> str:
    """KST 기준 오늘 날짜를 '2026년 04월 10일' 형식으로 반환합니다."""
    return datetime.now(KST).strftime("%Y년 %m월 %d일")


def get_run_mode() -> str:
    """실행 모드를 반환합니다.

    환경변수 RUN_MODE가 설정된 경우 우선 사용.
    없으면 KST 현재 시간으로 자동 판별:
        13시 미만 → 'morning'
        13시 이상 → 'afternoon'
    """
    mode = os.environ.get("RUN_MODE", "").strip().lower()
    if mode in ("morning", "afternoon"):
        return mode
    hour = datetime.now(KST).hour
    return "morning" if hour < 13 else "afternoon"


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main() -> None:
    """전체 실행 흐름을 조율하는 메인 함수."""
    today_str = get_today_str()
    mode = get_run_mode()
    logger.info("=== 주식 정보 자동 알림 시작: %s [%s] ===", today_str, mode)

    if mode == "morning":
        _run_morning(today_str)
    else:
        _run_afternoon(today_str)

    logger.info("=== 주식 정보 자동 알림 종료 ===")


# ---------------------------------------------------------------------------
# 오전 브리핑
# ---------------------------------------------------------------------------

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

    # 2단계: 미국 지수 + 매크로 자산 수집
    stocks = {}
    try:
        logger.info("2단계: 지수 및 매크로 자산 수집 중...")
        stocks = collect_morning_stocks()
        logger.info("시장 데이터 수집 완료: %d개 항목", len(stocks))
    except Exception as e:
        logger.error("시장 데이터 수집 중 예외 발생: %s", e)

    # 3단계: 주요 기업 실적 발표 수집
    earnings = []
    try:
        logger.info("3단계: 주요 기업 실적 발표 수집 중...")
        earnings = collect_upcoming_earnings(days_ahead=7)
        logger.info("실적 발표 수집 완료: %d건", len(earnings))
    except Exception as e:
        logger.error("실적 발표 수집 중 예외 발생: %s", e)

    # 4단계: AI 오전 브리핑 분석
    briefing = None
    try:
        logger.info("4단계: AI 오전 브리핑 분석 중...")
        briefing = build_morning_briefing(
            raw_news["domestic"],
            raw_news["foreign"],
            stocks,
            today_str=today_str,
            earnings=earnings,
        )
        if briefing:
            logger.info("AI 분석 완료")
        else:
            logger.info("AI 분석 생략 (API 키 없거나 실패)")
    except Exception as e:
        logger.error("AI 분석 중 예외 발생: %s", e)

    # 5단계: 메시지 포맷팅 및 전송
    try:
        logger.info("5단계: 메시지 포맷팅 및 전송 중...")
        message = format_morning_message(stocks, briefing, today_str)
        success = send_message(message)
        if success:
            logger.info("텔레그램 전송 성공")
        else:
            logger.error("텔레그램 전송 실패")
    except Exception as e:
        logger.error("메시지 포맷팅·전송 중 예외 발생: %s", e)


# ---------------------------------------------------------------------------
# 오후 브리핑
# ---------------------------------------------------------------------------

def _run_afternoon(today_str: str) -> None:
    """오후 브리핑 실행 흐름."""

    # 1단계: 뉴스 수집 (국내 중심)
    raw_news = {"domestic": [], "foreign": []}
    try:
        logger.info("1단계: 경제 뉴스 수집 중...")
        raw_news = collect_all_news()
        logger.info("뉴스 수집 완료: 국내 %d건, 해외 %d건",
                    len(raw_news["domestic"]), len(raw_news["foreign"]))
    except Exception as e:
        logger.error("뉴스 수집 중 예외 발생: %s", e)

    # 2단계: 국내 지수 + VIX 수집
    stocks = {}
    try:
        logger.info("2단계: 국내 지수 및 VIX 수집 중...")
        stocks = collect_afternoon_stocks()
        logger.info("시장 데이터 수집 완료: %d개 항목", len(stocks))
    except Exception as e:
        logger.error("시장 데이터 수집 중 예외 발생: %s", e)

    # 3단계: 주요 기업 실적 발표 수집
    earnings = []
    try:
        logger.info("3단계: 주요 기업 실적 발표 수집 중...")
        earnings = collect_upcoming_earnings(days_ahead=7)
        logger.info("실적 발표 수집 완료: %d건", len(earnings))
    except Exception as e:
        logger.error("실적 발표 수집 중 예외 발생: %s", e)

    # 4단계: AI 오후 브리핑 분석
    briefing = None
    try:
        logger.info("4단계: AI 오후 브리핑 분석 중...")
        briefing = build_afternoon_briefing(
            raw_news["domestic"],
            stocks,
            earnings=earnings,
            today_str=today_str,
        )
        if briefing:
            logger.info("AI 분석 완료")
        else:
            logger.info("AI 분석 생략 (API 키 없거나 실패)")
    except Exception as e:
        logger.error("AI 분석 중 예외 발생: %s", e)

    # 5단계: 메시지 포맷팅 및 전송
    try:
        logger.info("5단계: 메시지 포맷팅 및 전송 중...")
        message = format_afternoon_message(stocks, briefing, today_str)
        success = send_message(message)
        if success:
            logger.info("텔레그램 전송 성공")
        else:
            logger.error("텔레그램 전송 실패")
    except Exception as e:
        logger.error("메시지 포맷팅·전송 중 예외 발생: %s", e)


# ---------------------------------------------------------------------------
# 오전 메시지 포맷팅
# ---------------------------------------------------------------------------

def format_morning_message(stocks: dict, briefing, today_str: str) -> str:
    """오전 브리핑 텔레그램 HTML 메시지를 포맷팅합니다 (오선 스타일).

    구조:
        헤더
        ① 시장 기조 + 포트폴리오 참고 한줄
        ② 시장 데이터 (오선 스타일 카테고리 분류)
           [미국 증시] S&P500 / 나스닥100 / 다우 / 러셀2000
           [미국 국채] 2년 수익률 / 10년 수익률
           [달러 인덱스]
           [골드]
           [에너지] WTI / 천연가스
           [원/달러]
        ③ 핵심 이슈 3선
        ④ 오늘의 주도 섹터
        ⑤ 스윙 트레이딩 체크포인트
        ⑥ 이번 주 주요 일정
        면책 문구
    """
    now_kst = datetime.now(KST)
    day_kr = _DAY_KR.get(now_kst.strftime("%a"), "")
    lines = [f"📊 <b>{today_str}({day_kr}) 오전 브리핑</b>", ""]

    # ─────────────────────────────────────────────────────────────────────
    # ① 시장 기조
    # ─────────────────────────────────────────────────────────────────────
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

    # ─────────────────────────────────────────────────────────────────────
    # ② 시장 데이터 (오선 스타일)
    # ─────────────────────────────────────────────────────────────────────
    us_data_date = stocks.get("SP500", {}).get("data_date", "")
    date_note = f"  <i>※ {us_data_date} 종가 기준</i>" if us_data_date else ""
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    def _prev(info: dict) -> float:
        """이전 값 = 현재값 - 변동폭"""
        return info["current"] - info["change"]

    def _arrow(change: float) -> str:
        return "▲" if change >= 0 else "▼"

    # ── [미국 증시] ──────────────────────────────────────────────────────
    lines.append(f"📈 <b>[미국 증시]</b>{date_note}")
    us_index_cfg = [
        ("SP500",       "S&amp;P500"),
        ("NASDAQ",      "나스닥100"),
        ("DOW",         "다우"),
        ("RUSSELL2000", "러셀2000"),
    ]
    for key, label in us_index_cfg:
        if key in stocks:
            info = stocks[key]
            sign = "+" if info["change_pct"] >= 0 else ""
            lines.append(
                f"{label} {sign}{info['change_pct']:.2f}% → {info['current']:,.2f}"
            )
    lines.append("")

    # ── [미국 국채] ──────────────────────────────────────────────────────
    rate_items = [(k, lbl) for k, lbl in [("US2Y", "2년 수익률"), ("US10Y", "10년 수익률")]
                  if k in stocks]
    if rate_items:
        lines.append("🏦 <b>[미국 국채]</b>")
        for key, label in rate_items:
            info = stocks[key]
            prev = _prev(info)
            lines.append(
                f"{label} {prev:.3f}% → {info['current']:.3f}%  {_arrow(info['change'])}"
            )
        lines.append("")

    # ── [달러 인덱스] ────────────────────────────────────────────────────
    if "DXY" in stocks:
        info = stocks["DXY"]
        prev = _prev(info)
        lines.append("💵 <b>[달러 인덱스]</b>")
        lines.append(f"{prev:.3f} → {info['current']:.3f}  {_arrow(info['change'])}")
        lines.append("")

    # ── [골드] ───────────────────────────────────────────────────────────
    if "GOLD" in stocks:
        info = stocks["GOLD"]
        prev = _prev(info)
        lines.append("🥇 <b>[골드]</b>")
        lines.append(f"{prev:,.2f} → {info['current']:,.2f}  {_arrow(info['change'])}")
        lines.append("")

    # ── [에너지] ─────────────────────────────────────────────────────────
    energy_items = [(k, lbl, d) for k, lbl, d in
                    [("WTI", "WTI", 2), ("NATGAS", "천연가스", 3)]
                    if k in stocks]
    if energy_items:
        lines.append("🛢️ <b>[에너지]</b>")
        for key, label, decimals in energy_items:
            info = stocks[key]
            prev = _prev(info)
            fmt = f"{{:.{decimals}f}}"
            lines.append(
                f"{label} {fmt.format(prev)} → {fmt.format(info['current'])}  {_arrow(info['change'])}"
            )
        lines.append("")

    # ── [원/달러] ────────────────────────────────────────────────────────
    if "USDKRW" in stocks:
        info = stocks["USDKRW"]
        prev = _prev(info)
        sign = "+" if info["change_pct"] >= 0 else ""
        lines.append("💱 <b>[원/달러]</b>")
        lines.append(
            f"{prev:,.2f} → {info['current']:,.2f}원  "
            f"{_arrow(info['change'])} {sign}{info['change_pct']:.2f}%"
        )
        lines.append("")

    # ─────────────────────────────────────────────────────────────────────
    # AI 분석 없을 때 조기 종료
    # ─────────────────────────────────────────────────────────────────────
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
            why = _safe_html(issue.get("why_important", issue.get("impact", "")))
            swing = _safe_html(issue.get("swing_point", ""))

            lines.append(f"{icon} [{category}] {title}")
            if why:
                lines.append(f"  📌 {why}")
            if swing:
                lines.append(f"  🎯 {swing}")
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

    # ── ⑤ 스윙 체크포인트 ───────────────────────────────────────────────
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
            detail = _safe_html(item.get("detail", ""))
            importance = item.get("importance", 1)
            stars_str = star_map.get(importance, "")
            lines.append(f"• {date} {event}{stars_str}")
            if detail:
                lines.append(f"  {detail}")
        lines.append("")

    lines.append("⚠️ 본 정보는 투자 권유가 아니며 투자 판단의 책임은 본인에게 있습니다.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 오후 메시지 포맷팅
# ---------------------------------------------------------------------------

def format_afternoon_message(stocks: dict, briefing, today_str: str) -> str:
    """오후 브리핑 텔레그램 HTML 메시지를 포맷팅합니다 (오선 스타일).

    구조:
        헤더
        ① 오늘 시장 총평 + VIX 코멘트 + 포트폴리오 한줄
        ② 시장 데이터 (오선 스타일)
           [국내 지수] KOSPI / KOSDAQ
           [공포지수] VIX
        ③ 핵심 이슈 3선
        ④ 오늘의 주도 섹터
        ⑤ 스윙 트레이딩 체크포인트
        ⑥ 이번 주 주요 일정
        면책 문구
    """
    now_kst = datetime.now(KST)
    day_kr = _DAY_KR.get(now_kst.strftime("%a"), "")
    lines = [f"📊 <b>{today_str}({day_kr}) 오후 브리핑</b>", ""]

    # ─────────────────────────────────────────────────────────────────────
    # ① 시장 총평 & VIX 코멘트
    # ─────────────────────────────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    if briefing:
        summary = briefing.get("market_summary", "")
        vix_comment = briefing.get("vix_comment", "")
        portfolio_note = briefing.get("portfolio_note", "")

        if summary:
            lines.append(_safe_html(summary))
        if vix_comment:
            lines.append(f"😨 VIX: {_safe_html(vix_comment)}")
        if portfolio_note:
            lines.append(f"📌 포트폴리오: {_safe_html(portfolio_note)}")
    else:
        lines.append("📍 시장 총평 분석 불가")
    lines.append("")

    # ─────────────────────────────────────────────────────────────────────
    # ② 시장 데이터 (오선 스타일)
    # ─────────────────────────────────────────────────────────────────────
    kr_data_date = stocks.get("KOSPI", {}).get("data_date", "")
    date_note = f"  <i>※ {kr_data_date} 종가 기준</i>" if kr_data_date else ""
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    def _prev(info: dict) -> float:
        return info["current"] - info["change"]

    def _arrow(change: float) -> str:
        return "▲" if change >= 0 else "▼"

    # ── [국내 지수] ──────────────────────────────────────────────────────
    lines.append(f"📈 <b>[국내 지수]</b>{date_note}")
    for key, label in [("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")]:
        if key in stocks:
            info = stocks[key]
            sign = "+" if info["change_pct"] >= 0 else ""
            lines.append(
                f"{label} {sign}{info['change_pct']:.2f}% → {info['current']:,.2f}"
            )
    lines.append("")

    # ── [공포지수] ───────────────────────────────────────────────────────
    vix_info = stocks.get("VIX", {})
    if vix_info:
        vix_val = vix_info["current"]
        prev_vix = round(_prev(vix_info), 2)
        if vix_val >= 30:
            vix_label = "😱 VIX (공포 구간)"
        elif vix_val >= 20:
            vix_label = "😟 VIX (주의 구간)"
        else:
            vix_label = "😊 VIX (안정 구간)"
        lines.append("🌡️ <b>[공포지수]</b>")
        lines.append(
            f"{vix_label}  {prev_vix:.2f} → {vix_val:.2f}  {_arrow(vix_info['change'])}"
        )
        lines.append("")

    # ─────────────────────────────────────────────────────────────────────
    # AI 분석 없을 때 조기 종료
    # ─────────────────────────────────────────────────────────────────────
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
            why = _safe_html(issue.get("why_important", issue.get("impact", "")))
            swing = _safe_html(issue.get("swing_point", ""))

            lines.append(f"{icon} [{category}] {title}")
            if why:
                lines.append(f"  📌 {why}")
            if swing:
                lines.append(f"  🎯 {swing}")
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

    # ── ⑤ 스윙 체크포인트 ───────────────────────────────────────────────
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
            detail = _safe_html(item.get("detail", ""))
            importance = item.get("importance", 1)
            stars_str = star_map.get(importance, "")
            lines.append(f"• {date} {event}{stars_str}")
            if detail:
                lines.append(f"  {detail}")
        lines.append("")

    lines.append("⚠️ 본 정보는 투자 권유가 아니며 투자 판단의 책임은 본인에게 있습니다.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML 이스케이프
# ---------------------------------------------------------------------------

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
