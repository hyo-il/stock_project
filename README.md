# 주식 정보 자동화 알림

향후 60일 주요 이벤트(실적·정책), 주도 섹터, 투자 방향성 제안을 텔레그램으로 전송하는 자동화 시스템입니다.

당일 단기 트레이딩 참고용이 아니라 **향후 이벤트 검토 + 패시브 포트폴리오·스윙 후보 방향성 설정**을 목적으로 합니다. 당일 시장 데이터는 참고용으로 메시지 후반에 배치됩니다.

버전별 변경 이력은 [CHANGELOG.md](CHANGELOG.md), 개발 규칙은 `CLAUDE.md` 를 참조하세요.

## 기능

- **뉴스 수집** — 국내 4개(연합뉴스·매일경제·매경 증권·전자신문) + 해외 4개(CNBC Tech/Economy/Finance·Yahoo Finance) RSS
- **시장 데이터** — 국내 지수(KOSPI·KOSDAQ), 미국 지수(S&P500·나스닥·다우·러셀2000), 매크로 자산(금·달러인덱스·미국채 2년/10년·원달러·WTI·천연가스), VIX
- **실적 수집** — 대형주 78종(미국 76 + 국내 2)의 향후 60일 실적 발표 예정 + EPS/매출 전망치, 컨센서스, 추정치 신뢰도(sanity) 검증
- **AI 분석** — Google Gemini 로 시장 기조·주도 섹터·핵심 테마·향후 일정·액션 포인트 생성 (API 키 없으면 시장 데이터만 전송)
- **텔레그램 전송** — 4,000자 초과 시 자동 분할

## 실행 모드

환경변수 `RUN_MODE` 로 분기합니다. 미설정 시 KST 13시를 경계로 자동 판별합니다.

| 모드 | 내용 | 트리거 |
|------|------|--------|
| `morning` | 전날 미국 시장 마감 + 매크로 자산 (미국 지수 중심) | GitHub Actions cron, 평일 KST **06:55** 자동 |
| `afternoon` | 당일 국내 장 마감 + VIX (국내 지수 중심) | `workflow_dispatch` **수동 전용** |

> ⚠️ GitHub Actions `schedule` 은 정시 도착을 보장하지 않습니다(공식 문서). 본 저장소에서도 수십 분~수 시간의 큐 지연이 관측되며, `06:55` 는 평균 지연 약 60분을 흡수하기 위해 앞당긴 값입니다. 정시 도착이 필요한 회차는 아래 수동 트리거를 사용하세요.

### 수동 실행

| 방법 | 경로 |
|------|------|
| GitHub 웹·모바일 | Actions → "주식 정보 자동 알림" → Run workflow → mode 선택 |
| GitHub CLI | `gh workflow run "주식 정보 자동 알림" -f mode=afternoon` |

## 환경 변수 설정

`.env.example` 을 복사하여 `.env` 파일을 만들고 값을 입력합니다.

```bash
cp .env.example .env
```

| 변수 | 설명 |
|------|------|
| `TELEGRAM_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 텔레그램 수신 Chat ID |
| `GEMINI_API_KEY` | Google Gemini API 키 (없으면 AI 분석 생략) |

### 텔레그램 봇 생성

1. 텔레그램에서 `@BotFather` 에게 `/newbot` 명령 전송
2. 봇 이름과 username 설정
3. 발급된 토큰을 `TELEGRAM_TOKEN` 에 입력
4. 봇에게 메시지를 보낸 후 `https://api.telegram.org/bot<TOKEN>/getUpdates` 에서 Chat ID 확인

### Gemini API 키 발급 (무료)

1. https://aistudio.google.com/apikey 접속 후 `Create API key`
2. 발급된 키를 `GEMINI_API_KEY` 에 입력

> ⚠️ 2026-08 이후 신규 발급된 키는 **gemini-2.5 계열 전체를 사용할 수 없습니다** (`no longer available to new users`). 현재 모델은 `gemini-3.6-flash` 이며 `src/ai_analyzer.py` 의 `MODEL_NAME` 에 고정되어 있습니다. 자세한 내용은 CHANGELOG v1.5.17 참조.

## 로컬 실행

```bash
pip install -r requirements.txt

# 오전 브리핑
RUN_MODE=morning python src/main.py

# 오후 브리핑
RUN_MODE=afternoon python src/main.py
```

> `src/` 모듈은 플랫 임포트 구조입니다. `python src/main.py` 로 실행하세요 (`python -m src.main` 은 동작하지 않습니다).

브리핑 생성 또는 전송이 실패하면 **종료 코드 1** 로 끝나므로, GitHub Actions 에서 실패가 빨간색으로 표시됩니다.

## GitHub Actions 설정

`Settings` → `Secrets and variables` → `Actions` → `New repository secret` 에 아래 값을 등록합니다.

| Secret 이름 | 필수 |
|-------------|------|
| `TELEGRAM_TOKEN` | ✅ |
| `TELEGRAM_CHAT_ID` | ✅ |
| `GEMINI_API_KEY` | 선택 (없으면 AI 분석 생략) |

## 프로젝트 구조

```
stock_project/
├── .github/workflows/stock-alert.yml  # 오전 cron + workflow_dispatch
├── src/
│   ├── main.py                        # 진입점, RUN_MODE 분기, 메시지 포맷팅
│   ├── news_collector.py              # RSS 수집 (국내 4 + 해외 4)
│   ├── stock_analyzer.py              # 지수·매크로 자산·VIX 수집
│   ├── earnings_collector.py          # 실적 발표 예정 + 전망치 수집 (78종)
│   ├── ai_analyzer.py                 # Gemini 브리핑 생성
│   └── telegram_sender.py             # 텔레그램 전송
├── requirements.txt
├── .env.example
├── CHANGELOG.md
└── README.md
```

## 주의사항

- `.env` 파일은 절대 GitHub 에 커밋하지 않습니다
- 본 서비스는 투자 권유가 아니며, 투자 판단의 책임은 본인에게 있습니다
