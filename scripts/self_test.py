"""v2.0 오프라인 전수 검증: 문법 + ATR 매매 시나리오 + 5대 촉매/전종목 급등초입 + 3년 백테스트 + D+N 추적."""
import pathlib
import py_compile
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

for sp in sorted((ROOT / "scripts").glob("*.py")):
    py_compile.compile(str(sp), doraise=True)
print("[PASS 1/6] 전체 파이썬 스크립트 문법 검증 통과")

import stock_analyzer as sa
import backtester as bt
import track_performance as tp
from indicators import compute_trade_plan


def make_synthetic_ohlcv(symbol: str, days: int = 360) -> pd.DataFrame:
    rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
    dates = pd.bdate_range("2025-01-01", periods=days)
    drift = 0.0008 if ("NVDA" in symbol or "005930" in symbol) else 0.0003
    rets = rng.normal(drift, 0.018, size=days)
    base = 70000.0 if symbol.endswith(".KS") else 150.0
    close = base * np.cumprod(1.0 + rets)
    high = close * (1.0 + rng.uniform(0.002, 0.02, size=days))
    low = close * (1.0 - rng.uniform(0.002, 0.02, size=days))
    open_p = close * (1.0 + rng.uniform(-0.01, 0.01, size=days))
    vol = rng.integers(500_000, 3_000_000, size=days).astype(float)
    return pd.DataFrame({"Open": open_p, "High": high, "Low": low, "Close": close, "Volume": vol}, index=dates)


plan_kr = compute_trade_plan(50000, 1800, 47000, market="KR")
assert "원" in plan_kr["entry_zone"] and "1 :" in plan_kr["rr_ratio"]
print(f"[PASS 2/6] ATR 기반 실전 매매 시나리오(진입·손절·1/2차 목표·손익비) 계산 검증 통과 ({plan_kr['rr_ratio']})")

with tempfile.TemporaryDirectory() as td:
    td_p = pathlib.Path(td)
    out_d, data_d = td_p / "reports", td_p / "data"

    fake_movers = {
        "date": "20261123",
        "breakout": [{
            "ticker": "012345", "name": "샘플수주테크", "close": 12500, "chg_pct": 8.4,
            "trade_val_eok": 320.5, "news_title": "대규모 공급계약 체결", "news_link": "https://example.com",
            **compute_trade_plan(12500, 500, 11700, market="KR"),
        }],
        "limit_up": [{
            "ticker": "099999", "name": "샘플상한바이오", "close": 8900, "chg_pct": 29.93,
            "trade_val_eok": 510.0, "news_title": "임상 2상 결과 발표 상한가", "news_link": "https://example.com",
        }],
    }

    rep, rows = sa.run_analysis(
        ROOT / "config/watchlist.json",
        out_d,
        data_d,
        fetcher=lambda sym, period="6mo": make_synthetic_ohlcv(sym, 160),
        news_fetcher=lambda q, market="KR", limit=2: [{"title": f"{q} 뉴스", "link": "https://example.com", "pub": ""}],
        full_scanner=lambda: fake_movers,
    )
    rep_txt = rep.read_text(encoding="utf-8")
    assert "샘플수주테크" in rep_txt and "샘플상한바이오" in rep_txt and "진입 참고 구간" in rep_txt
    print("[PASS 3/6] v2.0 5대 투자 촉매 뉴스 · 국장 급등초입 · 상한가 원인 · 실전 가격표 생성 검증 통과")

    md_bt, bt_rows = bt.run_backtest(ROOT / "config/watchlist.json", out_d, data_d, fetcher=lambda sym: make_synthetic_ohlcv(sym, 520))
    assert md_bt.exists() and len(bt_rows) >= 30
    print(f"[PASS 4/6] 2단계 3년 백테스트 검증 통과 ({len(bt_rows)}종목)")

    hist_csv = data_d / "signals_history.csv"
    hist_csv.write_text(hist_csv.read_text(encoding="utf-8").replace(rows[0]["date"], "2025-06-01"), encoding="utf-8")
    res_tp = tp.update_signal_performance(hist_csv, fetcher=lambda sym, start_date: make_synthetic_ohlcv(sym, 360)[["Close"]])
    assert res_tp["updated"] > 0
    print(f"[PASS 5/6] 3단계 준비용 D+1/D+5/D+20 사후 수익률 자동 채점 검증 통과 ({res_tp['updated']}셀)")

