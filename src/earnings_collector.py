"""주요 기업 실적 발표 일정 + 전망치 수집 모듈 (yfinance 기반).

v1.5.6:
    - 티커 풀 18개 → 약 75개 (EXTENDED_TICKERS, 섹터 태그 포함)
    - 수집 기간 7일 → 90일 (향후 3개월)
    - 전망치 4종 (EPS 추정 / 매출 추정 / 전년 동기 실제 EPS / 애널리스트 컨센서스)
    - 2-phase ThreadPoolExecutor 병렬 수집
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")


def _yf_earnings_dates_safe(ticker: str, per_call_timeout: float = 10.0):
    """yfinance Ticker.earnings_dates 호출에 스레드 타임아웃을 강제합니다.

    수집 실패 시 None 을 반환하여 호출부에서 skip 처리하도록 합니다.
    """
    import concurrent.futures
    import yfinance as yf

    def _call():
        return yf.Ticker(ticker).earnings_dates

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_call)
        try:
            return fut.result(timeout=per_call_timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("yfinance %s earnings_dates 타임아웃 (%.1fs)", ticker, per_call_timeout)
            return None
        except Exception as e:
            logger.warning("yfinance %s earnings_dates 예외: %s", ticker, e)
            return None


def _yf_revenue_estimate_safe(ticker: str, per_call_timeout: float = 10.0):
    """yfinance Ticker.revenue_estimate 호출에 스레드 타임아웃을 강제합니다.

    Returns:
        {"avg": float, "year_ago_revenue": float|None}  다음 분기(+1q) 기준.
        실패 시 None.
    """
    import concurrent.futures
    import yfinance as yf

    def _call():
        re = yf.Ticker(ticker).revenue_estimate
        if re is None or re.empty:
            return None
        # 다음 분기(+1Q) 행의 'avg' / 'yearAgoRevenue' 컬럼 추출
        # 인덱스가 ['0q','+1q','0y','+1y'] 형태이거나 비슷한 경우
        try:
            if '+1q' in re.index:
                row = re.loc['+1q']
            else:
                row = re.iloc[0]
            avg          = float(row['avg'])      if 'avg' in row and row['avg'] == row['avg']      else None
            year_ago_rev = float(row['yearAgoRevenue']) if 'yearAgoRevenue' in row and row['yearAgoRevenue'] == row['yearAgoRevenue'] else None
            if avg is None:
                return None
            return {"avg": avg, "year_ago_revenue": year_ago_rev}
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_call)
        try:
            return fut.result(timeout=per_call_timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("yfinance %s revenue_estimate 타임아웃", ticker)
            return None
        except Exception as e:
            logger.warning("yfinance %s revenue_estimate 예외: %s", ticker, e)
            return None


def _yf_earnings_history_safe(ticker: str, per_call_timeout: float = 10.0):
    """직전 분기 + 전년 동기 실제 EPS 를 가져옵니다.

    earnings_dates DataFrame 의 발표 완료 행에서 직전 분기(iloc[0])와
    4분기 전(iloc[3], 전년 동기) Reported EPS 를 추출합니다.

    Returns:
        {"last_quarter": float|None, "year_ago": float|None}. 실패 시 None.
    """
    import concurrent.futures
    import yfinance as yf

    def _call():
        ed = yf.Ticker(ticker).earnings_dates
        if ed is None or ed.empty:
            return None
        # 이미 발표된(Reported EPS 가 NaN 이 아닌) 행만 시간순 내림차순 정렬
        past = ed[ed["Reported EPS"].notna()].copy()
        if past.empty:
            return None
        past = past.sort_index(ascending=False)
        # iloc[0]=직전 분기, iloc[3]=4분기 전(전년 동기)
        try:
            last_q = float(past.iloc[0]["Reported EPS"]) if len(past) >= 1 else None
            yoy    = float(past.iloc[3]["Reported EPS"]) if len(past) >= 4 else None
            return {"last_quarter": last_q, "year_ago": yoy}
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_call)
        try:
            return fut.result(timeout=per_call_timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("yfinance %s earnings_history 타임아웃", ticker)
            return None
        except Exception as e:
            logger.warning("yfinance %s earnings_history 예외: %s", ticker, e)
            return None


def _yf_recommendations_safe(ticker: str, per_call_timeout: float = 10.0):
    """애널리스트 컨센서스 등급 분포를 가져옵니다.

    Returns:
        {"strong_buy": int, "buy": int, "hold": int, "sell": int, "strong_sell": int, "total": int}
        실패 시 None.
    """
    import concurrent.futures
    import yfinance as yf

    def _call():
        rec = yf.Ticker(ticker).recommendations
        if rec is None or rec.empty:
            return None
        # 가장 최근 'period=0m' 행 사용 (현재 컨센서스)
        try:
            if 'period' in rec.columns:
                row = rec[rec['period'] == '0m']
                if row.empty:
                    row = rec.iloc[[0]]
                row = row.iloc[0]
            else:
                row = rec.iloc[0]
            sb = int(row.get('strongBuy', 0))
            b  = int(row.get('buy', 0))
            h  = int(row.get('hold', 0))
            s  = int(row.get('sell', 0))
            ss = int(row.get('strongSell', 0))
            return {
                "strong_buy":  sb,
                "buy":         b,
                "hold":        h,
                "sell":        s,
                "strong_sell": ss,
                "total":       sb + b + h + s + ss,
            }
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_call)
        try:
            return fut.result(timeout=per_call_timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("yfinance %s recommendations 타임아웃", ticker)
            return None
        except Exception as e:
            logger.warning("yfinance %s recommendations 예외: %s", ticker, e)
            return None


def _yf_calendar_safe(ticker: str, per_call_timeout: float = 10.0):
    """yfinance Ticker.calendar 에서 다음 분기 EPS 컨센서스 분산을 가져옵니다.

    Returns:
        {"eps_avg": float, "eps_high": float, "eps_low": float}
        실패 시 None.
    """
    import concurrent.futures
    import yfinance as yf

    def _call():
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return None
        # calendar 는 dict 형태로 'Earnings Average', 'Earnings High', 'Earnings Low' 제공
        try:
            eps_avg  = cal.get("Earnings Average")
            eps_high = cal.get("Earnings High")
            eps_low  = cal.get("Earnings Low")
            if eps_avg is None:
                return None
            return {
                "eps_avg":  float(eps_avg)  if eps_avg  is not None else None,
                "eps_high": float(eps_high) if eps_high is not None else None,
                "eps_low":  float(eps_low)  if eps_low  is not None else None,
            }
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_call)
        try:
            return fut.result(timeout=per_call_timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("yfinance %s calendar 타임아웃", ticker)
            return None
        except Exception as e:
            logger.warning("yfinance %s calendar 예외: %s", ticker, e)
            return None


# 주요 미국 대형주 티커 → (한국어 기업명, 한국어 섹터명) 매핑
# v1.5.6: 18개 → 75개로 확장. 섹터 태그 추가. AI가 주도섹터 기반 필터링 수행.
EXTENDED_TICKERS = {
    # ── 빅테크 / AI ─────────────────────────────────────────
    "AAPL":  ("애플",            "빅테크/AI"),
    "MSFT":  ("마이크로소프트",  "빅테크/AI"),
    "GOOGL": ("알파벳(구글)",    "빅테크/AI"),
    "META":  ("메타",            "빅테크/AI"),
    "AMZN":  ("아마존",          "빅테크/AI"),
    "NVDA":  ("엔비디아",        "빅테크/AI"),

    # ── 반도체 ─────────────────────────────────────────────
    "AMD":   ("AMD",             "반도체"),
    "INTC":  ("인텔",            "반도체"),
    "AVGO":  ("브로드컴",        "반도체"),
    "QCOM":  ("퀄컴",            "반도체"),
    "TXN":   ("텍사스인스트루먼트", "반도체"),
    "AMAT":  ("어플라이드머티어리얼즈", "반도체"),
    "MU":    ("마이크론",        "반도체"),
    "ASML":  ("ASML",            "반도체"),

    # ── 소프트웨어 / 엔터프라이즈 ──────────────────────────
    "ORCL":  ("오라클",          "소프트웨어"),
    "CRM":   ("세일즈포스",      "소프트웨어"),
    "ADBE":  ("어도비",          "소프트웨어"),
    "NOW":   ("서비스나우",      "소프트웨어"),
    "INTU":  ("인튜이트",        "소프트웨어"),
    "IBM":   ("IBM",             "소프트웨어"),
    "CSCO":  ("시스코",          "소프트웨어"),

    # ── 자동차 / EV ────────────────────────────────────────
    "TSLA":  ("테슬라",          "자동차/EV"),
    "F":     ("포드",            "자동차/EV"),
    "GM":    ("제너럴모터스",    "자동차/EV"),

    # ── 소비재 (리테일 / 이커머스) ─────────────────────────
    "WMT":   ("월마트",          "소비재(리테일)"),
    "COST":  ("코스트코",        "소비재(리테일)"),
    "TGT":   ("타겟",            "소비재(리테일)"),
    "HD":    ("홈디포",          "소비재(리테일)"),
    "LOW":   ("로우스",          "소비재(리테일)"),

    # ── 음식료 / 생활용품 ──────────────────────────────────
    "PG":    ("P&G",             "음식료/생활"),
    "KO":    ("코카콜라",        "음식료/생활"),
    "PEP":   ("펩시",            "음식료/생활"),
    "MCD":   ("맥도날드",        "음식료/생활"),
    "SBUX":  ("스타벅스",        "음식료/생활"),
    "NKE":   ("나이키",          "음식료/생활"),
    "CL":    ("콜게이트",        "음식료/생활"),

    # ── 금융 (은행 / 카드) ─────────────────────────────────
    "JPM":   ("JP모건",          "금융"),
    "BAC":   ("뱅크오브아메리카",  "금융"),
    "WFC":   ("웰스파고",        "금융"),
    "C":     ("씨티그룹",        "금융"),
    "GS":    ("골드만삭스",      "금융"),
    "MS":    ("모건스탠리",      "금융"),
    "BLK":   ("블랙록",          "금융"),
    "V":     ("비자",            "금융"),
    "MA":    ("마스터카드",      "금융"),
    "AXP":   ("아메리칸익스프레스", "금융"),

    # ── 헬스케어 / 제약 ────────────────────────────────────
    "UNH":   ("유나이티드헬스",  "헬스케어/제약"),
    "JNJ":   ("존슨앤존슨",      "헬스케어/제약"),
    "LLY":   ("일라이릴리",      "헬스케어/제약"),
    "MRK":   ("머크",            "헬스케어/제약"),
    "PFE":   ("화이자",          "헬스케어/제약"),
    "ABBV":  ("애브비",          "헬스케어/제약"),
    "TMO":   ("써모피셔",        "헬스케어/제약"),
    "ABT":   ("애보트",          "헬스케어/제약"),
    "BMY":   ("브리스톨마이어스", "헬스케어/제약"),
    "AMGN":  ("암젠",            "헬스케어/제약"),

    # ── 산업 / 항공 / 방산 ─────────────────────────────────
    "BA":    ("보잉",            "산업/항공/방산"),
    "RTX":   ("RTX",             "산업/항공/방산"),
    "LMT":   ("록히드마틴",      "산업/항공/방산"),
    "GE":    ("GE",              "산업/항공/방산"),
    "HON":   ("하니웰",          "산업/항공/방산"),
    "CAT":   ("캐터필러",        "산업/항공/방산"),
    "UPS":   ("UPS",             "산업/항공/방산"),
    "DE":    ("디어",            "산업/항공/방산"),

    # ── 미디어 / 통신 ──────────────────────────────────────
    "NFLX":  ("넷플릭스",        "미디어/통신"),
    "DIS":   ("디즈니",          "미디어/통신"),
    "T":     ("AT&T",            "미디어/통신"),
    "VZ":    ("버라이즌",        "미디어/통신"),
    "CMCSA": ("컴캐스트",        "미디어/통신"),

    # ── 에너지 ─────────────────────────────────────────────
    "XOM":   ("엑슨모빌",        "에너지"),
    "CVX":   ("셰브론",          "에너지"),
    "COP":   ("코노코필립스",    "에너지"),
    "SLB":   ("슐럼버거",        "에너지"),

    # ── 유틸리티 / 리츠 ────────────────────────────────────
    "NEE":   ("넥스트에라",      "유틸리티/리츠"),
    "AMT":   ("아메리칸타워",    "유틸리티/리츠"),
    "PLD":   ("프로로지스",      "유틸리티/리츠"),
}


def collect_upcoming_earnings(days_ahead: int = 60) -> list:
    """향후 N일 이내 주요 기업 실적 발표 일정 + 전망치를 수집합니다.

    v1.5.10 변경:
        - 기본 기간 90일 → 60일 (옵션 IV·컨센서스 revision 활발기 기준)
    v1.5.6~v1.5.8 변경:
        - 기본 기간 7일 → 90일 (v1.5.6)
        - 티커 풀 18개 → 약 75개 (EXTENDED_TICKERS)
        - 섹터 태그 포함
        - 전망치 4종 (EPS, Revenue, YoY 실제, Recommendations)
        - ThreadPoolExecutor 로 병렬화

    Args:
        days_ahead: 오늘부터 며칠 앞까지 조회할지 (기본 90일)

    Returns:
        [
            {
                "ticker":         str,         # 예: "AAPL"
                "name_kr":        str,         # 예: "애플"
                "sector_kr":      str,         # 예: "빅테크/AI"
                "earnings_date":  str,         # 예: "06/15(화)"
                "days_until":     int,         # 오늘부터 며칠 후
                "eps_estimate":   str,         # 예: "$1.57" 또는 "-"
                "revenue_estimate": str,       # 예: "$30.2B" 또는 "-" (revenue_sanity_flag 시 "-")
                "yoy_eps_actual": str,         # 전년 동기 실제 EPS, "$1.30" 또는 "-"
                "recommendation": str,         # 예: "Buy (28/35)" 또는 "-"
                # v1.5.7 신규
                "last_quarter_eps_actual": str,  # 직전 분기 실제 EPS, "$12.20" 또는 "-"
                "eps_dispersion": str,           # 컨센서스 분산 "분산 ±46%" (30%+ 일 때만), 아니면 "-"
                "eps_sanity_flag": bool,         # 직전분기 대비 2.5배 초과/0.4배 미만 또는 분산 50%+ 시 True
                "revenue_sanity_flag": bool,     # 전년동기 대비 3배 초과/0.3배 미만 시 True
            },
            ...
        ]
        날짜 오름차순 정렬. 수집 실패 항목은 결과에서 제외.
    """
    import concurrent.futures
    import time as _time

    today = datetime.now(KST).date()
    end_date = today + timedelta(days=days_ahead)

    _WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

    def _fmt_date(d) -> str:
        try:
            if hasattr(d, "date"):
                d = d.date()
            return f"{d.month:02d}/{d.day:02d}({_WEEKDAY_KR[d.weekday()]})"
        except Exception:
            return ""

    def _fmt_money(value, suffix: str = "") -> str:
        """달러 금액을 사람이 읽기 쉬운 형식으로 변환. 1.0e9 → '$1.0B'."""
        if value is None:
            return "-"
        try:
            v = float(value)
            if abs(v) >= 1e12:
                return f"${v/1e12:.2f}T{suffix}"
            if abs(v) >= 1e9:
                return f"${v/1e9:.2f}B{suffix}"
            if abs(v) >= 1e6:
                return f"${v/1e6:.2f}M{suffix}"
            return f"${v:.2f}{suffix}"
        except Exception:
            return "-"

    def _fmt_eps(value) -> str:
        if value is None:
            return "-"
        try:
            return f"${float(value):.2f}"
        except Exception:
            return "-"

    def _fmt_recommendation(rec_dict) -> str:
        if not rec_dict or rec_dict.get("total", 0) == 0:
            return "-"
        total = rec_dict["total"]
        strong_buy_buy = rec_dict["strong_buy"] + rec_dict["buy"]
        hold = rec_dict["hold"]
        sell_total = rec_dict["sell"] + rec_dict["strong_sell"]
        # 우세 등급 결정
        if strong_buy_buy >= max(hold, sell_total):
            label = "Buy"
            count = strong_buy_buy
        elif sell_total >= hold:
            label = "Sell"
            count = sell_total
        else:
            label = "Hold"
            count = hold
        return f"{label} ({count}/{total})"

    # 단계 1: 모든 티커의 earnings_dates 병렬 수집 (가벼움)
    _loop_start = _time.monotonic()
    _PHASE1_TIMEOUT = 120.0  # 1단계 전체 상한 (초)

    phase1_results = {}  # ticker → {date_obj, eps_estimate_raw}

    def _phase1_one(ticker: str):
        ed = _yf_earnings_dates_safe(ticker, per_call_timeout=10.0)
        if ed is None or ed.empty:
            return None
        future = ed[ed["Reported EPS"].isna()].copy()
        if future.empty:
            return None
        future.index = future.index.tz_localize(None) if future.index.tz else future.index
        future_dates = future.index.date
        mask = (future_dates >= today) & (future_dates <= end_date)
        upcoming = future[mask]
        if upcoming.empty:
            return None
        # 가장 가까운 예정일 1건 선택
        dt_idx = upcoming.index[0]
        eps_est = upcoming.loc[dt_idx, "EPS Estimate"]
        d_obj = dt_idx.date() if hasattr(dt_idx, "date") else dt_idx
        return {
            "date_obj":         d_obj,
            "eps_estimate_raw": float(eps_est) if eps_est == eps_est else None,
        }

    tickers = list(EXTENDED_TICKERS.keys())
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        future_to_ticker = {ex.submit(_phase1_one, t): t for t in tickers}
        for fut in concurrent.futures.as_completed(future_to_ticker, timeout=_PHASE1_TIMEOUT):
            if _time.monotonic() - _loop_start > _PHASE1_TIMEOUT:
                logger.warning("earnings phase1 상한 %ss 초과 — 나머지 스킵", _PHASE1_TIMEOUT)
                break
            t = future_to_ticker[fut]
            try:
                r = fut.result(timeout=5)
                if r:
                    phase1_results[t] = r
            except Exception as e:
                logger.warning("phase1 %s 실패: %s", t, e)

    logger.info("phase1 수집 완료: %d/%d 티커에 향후 %d일 내 실적 예정",
                len(phase1_results), len(tickers), days_ahead)

    # 단계 2: 발표 예정 티커에 대해서만 전망치 3종 추가 수집
    _PHASE2_TIMEOUT = 120.0

    def _phase2_one(ticker: str):
        rev_dict = _yf_revenue_estimate_safe(ticker)  # {"avg", "year_ago_revenue"} or None
        eh_dict  = _yf_earnings_history_safe(ticker)  # {"last_quarter", "year_ago"} or None
        rec      = _yf_recommendations_safe(ticker)
        cal_dict = _yf_calendar_safe(ticker)          # {"eps_avg","eps_high","eps_low"} or None
        return {
            "revenue_dict":        rev_dict,
            "earnings_hist_dict":  eh_dict,
            "recommendation_dict": rec,
            "calendar_dict":       cal_dict,
        }

    _phase2_start = _time.monotonic()
    phase2_extras = {}
    upcoming_tickers = list(phase1_results.keys())
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        future_to_ticker = {ex.submit(_phase2_one, t): t for t in upcoming_tickers}
        for fut in concurrent.futures.as_completed(future_to_ticker, timeout=_PHASE2_TIMEOUT):
            if _time.monotonic() - _phase2_start > _PHASE2_TIMEOUT:
                logger.warning("earnings phase2 상한 %ss 초과 — 나머지 스킵", _PHASE2_TIMEOUT)
                break
            t = future_to_ticker[fut]
            try:
                phase2_extras[t] = fut.result(timeout=5)
            except Exception as e:
                logger.warning("phase2 %s 실패: %s", t, e)
                phase2_extras[t] = {
                    "revenue_dict":        None,
                    "earnings_hist_dict":  None,
                    "recommendation_dict": None,
                    "calendar_dict":       None,
                }

    # 단계 3: 결과 조합 + sanity 체크
    results = []
    for t, p1 in phase1_results.items():
        name_kr, sector_kr = EXTENDED_TICKERS[t]
        p2 = phase2_extras.get(t, {})

        rev_dict   = p2.get("revenue_dict") or {}
        eh_dict    = p2.get("earnings_hist_dict") or {}
        rec_dict   = p2.get("recommendation_dict")
        cal_dict   = p2.get("calendar_dict") or {}

        eps_est_raw       = p1["eps_estimate_raw"]
        revenue_avg_raw   = rev_dict.get("avg")
        year_ago_revenue  = rev_dict.get("year_ago_revenue")
        last_quarter_eps  = eh_dict.get("last_quarter")
        year_ago_eps      = eh_dict.get("year_ago")
        eps_high          = cal_dict.get("eps_high")
        eps_low           = cal_dict.get("eps_low")

        # ── EPS sanity 체크 ────────────────────────────────────
        eps_sanity_flag = False
        # 룰 1: 직전 분기 EPS 대비 2.5배 초과 또는 0.4배 미만
        if eps_est_raw is not None and last_quarter_eps is not None and last_quarter_eps > 0:
            ratio = eps_est_raw / last_quarter_eps
            if ratio > 2.5 or ratio < 0.4:
                eps_sanity_flag = True
        # 룰 2: 컨센서스 분산 (high - low) / (2 * avg) > 50%
        if eps_high is not None and eps_low is not None and eps_est_raw is not None and eps_est_raw > 0:
            dispersion = (eps_high - eps_low) / (2 * eps_est_raw)
            if dispersion > 0.5:
                eps_sanity_flag = True

        # ── Revenue sanity 체크 ────────────────────────────────
        revenue_sanity_flag = False
        if revenue_avg_raw is not None and year_ago_revenue is not None and year_ago_revenue > 0:
            rev_ratio = revenue_avg_raw / year_ago_revenue
            # 정상 분기 매출 성장은 통상 0.3 ~ 3 사이.
            if rev_ratio > 3 or rev_ratio < 0.3:
                revenue_sanity_flag = True

        # ── 분산 표시 문자열 ───────────────────────────────────
        eps_dispersion_str = "-"
        if eps_high is not None and eps_low is not None and eps_est_raw is not None and eps_est_raw > 0:
            spread_pct = (eps_high - eps_low) / (2 * eps_est_raw) * 100
            if spread_pct >= 30:
                # 30% 이상일 때만 노출 ("분산 ±N%")
                eps_dispersion_str = f"분산 ±{spread_pct:.0f}%"

        days_until = (p1["date_obj"] - today).days
        results.append({
            "ticker":                    t,
            "name_kr":                   name_kr,
            "sector_kr":                 sector_kr,
            "earnings_date":             _fmt_date(p1["date_obj"]),
            "days_until":                days_until,
            # 기존 4종 (v1.5.6)
            "eps_estimate":              _fmt_eps(eps_est_raw),
            "revenue_estimate":          "-" if revenue_sanity_flag else _fmt_money(revenue_avg_raw),
            "yoy_eps_actual":            _fmt_eps(year_ago_eps),
            "recommendation":            _fmt_recommendation(rec_dict),
            # v1.5.7 신규
            "last_quarter_eps_actual":   _fmt_eps(last_quarter_eps),
            "eps_dispersion":            eps_dispersion_str,
            "eps_sanity_flag":           eps_sanity_flag,
            "revenue_sanity_flag":       revenue_sanity_flag,
        })

    results.sort(key=lambda x: x["days_until"])
    logger.info("실적 발표 예정 수집 완료: %d건 (sanity flag EPS: %d / REV: %d)",
                len(results),
                sum(1 for r in results if r["eps_sanity_flag"]),
                sum(1 for r in results if r["revenue_sanity_flag"]))
    return results
