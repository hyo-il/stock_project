# 개발 내역 (CHANGELOG)

주식 정보 자동화 알림 프로젝트의 버전별 변경 이력입니다.
상세 설계 규칙과 금지 사항은 `CLAUDE.md` 를 참조하세요.

---

## v1.5.17 — 2026-08-19

**Gemini 2.5 계열 차단 대응, `gemini-3.6-flash` 이전**

다른 프로젝트 정리 과정에서 기존 API 키가 삭제되어 AI 분석이 전량 실패.
새 키를 발급했으나 **2.5 계열 전체가 신규 키에서 차단**된 상태였음.

```
gemini-2.5-flash       HTTP 404  "no longer available to new users"
gemini-2.5-flash-lite  HTTP 404  (동일)
gemini-2.5-pro         HTTP 404  (동일)
gemini-3.6-flash       OK
```

키 재발급으로 해결 불가 — 계정이 아니라 **키 생성 시점 기준의 신규 사용자 게이트**.
따라서 모델 이전이 유일한 선택지였음.

### 변경

- `MODEL_NAME`: `gemini-2.5-flash` → `gemini-3.6-flash`
- `_make_json_gen_config()`: `thinking_config` 제거
  - 2.5 의 `thinking_budget=0` 은 3.x 에서 `INVALID_ARGUMENT` (HTTP 400) 유발
  - `google-genai` 1.47.0 (PyPI 최신) 에는 3.x 용 `thinking_level` 파라미터가 아직 없어 생략이 정답

### 검증 (실측, 2026-08-19 오전 브리핑 실제 프롬프트)

| 항목 | 값 |
|------|-----|
| prompt | 6,770 |
| thoughts | 4,640 |
| output (candidates) | 2,211 = 8192 한도의 **27.0%** |
| total | 13,621 |
| 재시도 | 0회, `finishReason=STOP` |

3.x 는 **thinking 토큰이 `max_output_tokens` 를 잠식하지 않음**.
2.5 시절 `thinking_budget=0` 이 막으려던 "응답 잘림" 문제가 구조적으로 사라짐.
전 파이프라인 실행 + 텔레그램 전송 성공 확인 (뉴스 31건 / 시장 12항목 / 실적 25건 / 필수 키 8종 정상).

### 후속 조치

- GitHub Actions Secret 의 `GEMINI_API_KEY` 를 새 키로 갱신 필요 (저장소 코드와 별개)

---

## v1.5.16 — 2026-07-09

**upcoming_schedule 슬롯 3중 개선**

AI 응답 후처리를 3단계로 확립 (호출 순서 고정):

1. `_filter_upcoming_by_leading_sectors` — 주도섹터 필터 (국내 종목 whitelist 예외)
2. `_reassign_upcoming_slots_by_days` — 실제 오늘 대비 `days` 로 슬롯 재배치 (AI 오분류 보정)
3. `_cap_and_sort_by_sector_priority` — 섹터별 상한(this_week·this_month 3 / next_2_months 2) + 주도섹터 별점 순 정렬

`earnings_text` 에 `[D+N]` 필드 추가로 AI 슬롯 배치 정확도 향상.

---

## v1.5.13 ~ v1.5.15 — 2026-06-26 ~ 2026-07-08

**시각 조정 + 국내 티커 편입 + 주도섹터 정의 확장**

- **v1.5.13**: 오전 cron `07:55` → `06:55` KST. v1.5.11 운영에서 평균 55~65분 큐 지연이 일관 관측되어 1시간 앞당김
- **v1.5.14**: EXTENDED_TICKERS 에 국내 종목 첫 편입 (78개 = 미국 76 + 국내 2)
  - `005930.KS` 삼성전자, `000660.KS` SK하이닉스 (반도체)
  - 편입 기준 수립: 분할상장 주주가치 훼손 계열 제외(LG 계열), 실적 부진 대형주 제외(네이버·카카오), 신규 편입은 사용자 명시 승인 필수
  - yfinance 국내 종목 특이사항 대응: `earnings_dates` US/Eastern → KST 변환, `_fmt_eps`/`_fmt_money` 의 KRW(₩·조·억) 분기 처리
- **v1.5.15**: `leading_sectors` 선정 기준 확장 — (A) 지속 상승 모멘텀 + (B) 방향 무관 시장 집중 관심. 국내 종목(.KS/.KQ)은 주도섹터 필터에서 whitelist 예외 처리

---

## v1.5.12 — 2026-06-23

**Gemini 재시도 백오프 추가**

6/23 오전 HTTP 503 (high demand spike) 대응.
재시도 루프를 **3회 + 선형 백오프(5초, 10초)** 로 강화 (`MAX_AI_ATTEMPTS=3`, `_ai_retry_backoff()`).
최대 추가 대기 15초로 workflow `timeout-minutes: 20` 대비 영향 없음.

---

## v1.5.11 — 2026-06-22

**오전 07:55 변경 + 오후 schedule cron 제거**

- 오후 자동 발사 제거 → `workflow_dispatch` 의 `mode=afternoon` 수동 트리거로 전환
- 정각 `:00`·반정각 `:30` 회피로 큐 지연 완화 목표 (`:55` 채택)
- 오후 관련 코드(`_run_afternoon` / `format_afternoon_message` / `collect_afternoon_stocks`)는 미래 부활 대비 보존

---

## v1.5.6 ~ v1.5.10 — 2026-06-22

**데이터 확장 · 방향성 재배치 · 메시지 압축 · 60일 + 주도섹터 필터 (통합 릴리스)**