from kr_price_feed import parse_naver_fchart_xml
sample_xml = '<protocol><chartdata><item data="20261120|245000|249000|244000|247000|812345"/><item data="20261123|246000|248500|245500|247000|512345"/></chartdata></protocol>'
df_nv = parse_naver_fchart_xml(sample_xml)
assert len(df_nv) == 2 and float(df_nv["Close"].iloc[-1]) == 247000.0
# v1.0(16컬럼) -> v2.1(20컬럼) 마이그레이션 무오류 검증
with tempfile.TemporaryDirectory() as td2:
    cpath = pathlib.Path(td2) / "signals_history.csv"
    cpath.write_text("date,market,symbol,name,sector,close,ret_1d,ret_20d,rsi,vol_ratio,score,grade,reasons,d1_ret_pct,d5_ret_pct,d20_ret_pct\n2026-11-20,KR,005380.KS,현대차,자동차,247000,0,0,50,1,60,관찰,중립,,,\n", encoding="utf-8")
    sa.append_signals_history([{"date": "2026-11-23", "market": "KR", "symbol": "005380.KS", "name": "현대차", "sector": "자동차", "close": 247000, "ret_1d": 0, "ret_20d": 0, "rsi": 50, "vol_ratio": 1, "score": 60, "grade": "관찰", "reasons": "중립", "entry_zone": "243,295원~248,235원", "stop_loss": "238,355원", "target_1": "262,500원", "target_2": "272,900원"}], cpath)
    tp.update_signal_performance(cpath, fetcher=lambda s, d: df_nv[["Close"]])
print("[PASS 6/6] v2.1 네이버 금융 KRX 1원 정확 시세 파서 & 구버전 CSV 자동 마이그레이션 검증 통과")
print("=== ALL 6 STOCK QUANT V2.1 SELF-TESTS PASSED ===")


import json as _json_v3

def test_pwa_aes_roundtrip_and_beginner_schema():
    from build_pwa_feed import build_and_save_pwa_feed, decrypt_envelope
    sample_rows = [
        {
            "market": "KR", "symbol": "005930.KS", "name": "삼성전자", "sector": "반도체",
            "close": 75000, "ret_1d": 2.4, "ret_20d": 8.1, "score": 76, "grade": "매수 관심",
            "reasons": "정배열 상승 / 거래량 증가", "entry_zone": "73,500~75,500원",
            "stop_loss": "72,300원 (-3.5%)", "target_1": "79,700원 (+6.3%)", "target_2": "82,900원 (+10.5%)",
        },
        {
            "market": "US", "symbol": "NVDA", "name": "NVIDIA", "sector": "AI",
            "close": 140.5, "ret_1d": -1.2, "ret_20d": 4.0, "score": 32, "grade": "주의",
            "reasons": "단기 과열", "entry_zone": "$138~$141",
            "stop_loss": "$135 (-3.5%)", "target_1": "$149 (+6.3%)", "target_2": "$155 (+10.5%)",
        },
    ]
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "live_feed.json"
        build_and_save_pwa_feed(sample_rows, {"breakout": [], "limit_up": []}, [], out_path=p, pin="MySecret2026!")
        env = _json_v3.loads(p.read_text(encoding="utf-8"))
        assert env["encrypted"] is True and env["alg"] == "AES-256-GCM"
        failed = False
        try:
            decrypt_envelope(env, "WrongPin999")
        except Exception:
            failed = True
        assert failed, "Wrong PIN should fail decryption"
        dec = decrypt_envelope(env, "MySecret2026!")
        assert dec["summary"]["buy_count"] == 1
        assert len(dec["recommendations"]) == 1 and dec["recommendations"][0]["name"] == "삼성전자"
        assert dec["recommendations"][0]["badge"] == "분할매수 관심"
    print("[PASS 7/8] PWA 초보자 스키마 + AES-256-GCM 암호화/복호화 및 오답 PIN 차단 검증 통과")


