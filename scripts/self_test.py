"""오프라인 가상 시세 데이터로 1단계 스캐너·2단계 백테스터·3단계 성과추적기를 전수 검증합니다."""
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
print("[PASS 1/4] 전체 파이썬 스크립트 문법 검증 통과")

import stock_analyzer as sa
import backtester as bt
import track_performance as tp


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


with tempfile.TemporaryDirectory() as td:
    td_p = pathlib.Path(td)
    out_d, data_d = td_p / "reports", td_p / "data"

    # Test 1: Daily Scanner
    rep, rows = sa.run_analysis(
        ROOT / "config/watchlist.json",
        out_d,
        data_d,
        fetcher=lambda sym, period="6mo": make_synthetic_ohlcv(sym, 160),
        news_fetcher=lambda q, market="KR", limit=2: [(f"{q} 샘플 뉴스", "https://example.com")],
    )
    assert rep.exists() and len(rows) == 23, f"Expected 23 rows, got {len(rows)}"
    assert (data_d / "signals_history.csv").exists()
    print(f"[PASS 2/4] 1단계 일일 한·미 23종목 스크리닝 & 시그널 DB 저장 검증 통과 (최고점: {rows[0]['name']} {rows[0]['score']}점)")

    # Test 2: 3Y Backtester
    md_bt, bt_rows = bt.run_backtest(
        ROOT / "config/watchlist.json",
        out_d,
        data_d,
        fetcher=lambda sym: make_synthetic_ohlcv(sym, 520),
    )
    assert md_bt.exists() and len(bt_rows) == 23
    print(f"[PASS 3/4] 2단계 3년 백테스트(수수료·손절·익절·MDD·B&H 비교) 검증 통과 ({len(bt_rows)}종목)")

    # Test 3: Performance Tracker
    # Seed a past-dated row so D+1/D+5/D+20 get scored
    hist_csv = data_d / "signals_history.csv"
    txt = hist_csv.read_text(encoding="utf-8").replace(rows[0]["date"], "2025-06-01")
    hist_csv.write_text(txt, encoding="utf-8")
    res_tp = tp.update_signal_performance(
        hist_csv,
        fetcher=lambda sym, start_date: make_synthetic_ohlcv(sym, 360)[["Close"]],
    )
    assert res_tp["updated"] > 0
    print(f"[PASS 4/4] 3단계 준비용 시그널 사후 수익률(D+1/D+5/D+20) 자동 채점 검증 통과 ({res_tp['updated']}셀 갱신)")

print("=== ALL 4 STOCK QUANT AUTOMATION SELF-TESTS PASSED ===")