프로젝트 목적을 재정의한 가장 큰 변곡점.
"당일 단기 트레이딩 참고" → **"향후 이벤트 검토 + 패시브 포트폴리오·스윙 후보 방향성 설정"**.

- **v1.5.6**: `schedule` 트리거 부활 (사용자 결정 — 자동화 우선, 큐 지연 trade-off 감수). `_yf_revenue_estimate_safe` / `_yf_earnings_history_safe` / `_yf_recommendations_safe` 헬퍼 추가
- **v1.5.7**: 메시지 구조 전면 재배치 — 향후 일정을 ②로 승격, 시장 데이터를 ⑤로 하향
  - `key_issues` → `key_themes` (duration 필드 포함) **호환성 깨짐 변경**
  - `portfolio_adjustment` (액션 포인트 ⑦) 신규
  - 실적 sanity 체크 도입: `eps_sanity_flag` / `revenue_sanity_flag` / `eps_dispersion` / `last_quarter_eps_actual` (마이크론 MU 등 yfinance forward-estimate 노이즈 대응)
  - `_yf_calendar_safe` 헬퍼 추가
- **v1.5.8**: 실적 detail 압축형 포맷 확정, `swing_check.catalysts` 와 `upcoming_schedule` 중복 금지, 글자 수 한도(passive_note 80자 / swing_candidates 100자)
- **v1.5.10**: 수집 기간 30일 → **60일**, `upcoming_schedule` 3슬롯 구조(`this_week`/`this_month`/`next_2_months`) 확립, 주도섹터 사후 필터 강제

---

## v1.5.5 — 2026-06-04

**수동 트리거 전용 전환 (schedule 제거)**

오후 트리거가 약 **4시간 50분** 지연 도착한 사례 발생.
GitHub Actions `schedule` 은 정시 보장이 없다는 공식 문서 확인 후 cron 전면 제거.
→ v1.5.6 에서 사용자 결정으로 부활.

---

## v1.5.3 — 2026-06-02

**yfinance 행(hang) 방지 보호장치**

- 모든 yfinance 호출을 `_yf_*_safe` 헬퍼 경유로 강제 (스레드 타임아웃)
- workflow `timeout-minutes: 20` + `cancel-in-progress: true` 추가 (큐잉 보호)
- earnings_collector 2-phase 수집 구조 도입 — phase1(전 티커 가벼움) + phase2(예정자만 전망치). Actions 20분 상한 내 수렴 보장의 핵심

---

## v1.5.0 ~ v1.5.1 — 2026-05-22 ~ 2026-05-28

**시장 데이터 포맷 개편 및 전송 시각 조정**

- '오선 스타일' 포맷 채택: `등락률% → 현재값`
- 신규 자산 추가: RUSSELL2000, US2Y(FRED API 경유 — yfinance 에 2년물 직접 티커 없음), WTI(`CL=F`), NATGAS(`NG=F`)
- 카테고리 헤더 이모지 접두사
- 전송 시각 조정 (오전 07:30 → 07:00, 오후 16:30 → 16:00) — Actions 지연 흡수 목적

---

## v1.4.0 — 2026-05-01

**오전/오후 브리핑 분리 + 실적 자동 수집 + VIX**

- `RUN_MODE=morning|afternoon` 환경변수 분기 도입 (미설정 시 KST 13시 기준 자동 판별)
- `earnings_collector.py` 신규 — 주요 티커 실적 발표 예정 자동 수집
- 오후 브리핑에 VIX 공포지수 추가

---

## v1.3.0 ~ v1.3.1 — 2026-04-18 ~ 2026-05-01

**매크로 자산 추가 + AI 단일 호출 통합**

- `select_and_classify_news()` + `analyze_market()` 분리 구조 → **단일 `build_morning_briefing()`** 으로 통합
- 매크로 자산 확장 (금, 달러인덱스, 미10년물, 원/달러)
- `config.py` 의 `SECTORS` 제거 — **고정 섹터 폐지, Gemini 가 뉴스 기반으로 주도 섹터 자유 선정**
- 메시지 포맷 개편

---

## v1.2.0 ~ v1.2.2 — 2026-04-11 ~ 2026-04-18

- 오전 전용 전환
- 섹터별 주목 종목 잘림 수정
- 해외 뉴스 RSS 확장

---

## v1.1.0 — 2026-04-10

초기 기능 확장.

---

## v1.0.0 — 2026-04-03

**최초 구축**

연합뉴스 RSS + 국내/미국 지수 수집 → Gemini 분석 → 텔레그램 전송,
GitHub Actions 자동 실행 파이프라인.

---

## 부록: 반복된 퇴행(regression) 기록

같은 실수가 재발한 이력이 있어 `CLAUDE.md` 에 금지 규칙으로 명문화된 항목들입니다.

| 퇴행 | 발생 | 현재 규칙 |
|------|------|-----------|
| 구 SDK(`google-generativeai`) 로 회귀 | v8~v11 | 신규 SDK(`google-genai`) 고정, 구 SDK 금지 |
| `thinking_budget=0` 제거로 응답 잘림 | 2.5 시절 | **v1.5.17 부터 반대로 뒤집힘** — 3.x 에서는 이 인자가 HTTP 400 유발하므로 부활 금지 |
| cron 요일 `0-4` → `1-5` 변경 | — | 월요일 누락·토요일 발송 오류 유발, 변경 금지 |
| `key_issues` → `key_themes` 미동기화 | v1.5.7 | ai_analyzer + main.py 항상 동시 수정 |
| `weekly_schedule` (flat list) 회귀 | v1.5.5 이전 | 3슬롯 dict 구조 유지 |