def test_pwa_static_assets():
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    man = _json_v3.loads((ROOT / "docs/manifest.json").read_text(encoding="utf-8"))
    sw = (ROOT / "docs/sw.js").read_text(encoding="utf-8")
    assert "crypto.subtle" in idx and "PBKDF2" in idx and "AES-GCM" in idx
    assert "오늘 추천" in idx and "매수 구간" in idx and "목표 매도가" in idx and "손절 기준가" in idx
    assert man["display"] == "standalone"
    assert "live_feed.json" in sw
    print("[PASS 8/8] PWA 앱 화면(index.html·manifest·sw.js) 및 Web Crypto 복호화 검증 통과")


test_pwa_aes_roundtrip_and_beginner_schema()
test_pwa_static_assets()
print("=== ALL 8 STOCK QUANT V3.0 PRIVATE PWA SELF-TESTS PASSED ===")


def test_v35_trusted_fundamentals():
    from trusted_fundamentals import parse_kr_fundamental_html, score_fundamentals
    sample_html = '<html><body><em id="_per">12.5</em><em id="_pbr">1.15</em><em id="_market_sum">450조 1,000억</em></body></html>'
    f = parse_kr_fundamental_html(sample_html)
    assert f["per"] == 12.5 and f["pbr"] == 1.15
    good = score_fundamentals(dict(f), market="KR", disclosures=[{"report_nm": "단일판매·공급계약체결"}])
    assert good["trust_label"] == "재무 탄탄" and good["trust_bonus"] > 0
    bad = score_fundamentals({"per": -15.0, "pbr": 3.0}, market="KR", disclosures=[{"report_nm": "유상증자 결정"}])
    assert "주의" in bad["trust_label"] and bad["trust_bonus"] < 0
    print("[PASS 9/10] v3.5 KRX 재무 파서·흑자/적자 판별 및 DART 위험공시(유상증자) 경고 검증 통과")


def test_v35_issue_engine_and_oneclick():
    from issue_catalyst_engine import score_issue_mover
    good_m = score_issue_mover({
        "name": "테스트조선", "ticker": "000001", "close": 50000, "chg_pct": 6.5,
        "trade_val_eok": 420, "news_title": "[연합뉴스] 테스트조선, 대규모 미 해군 함정 수주 계약",
    })
    assert good_m["issue_score"] >= 72 and not good_m["risk_flag"]
    bad_m = score_issue_mover({
        "name": "위험소형주", "ticker": "000002", "close": 3000, "chg_pct": 25.0,
        "trade_val_eok": 30, "news_title": "위험소형주 대규모 유상증자 결정",
    })
    assert bad_m["risk_flag"] is True and bad_m["issue_score"] < 50
    # Windows 로컬 실행기(.bat)는 모바일/GitHub Actions 전용 환경에서 생략 가능
    assert (ROOT / "scripts/run_local_app.py").exists()
    print("[PASS 10/10] v3.5 신뢰 언론/수급 이슈 채점·위험주 필터링 및 PC 원클릭 실행기 검증 통과")


test_v35_trusted_fundamentals()
test_v35_issue_engine_and_oneclick()
print("=== ALL 10 STOCK QUANT V3.5 TRUSTED ONE-CLICK SELF-TESTS PASSED ===")


def test_v40_precision_engine():
    from precision_engine import detect_market_regime, compute_sr_precision_plan, compute_historical_accuracy
    df_up = make_synthetic_ohlcv("005930.KS", 120)
    reg = detect_market_regime(df_up, "국장(KOSPI)")
    assert reg["regime"] in ("강세장", "혼조세", "약세 주의")
    plan = compute_sr_precision_plan(df_up, market="KR")
    assert plan["rr_ratio"] >= 1.5 and "원" in plan["entry_zone"] and "%" in plan["stop_loss"]
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "hist.csv"
        p.write_text("date,symbol,d5_ret_pct\n2026-11-01,005930.KS,3.2\n2026-11-02,NVDA,-1.1\n2026-11-03,005380.KS,2.5\n", encoding="utf-8")
        acc = compute_historical_accuracy(p)
        assert acc["tracked"] == 3 and acc["win_rate"] == 66.7
    print(f"[PASS 11/12] v4.0 시장 국면 필터·지지/저항 정밀 매매가(손익비 1:{plan['rr_ratio']})·사후 승률 집계 검증 통과")


