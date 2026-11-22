"""2단계: 한국·미국 종목 3년 과거 데이터 백테스트 (수수료·손절·익절 반영 vs 단순 보유 비교)."""
import argparse
import csv
import json
import math
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from indicators import compute_indicators, score_latest_row


def backtest_single_series(df: pd.DataFrame, strategy: dict) -> dict:
    ind = compute_indicators(df, rsi_period=int(strategy.get("rsi_period", 14)))
    if len(ind) < 80:
        return {}

    fee = float(strategy.get("fee_slippage_pct", 0.0025))
    sl = float(strategy.get("stop_loss_pct", -0.05))
    tp = float(strategy.get("take_profit_pct", 0.12))
    min_buy = float(strategy.get("min_score_buy_interest", 70))

    cash = 1.0
    shares = 0.0
    entry_price = 0.0
    trades = []
    equity_curve = []

    for i in range(60, len(ind)):
        sub = ind.iloc[: i + 1]
        price = float(sub["Close"].iloc[-1])
        res = score_latest_row(sub, strategy)

        if shares == 0.0:
            if res["score"] >= min_buy:
                shares = (cash * (1.0 - fee)) / price
                cash = 0.0
                entry_price = price
        else:
            ret_now = (price / entry_price) - 1.0
            should_exit = (ret_now <= sl) or (ret_now >= tp) or (res["score"] < 42)
            if should_exit or i == len(ind) - 1:
                cash = shares * price * (1.0 - fee)
                trades.append((price / entry_price) - 1.0 - 2.0 * fee)
                shares = 0.0
                entry_price = 0.0

        eq = cash + shares * price
        equity_curve.append(eq)

    eq_s = pd.Series(equity_curve)
    total_ret = (float(eq_s.iloc[-1]) - 1.0) * 100.0
    buy_hold_ret = ((float(ind["Close"].iloc[-1]) / float(ind["Close"].iloc[60])) - 1.0) * 100.0

    roll_max = eq_s.cummax()
    dd = (eq_s / roll_max) - 1.0
    mdd = float(dd.min()) * 100.0

    years = max(0.25, len(eq_s) / 252.0)
    cagr = ((float(eq_s.iloc[-1]) ** (1.0 / years)) - 1.0) * 100.0
    win_rate = (sum(1 for t in trades if t > 0) / len(trades) * 100.0) if trades else 0.0

    return {
        "trades": len(trades),
        "win_rate_pct": round(win_rate, 1),
        "strategy_ret_pct": round(total_ret, 2),
        "buy_hold_ret_pct": round(buy_hold_ret, 2),
        "cagr_pct": round(cagr, 2),
        "mdd_pct": round(mdd, 2),
    }


def fetch_3y_yfinance(symbol: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(symbol).history(period="3y", auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


def run_backtest(config_path: pathlib.Path, out_dir: pathlib.Path, data_dir: pathlib.Path, fetcher=None):
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    strategy = cfg.get("strategy", {})
    _fetch = fetcher or fetch_3y_yfinance

    results = []
    for market, key in (("KR", "kr_tickers"), ("US", "us_tickers")):
        for item in cfg.get(key, []):
            df = _fetch(item["symbol"])
            if df is None or df.empty or len(df) < 80:
                continue
            bt = backtest_single_series(df, strategy)
            if not bt:
                continue
            results.append({"market": market, "symbol": item["symbol"], "name": item["name"], **bt})

    results.sort(key=lambda x: x["strategy_ret_pct"], reverse=True)

    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / "backtest_results.csv"
    if results:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)

    lines = [
        "# 한국·미국 주식 3년 정량 전략 백테스트 리포트",
        "",
        f"- 적용 규칙: 점수 {strategy.get('min_score_buy_interest', 70)}점 이상 진입 · 손절 {strategy.get('stop_loss_pct', -0.05)*100:.0f}% · 익절 +{strategy.get('take_profit_pct', 0.12)*100:.0f}% · 수수료/슬리피지 {strategy.get('fee_slippage_pct', 0.0025)*100:.2f}%",
        "",
        "| 시장 | 종목 | 매매횟수 | 승률 | 전략 수익률 | 단순보유(B&H) | 연복리(CAGR) | 최대낙폭(MDD) |",
        "| :---: | :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        lines.append(
            f"| {r['market']} | **{r['name']}** (`{r['symbol']}`) | {r['trades']}회 | {r['win_rate_pct']:.1f}% | "
            f"**{r['strategy_ret_pct']:+.2f}%** | {r['buy_hold_ret_pct']:+.2f}% | {r['cagr_pct']:+.2f}% | {r['mdd_pct']:.2f}% |"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "backtest_summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[백테스트 완료] {len(results)}종목 -> {md_path}")
    return md_path, results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config/watchlist.json"))
    ap.add_argument("--out", default=str(ROOT / "reports"))
    ap.add_argument("--data", default=str(ROOT / "data"))
    a = ap.parse_args()
    run_backtest(pathlib.Path(a.config), pathlib.Path(a.out), pathlib.Path(a.data))


if __name__ == "__main__":
    main()
