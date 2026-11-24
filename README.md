# 나만의 주식 비서 v4.0 (안드로이드 단독 APK + 정밀 분석 엔진)

## v4.0 정밀 분석 업그레이드
- **시장 국면 필터**: KOSPI·S&P500 지수 추세(강세/혼조/약세)를 판별해 약세장에서는 보수적으로 점수 조정
- **지지·저항선 기반 정밀 매매가**: 20·60일 지지선·저항선과 ATR을 결합해 매수 구간·손절가·1/2차 목표가 산출 및 **손익비 1.5 미만 자동 제외**
- **재무·공시 신뢰도 필터**: KRX/네이버 재무(PER·PBR·흑자 여부) + 미국 기업 재무 + (선택) 금감원 DART 공시 연동으로 적자·유상증자 위험주 경고
- **사후 적중률 투명 공개**: 과거 신호의 실제 5거래일 승률을 앱 상단에 자동 집계

---

## 안드로이드 단독 어플(`.apk`) 설치 방법 (3분 완료)
1. 이 ZIP 압축을 풀어 `lee-kwang-hyung/stock-quant-auto` 저장소에 덮어쓰기 후 커밋·푸시합니다.
2. **나만 보게 비밀번호 설정**: 저장소 **Settings → Secrets → Actions → New repository secret**에서 `APP_PIN`에 본인 전용 비밀번호 저장 *(선택: `DART_API_KEY` 추가 가능)*
3. **데이터 호스팅 켜기**: **Settings → Pages → Branch: `main` / `/docs` 선택 후 Save**
4. **안드로이드 APK 자동 빌드 & 설치**:
   - **Actions → `build-android-apk` → Run workflow** 실행 (약 3~4분 소요)
   - 완료 후 하단 **Artifacts → `MyStockAssistant-v4.0-APK`** 클릭해 다운로드
   - `MyStockAssistant-v4.0.apk` 파일을 안드로이드 폰에 옮겨 설치하면 **일반 어플과 똑같이 단독 실행**됩니다!
5. **최신 시세 반영**: **Actions → `intraday-live-pwa-scan` → Run workflow** 1회 실행 후, 이후부터 평일 장중(09:00~15:45) 15분마다 자동 갱신됩니다.