def test_v40_android_apk_project():
    req_files = [
        "android/settings.gradle",
        "android/build.gradle",
        "android/app/build.gradle",
        "android/app/src/main/AndroidManifest.xml",
        "android/app/src/main/java/com/mystock/assistant/MainActivity.java",
        ".github/workflows/build-android-apk.yml",
    ]
    for rf in req_files:
        assert (ROOT / rf).exists(), f"Missing {rf}"
    wf = (ROOT / ".github/workflows/build-android-apk.yml").read_text(encoding="utf-8")
    assert "assembleDebug" in wf and "MyStockAssistant-v5.1.apk" in wf
    print("[PASS 12/12] v4.0 안드로이드 단독 APK 프로젝트 및 GitHub Actions 자동 빌드 구성 검증 통과")


test_v40_precision_engine()
test_v40_android_apk_project()
print("=== ALL 12 STOCK QUANT V4.0 ANDROID PRECISION SELF-TESTS PASSED ===")


def test_v45_investor_flow():
    from trusted_fundamentals import parse_kr_investor_flow_html, score_investor_flow
    html_buy = """<table class="type2">
      <tr><td>2026.11.24</td><td>75,000</td><td>1,000</td><td>+1.3%</td><td>100000</td><td>+50,000</td><td>+120,000</td></tr>
      <tr><td>2026.11.23</td><td>74,000</td><td>500</td><td>+0.7%</td><td>90000</td><td>+30,000</td><td>+80,000</td></tr>
    </table>"""
    fl = parse_kr_investor_flow_html(html_buy, days=5)
    assert fl["days"] == 2 and fl["inst_5d"] == 80000 and fl["frgn_5d"] == 200000
    sc = score_investor_flow(fl)
    assert sc["flow_bonus"] == 10 and "쌍끌이" in sc["flow_label"]

    sc_bad = score_investor_flow({"days": 5, "inst_5d": -10000, "frgn_5d": -25000})
    assert sc_bad["flow_bonus"] == -8 and "주의" in sc_bad["flow_label"]
    print("[PASS 13/14] v4.5 외국인·기관 5일 수급 파서 및 쌍끌이 매수/동반 매도 점수화 검증 통과")


def test_v45_position_sizing_and_portfolio_ui():
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert "calcPositionSize" in idx and "budgetMan" in idx and "riskPct" in idx
    assert "tab-my" in idx and "내 보유" in idx and "손절 기준가" in idx and "목표 매도가" in idx
    wf = (ROOT / ".github/workflows/build-android-apk.yml").read_text(encoding="utf-8")
    assert "MyStockAssistant-v5.1.apk" in wf
    print("[PASS 14/14] v4.5 투자금 기반 자동 매수량 계산기·내 보유 익절/손절 트래커·APK v4.5 구성 검증 통과")


test_v45_investor_flow()
test_v45_position_sizing_and_portfolio_ui()
print("=== ALL 14 STOCK QUANT V4.5 PRO PROFIT KIT SELF-TESTS PASSED ===")


