"""3단계 준비: data/signals_history.csv에 누적된 과거 시그널의 D+1, D+5, D+20 실제 수익률을 자동 채점합니다."""
import csv
import datetime as dt
import pathlib

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _default_price_fetcher(symbol: str, start_date: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(symbol).history(start=start_date, auto_adjust=True)
    return df[["Close"]].dropna() if df is not None and not df.empty else pd.DataFrame()


def update_signal_performance(csv_path: pathlib.Path, fetcher=None) -> dict:
    if not csv_path.exists():
        return {"updated": 0}
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {"updated": 0}

    _fetch = fetcher or _default_price_fetcher
    cache = {}
    updated = 0

    for r in rows:
        if r.get("d1_ret_pct") and r.get("d5_ret_pct") and r.get("d20_ret_pct"):
            continue
        sym = r["symbol"]
        sig_date = r["date"]
        entry_close = float(r.get("close") or 0)
        if entry_close <= 0:
            continue
        if sym not in cache:
            cache[sym] = _fetch(sym, sig_date)
        df = cache[sym]
        if df is None or df.empty:
            continue
        idx_dates = [str(x)[:10] for x in df.index]
        after = [(d, float(c)) for d, c in zip(idx_dates, df["Close"]) if d > sig_date]
        for offset, col in ((1, "d1_ret_pct"), (5, "d5_ret_pct"), (20, "d20_ret_pct")):
            if not r.get(col) and len(after) >= offset:
                fut_price = after[offset - 1][1]
                r[col] = f"{((fut_price / entry_close) - 1.0) * 100.0:.2f}"
                updated += 1

    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"[성과 추적 완료] 갱신된 D+N 수익률 셀: {updated}건")
    return {"updated": updated}


if __name__ == "__main__":
    update_signal_performance(ROOT / "data/signals_history.csv")
