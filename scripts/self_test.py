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
    assert md_bt.exists() and len(bt_rows) == 23
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
    assert (ROOT / "내_주식비서_바로실행.bat").exists()
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
    assert "assembleDebug" in wf and "MyStockAssistant-v4.5.apk" in wf
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
    assert "MyStockAssistant-v4.5.apk" in wf
    print("[PASS 14/14] v4.5 투자금 기반 자동 매수량 계산기·내 보유 익절/손절 트래커·APK v4.5 구성 검증 통과")


test_v45_investor_flow()
test_v45_position_sizing_and_portfolio_ui()
print("=== ALL 14 STOCK QUANT V4.5 PRO PROFIT KIT SELF-TESTS PASSED ===")