def test_v46_portfolio_and_refresh():
    import os
    from build_pwa_feed import build_and_save_pwa_feed, decrypt_envelope
    os.environ["MY_HOLDINGS_JSON"] = _json_v3.dumps([{"name": "카카오", "symbol": "035720.KS", "buyPrice": 119742, "qty": 33}])
    try:
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "live_feed.json"
            build_and_save_pwa_feed([], {}, [], out_path=p, pin="TestPin46!")
            env = _json_v3.loads(p.read_text(encoding="utf-8"))
            dec = decrypt_envelope(env, "TestPin46!")
            assert len(dec["my_holdings"]) == 1 and dec["my_holdings"][0]["qty"] == 33
    finally:
        os.environ.pop("MY_HOLDINGS_JSON", None)
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert "RAW_FEED_URL" in idx and "showToast" in idx and "calcWatering" in idx and "총 평가손익" in idx
    bg = (ROOT / "android/app/build.gradle").read_text(encoding="utf-8")
    assert "kotlin-bom:1.8.22" in bg and "minifyEnabled false" in bg
    print("[PASS 15/15] v4.6 암호화 보유종목(MY_HOLDINGS_JSON)·새로고침 캐시우회·물타기/손익계산·APK v4.6 검증 통과")


test_v46_portfolio_and_refresh()
print("=== ALL 15 STOCK QUANT V4.6 SELF-TESTS PASSED ===")


def test_v47_realtime_bridge_and_beginner_ui():
    java = (ROOT / "android/app/src/main/java/com/mystock/assistant/MainActivity.java").read_text(encoding="utf-8")
    assert "AndroidStockBridge" in java and "fetchLiveQuotes" in java and "onLiveQuotesUpdated" in java
    assert "triggerAppRefresh" in java and "QuoteFetcher.fetchSingleQuote" in java
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    for tok in ("computeBeginnerAction", "buildPriceGaugeHTML", "onLiveQuotesUpdated", "AndroidStockBridge", "guideModal", "editMyPortfolio", "① 살 때", "② 팔 때", "③ 멈출 때"):
        assert tok in idx, f"Missing v4.7 UI token: {tok}"
    bg = (ROOT / "android/app/build.gradle").read_text(encoding="utf-8")
    assert "versionCode 51" in bg and "versionName '5.1'" in bg
    print("[PASS 16/16] v4.7 안드로이드 실시간 시세 브릿지·초보자 신호등/게이지/3단계 매매 UI 검증 통과")


test_v47_realtime_bridge_and_beginner_ui()
print("=== ALL 16 STOCK QUANT V4.7 SELF-TESTS PASSED ===")


def test_v48_push_notifications_and_worker():
    man = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert "POST_NOTIFICATIONS" in man
    bg = (ROOT / "android/app/build.gradle").read_text(encoding="utf-8")
    assert "work-runtime:2.8.1" in bg and "versionCode 51" in bg and "versionName '5.1'" in bg
    worker = (ROOT / "android/app/src/main/java/com/mystock/assistant/StockAlertWorker.java").read_text(encoding="utf-8")
    assert "NotificationChannel" in worker and "QuoteFetcher.fetchSingleQuote" in worker
    qf = (ROOT / "android/app/src/main/java/com/mystock/assistant/QuoteFetcher.java").read_text(encoding="utf-8")
    assert "m.stock.naver.com" in qf and "query1.finance.yahoo.com" in qf
    java = (ROOT / "android/app/src/main/java/com/mystock/assistant/MainActivity.java").read_text(encoding="utf-8")
    assert "sendNotification" in java and "syncAlertRules" in java and "PeriodicWorkRequest" in java
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    for tok in ("alertModal", "syncAlertsToNative", "evaluateLiveAlerts", "fireDeviceNotification", "testAlertBtn", "알림 설정"):
        assert tok in idx, f"Missing v4.8 alert token: {tok}"
    print("[PASS 17/17] v4.8 안드로이드 푸시 알림·15분 백그라운드 감시 워커·알림 설정/테스트 UI 검증 통과")


test_v48_push_notifications_and_worker()
print("=== ALL 17 STOCK QUANT V4.8 SELF-TESTS PASSED ===")


