# 주식 정보 자동화 알림

매일 오전 경제 뉴스와 주식 동향을 수집·분석하여 텔레그램으로 전송하는 자동화 시스템입니다.

## 기능

- 연합뉴스 경제 RSS에서 최신 뉴스 수집
- 코스피, 코스닥, S&P 500, 나스닥, 다우존스 지수 수집
- Google Gemini AI로 시장 분석 (API 키 없으면 생략)
- 텔레그램으로 브리핑 전송
- GitHub Actions로 매일 오전 8시(KST) 자동 실행

## 환경 변수 설정

`.env.example`을 복사하여 `.env` 파일을 만들고 값을 입력합니다.

```bash
cp .env.example .env
```

`.env` 파일:
```
TELEGRAM_TOKEN=텔레그램_봇_토큰
TELEGRAM_CHAT_ID=텔레그램_채팅_ID
GEMINI_API_KEY=Gemini_API_키  # 없으면 AI 분석 생략
```

### 텔레그램 봇 생성

1. 텔레그램에서 `@BotFather`에게 `/newbot` 명령 전송
2. 봇 이름과 username 설정
3. 발급된 토큰을 `TELEGRAM_TOKEN`에 입력
4. 봇에게 메시지를 보낸 후 `https://api.telegram.org/bot<TOKEN>/getUpdates`에서 Chat ID 확인

### Gemini API 키 발급 (무료)

1. https://aistudio.google.com/app/apikey 접속
2. Google 계정으로 로그인
3. `Create API key` 클릭
4. 발급된 키를 `GEMINI_API_KEY`에 입력

## 로컬 실행

```bash
# 패키지 설치
pip install -r requirements.txt

# 전체 실행
python src/main.py
```

## GitHub Actions 설정

1. GitHub에 레포지토리 생성 후 코드 푸시
2. `Settings` → `Secrets and variables` → `Actions` → `New repository secret`에 아래 값 등록:

| Secret 이름 | 설명 |
|-------------|------|
| `TELEGRAM_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 텔레그램 수신 Chat ID |
| `GEMINI_API_KEY` | Google Gemini API 키 (선택) |

3. Actions 탭에서 `Run workflow`로 수동 테스트 가능

## 프로젝트 구조

```
stock-alert/
├── .github/workflows/stock-alert.yml  # GitHub Actions 워크플로우
├── src/
│   ├── main.py                        # 메인 실행 진입점
│   ├── news_collector.py              # 경제 뉴스 수집
│   ├── stock_analyzer.py              # 주식 데이터 수집
│   ├── ai_analyzer.py                 # Gemini AI 분석
│   └── telegram_sender.py             # 텔레그램 메시지 전송
├── requirements.txt
├── .env.example
└── README.md
```

## 주의사항

- `.env` 파일은 절대 GitHub에 커밋하지 않습니다
- 본 서비스는 투자 권유가 아니며, 투자 판단의 책임은 본인에게 있습니다
