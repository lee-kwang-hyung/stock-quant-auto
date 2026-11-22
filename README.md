# 한국·미국 주식 정량 스캔 · 3년 백테스트 · 시그널 누적 자동화 (`stock_quant_auto_v1_0`)

## 포함된 기능 (1단계 + 2단계 + 3단계 데이터 축적)
1. **1단계 — 평일 매일 오전 07:30 KST 한·미 23종목 정량 스캔 (`scripts/stock_analyzer.py`)**
   - 코스피/코스닥 11종목 + 미국 나스닥/S&P500 12종목 (`config/watchlist.json`에서 자유롭게 추가/수정)
   - 추세 정배열(MA5/20/60), RSI(14), MACD, 거래량 배수, 볼린저밴드 기반 **0~100점 정량 점수 + 뉴스 요약**
   - 결과 저장: `reports/latest_daily_report.md` 및 GitHub Actions Summary (선택: 텔레그램 자동 발송)
2. **2단계 — 매주 토요일 3년 과거 데이터 백테스트 (`scripts/backtester.py`)**
   - 수수료·슬리피지(0.25%), 손절(-5%), 익절(+12%)을 반영한 전략 수익률 vs 단순 보유(Buy & Hold), 연복리(CAGR), 최대낙폭(MDD), 승률 자동 계산
   - 결과 저장: `reports/backtest_summary.md` & `data/backtest_results.csv`
3. **3단계 자동매매 사전 검증용 — 실전 시그널 누적 DB & 자동 채점 (`scripts/track_performance.py`)**
   - 매일 기록된 `data/signals_history.csv`의 과거 시그널이 실제로 **D+1, D+5, D+20 거래일 후 몇 % 수익이 났는지** 자동 채점
   - 2~3개월 이상 실전 적중률 데이터를 쌓은 뒤, 검증된 전략만 증권사 Open API(한국투자증권 KIS 등)에 연결하세요.

## 3분 시작 방법
1. GitHub에 새 비공개(Private) 저장소(예: `stock-quant-auto`)를 만들고 이 ZIP의 모든 파일을 업로드합니다.
2. **Actions** 탭에서 **`daily-kr-us-stock-scan`**과 **`weekly-3y-backtest`**를 각각 1회 **`Run workflow`**로 테스트합니다.
3. *(선택)* 텔레그램 알림을 받으려면 저장소 **Settings → Secrets → Actions**에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`만 추가하면 매일 아침 07:30에 휴대폰으로 자동 도착합니다.

> 면책 안내: 본 시스템은 데이터 기반 투자 참고·연구용 자동화 도구이며, 모든 투자 판단과 책임은 사용자 본인에게 있습니다.