def test_v50_live_market_briefing():
    mf = (ROOT / "android/app/src/main/java/com/mystock/assistant/MarketBriefingFetcher.java").read_text(encoding="utf-8")
    assert "fetchFullLiveMarket" in mf and "m.stock.naver.com/api/stocks/" in mf and "거래정지 위험" in mf
    java = (ROOT / "android/app/src/main/java/com/mystock/assistant/MainActivity.java").read_text(encoding="utf-8")
    assert "fetchLiveMarketBriefing" in java and "openBrokerageApp" in java and "onLiveMarketBriefingUpdated" in java
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    for tok in ("liveBriefingBox", "renderLiveMarketBriefing", "explainLiveRankStock", "onLiveMarketBriefingUpdated", "openBrokerageApp", "상승 TOP", "거래대금 TOP", "거래정지"):
        assert tok in idx, f"Missing v5.0 token: {tok}"
    print("[PASS 18/18] v5.0 실시간 국내 4대 랭킹·한글 시황 브리핑·거래정지/과열 위험 경고·한투 앱 바로가기 검증 통과")


test_v50_live_market_briefing()
print("=== ALL 18 STOCK QUANT V5.0 SELF-TESTS PASSED ===")


def test_v51_simple_compare_board():
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    for tok in ("compareBoard", "renderCompareBoard", "toggleCompareItem", "🛡️ 안정형", "🔥 급등·고위험", "한눈에 비교", "repeat(3, 1fr)"):
        assert tok in idx, f"Missing v5.1 token: {tok}"
    print("[PASS 19/19] v5.1 3탭 초간단 UI·안정형 vs 고위험(상한가30%) 한눈에 비교 보드·아코디언 상세 검증 통과")


test_v51_simple_compare_board()
print("=== ALL 19 STOCK QUANT V5.1 SELF-TESTS PASSED ===")


def test_v52_kr_us_market_switcher():
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    for tok in ("marketSwitcher", "🇰🇷 한국 주식", "🇺🇸 미국 주식", "activeMarket", "my_stock_active_mkt_v52"):
        assert tok in idx, f"Missing v5.2 token: {tok}"
    print("[PASS 20/20] v5.2 한국 주식·미국 주식 전용 전환 스위치 및 시장별 맞춤 브리핑 검증 통과")


test_v52_kr_us_market_switcher()
print("=== ALL 20 STOCK QUANT V5.2 SELF-TESTS PASSED ===")


def test_v53_dynamic_bluechip_rotation():
    from dynamic_universe import build_active_universe, is_eligible_bluechip_name
    from build_pwa_feed import build_pwa_payload
    assert is_eligible_bluechip_name("HD현대중공업") is True
    assert is_eligible_bluechip_name("삼성전자우") is False
    assert is_eligible_bluechip_name("KODEX 200") is False
    assert is_eligible_bluechip_name("테스트스팩1호") is False
    assert is_eligible_bluechip_name("위험종목", '{"administration":true,"tag":"관리종목"}') is False

    cfg = _json_v3.loads((ROOT / "config/watchlist.json").read_text(encoding="utf-8"))
    fake_dyn = [
        {"symbol": f"9000{i:02d}.KS", "name": f"신규우량{i}", "sector": "코스피 시총상위"}
        for i in range(12)
    ]
    kr, us = build_active_universe(cfg, my_holdings=[{"name": "카카오", "symbol": "035720.KS", "buyPrice": 119742, "qty": 33}], dynamic_fetcher=lambda: fake_dyn)
    kr_syms = {x["symbol"] for x in kr}
    assert "035720.KS" in kr_syms, "Protected holding must never be dropped"
    assert "900000.KS" in kr_syms, "Dynamic new blue chip must be added"
    new_flags = [x for x in kr if x["symbol"] == "900000.KS" and x.get("is_new_bluechip")]
    assert len(new_flags) == 1
    assert len(us) >= 20

    # Verify KR up to 10 + US up to 8 recommendations split
    sample_many = (
        [{"market": "KR", "symbol": f"000{i:03d}.KS", "name": f"국장{i}", "close": 50000, "ret_1d": 1.0, "ret_20d": 5.0, "score": 75, "grade": "매수 관심", "rr_ratio": 2.0, "is_new_bluechip": i == 0} for i in range(12)]
        + [{"market": "US", "symbol": f"US{i}", "name": f"미장{i}", "close": 100, "ret_1d": 1.0, "ret_20d": 5.0, "score": 75, "grade": "매수 관심", "rr_ratio": 2.0} for i in range(10)]
    )
    pl = build_pwa_payload(sample_many, {}, [])
    kr_r = [x for x in pl["recommendations"] if x["market"] == "KR"]
    us_r = [x for x in pl["recommendations"] if x["market"] == "US"]
    assert len(kr_r) == 10 and len(us_r) == 8
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert "🆕 신규우량" in idx and "is_new_bluechip" in idx
    print("[PASS 21/21] v5.3 2단 자동 우량주 편입·퇴출(보유주 보호·신규우량 편입·스팩/우선주/관리종목 제외·한미10+8추천) 검증 통과")


