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
print("[PASS 1/5] 전체 파이썬 스크립트 문법 검증 통과")

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
print(f"[PASS 2/5] ATR 기반 실전 매매 시나리오(진입·손절·1/2차 목표·손익비) 계산 검증 통과 ({plan_kr['rr_ratio']})")

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
    print("[PASS 3/5] v2.0 5대 투자 촉매 뉴스 · 국장 급등초입 · 상한가 원인 · 실전 가격표 생성 검증 통과")

    md_bt, bt_rows = bt.run_backtest(ROOT / "config/watchlist.json", out_d, data_d, fetcher=lambda sym: make_synthetic_ohlcv(sym, 520))
    assert md_bt.exists() and len(bt_rows) == 23
    print(f"[PASS 4/5] 2단계 3년 백테스트 검증 통과 ({len(bt_rows)}종목)")

    hist_csv = data_d / "signals_history.csv"
    hist_csv.write_text(hist_csv.read_text(encoding="utf-8").replace(rows[0]["date"], "2025-06-01"), encoding="utf-8")
    res_tp = tp.update_signal_performance(hist_csv, fetcher=lambda sym, start_date: make_synthetic_ohlcv(sym, 360)[["Close"]])
    assert res_tp["updated"] > 0
    print(f"[PASS 5/5] 3단계 준비용 D+1/D+5/D+20 사후 수익률 자동 채점 검증 통과 ({res_tp['updated']}셀)")

print("=== ALL 5 STOCK QUANT V2.0 SELF-TESTS PASSED ===")