test_v53_dynamic_bluechip_rotation()
print("=== ALL 21 STOCK QUANT V5.3 SELF-TESTS PASSED ===")


def test_v54_discipline_guardrails():
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    for tok in ("accVerifyBar", "risk-guard-banner", "stop-rule-note", "고위험 도박성 구간", "자동매도 주문"):
        assert tok in idx, f"Missing v5.4 token: {tok}"
    print("[PASS 22/22] v5.4 30일 실전 승률 검증 배너·고위험 추격매수 경고·자동 손절 체크 리마인더 검증 통과")

test_v54_discipline_guardrails()
print("=== ALL 22 STOCK QUANT V5.4 SELF-TESTS PASSED ===")


def test_v60_kis_paper_autotrader():
    import kis_paper_trader as kpt
    assert "openapivts.koreainvestment.com:29443" in kpt.PAPER_BASE_URL
    assert kpt.TR_BUY_PAPER == "VTTC0012U" and kpt.TR_SELL_PAPER == "VTTC0011U" and kpt.TR_BALANCE_PAPER == "VTTC8434R"
    assert (ROOT / ".github/workflows/kis-paper-autotrade.yml").exists()

    # Verify stop-loss (-7%) full sell, +10% half TP, +18% full TP, and buy slot allocation
    positions = {
        "005930": {"code": "005930", "name": "삼성전자", "qty": 10, "avg_price": 70000, "cur_price": 64000, "half_sold": False},
        "005380": {"code": "005380", "name": "현대차", "qty": 4, "avg_price": 200000, "cur_price": 222000, "half_sold": False},
        "012450": {"code": "012450", "name": "한화에어로스페이스", "qty": 2, "avg_price": 500000, "cur_price": 600000, "half_sold": False},
    }
    price_map = {"005930": 64000, "005380": 222000, "012450": 600000, "278470": 50000}
    candidates = [{"market": "KR", "symbol": "278470.KS", "name": "에이피알", "status": "buy", "score": 78, "trust_label": "재무 탄탄", "price_num": 50000}]
    orders = kpt.plan_paper_orders(3_000_000, positions, candidates, price_map, kr_regime="강세장")
    sells = {o["code"]: o for o in orders if o["side"] == "SELL"}
    buys = {o["code"]: o for o in orders if o["side"] == "BUY"}
    assert sells["005930"]["qty"] == 10 and "손절" in sells["005930"]["reason"]
    assert sells["005380"]["qty"] == 2 and "절반 익절" in sells["005380"]["reason"]
    assert sells["012450"]["qty"] == 2 and "전량 익절" in sells["012450"]["reason"]
    assert "278470" in buys and buys["278470"]["qty"] > 0

    ledger = {"initial_cash": 10_000_000, "cash": 3_000_000, "positions": positions, "trades": [], "wins": 0, "losses": 0}
    new_ledger, summary = kpt.apply_orders_to_local_ledger(ledger, orders, price_map, "2026-11-24 14:50 KST")
    assert summary["closed_trades"] == 3 and summary["win_rate"] == 66.7
    idx = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert "paperBotCard" in idx and "renderPaperBotCard" in idx and "모의 자동매매 성적표" in idx
    print("[PASS 23/23] v6.0 한국투자증권 모의투자(VTTC0012U/0011U) 자동매매·손절(-7%)/분할익절(+10/+18%)·성적표 UI 검증 통과")

test_v60_kis_paper_autotrader()
print("=== ALL 23 STOCK QUANT V6.0 SELF-TESTS PASSED ===")
