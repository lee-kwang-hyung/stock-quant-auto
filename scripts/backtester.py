"""2단계: 한국·미국 종목 3년 단일 백테스트 + v6.3 모의 자동매매 동일 코드 1년 포트폴리오 백테스트."""
import argparse
import csv
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from indicators import compute_indicators, score_latest_row
from kr_price_feed import fetch_kr_ohlcv_exact
import kis_paper_trader as kpt

LIVE_SLIPPAGE_RATE = 0.003  # 실전 시장가 체결 슬리피지 편도 0.3% 반영


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


def _infer_kr_regime(day_rows: list[pd.Series]) -> str:
    """유니버스 전체의 20일/60일 정배열 비율로 당일 한국장 국면(상승/혼조/하락)을 판별합니다."""
    if not day_rows:
        return "혼조세"
    bull_cnt = 0
    bear_cnt = 0
    for row in day_rows:
        c = float(row.get("Close", 0))
        ma20 = float(row.get("MA20", c)) if pd.notna(row.get("MA20")) else c
        ma60 = float(row.get("MA60", c)) if pd.notna(row.get("MA60")) else c
        if c > ma20 > ma60:
            bull_cnt += 1
        elif c < ma20 < ma60:
            bear_cnt += 1
    n = len(day_rows)
    if bull_cnt / n >= 0.55:
        return "강세장"
    if bear_cnt / n >= 0.55:
        return "약세장"
    return "혼조세"


def backtest_v63_portfolio(
    kr_items: list[dict],
    kr_dfs: dict[str, pd.DataFrame],
    strategy: dict,
    lookback_days: int = 252,
    slippage_rate: float = LIVE_SLIPPAGE_RATE,
) -> dict:
    """
    v6.3 모의 자동매매(kis_paper_trader.plan_paper_orders)와 100% 동일한 주문 규칙으로
    최근 1년(252영업일) 한국 포트폴리오를 시뮬레이션합니다 (수수료·거래세 + 실전 슬리피지 0.3% 반영).
    """
    ind_map: dict[str, pd.DataFrame] = {}
    meta_map: dict[str, dict] = {}
    sector_map: dict[str, str] = {}
    for item in kr_items:
        sym = str(item.get("symbol", ""))
        code = sym.split(".")[0].strip()
        df = kr_dfs.get(sym)
        if df is None or df.empty or len(df) < 80:
            continue
        ind_map[code] = compute_indicators(df, rsi_period=int(strategy.get("rsi_period", 14)))
        meta_map[code] = item
        if item.get("sector"):
            sector_map[code] = str(item["sector"]).strip()

    if len(ind_map) < 2:
        return {}

    all_dates = sorted(set().union(*(df.index.tolist() for df in ind_map.values())))
    if len(all_dates) <= 65:
        return {}
    sim_dates = all_dates[max(60, len(all_dates) - lookback_days):]

    ledger = {
        "initial_cash": kpt.INITIAL_SIM_CASH,
        "cash": kpt.INITIAL_SIM_CASH,
        "positions": {},
        "trades": [],
        "wins": 0,
        "losses": 0,
    }
    equity_curve = []
    cb_trigger_weeks = set()
    gross_profit = 0.0
    gross_loss = 0.0

    # 벤치마크: 동일 기간 유니버스 균등보유(Equal-Weight B&H)
    bh_rets = []
    for code, df in ind_map.items():
        sub = df.loc[df.index >= sim_dates[0]]
        if len(sub) >= 2 and float(sub["Close"].iloc[0]) > 0:
            bh_rets.append(((float(sub["Close"].iloc[-1]) / float(sub["Close"].iloc[0])) - 1.0) * 100.0)
    benchmark_bh_pct = round(float(np.mean(bh_rets)), 2) if bh_rets else 0.0

    for cur_dt in sim_dates:
        today_str = pd.Timestamp(cur_dt).strftime("%Y-%m-%d")
        price_map = {}
        candidates = []
        day_rows = []

        for code, df in ind_map.items():
            if cur_dt not in df.index:
                continue
            loc = df.index.get_loc(cur_dt)
            if isinstance(loc, slice):
                loc = loc.stop - 1
            if loc < 60:
                continue
            sub = df.iloc[max(0, loc - 65): loc + 1]
            row = sub.iloc[-1]
            prev = sub.iloc[-2]
            close_p = float(row["Close"])
            prev_c = float(prev["Close"])
            if close_p <= 0:
                continue
            day_rows.append(row)
            price_map[code] = close_p
            res = score_latest_row(sub, strategy, market="KR")
            chg_pct = ((close_p / prev_c) - 1.0) * 100.0 if prev_c > 0 else 0.0
            meta = meta_map[code]
            candidates.append({
                "market": "KR",
                "symbol": meta["symbol"],
                "name": meta["name"],
                "sector": meta.get("sector", ""),
                "status": res.get("status", "hold"),
                "score": res.get("score", 50),
                "price_num": close_p,
                "change_pct": round(chg_pct, 2),
                "trust_label": "우량",
            })

        if not price_map:
            continue

        kr_regime = _infer_kr_regime(day_rows)
        est_eq = float(ledger["cash"]) + sum(
            int(p["qty"]) * float(price_map.get(c) or p.get("cur_price") or p.get("avg_price") or 0)
            for c, p in ledger["positions"].items()
        )
        cb_active, _ = kpt._update_weekly_circuit_breaker(ledger, est_eq, today_str)
        if cb_active:
            cb_trigger_weeks.add(ledger.get("weekly_key"))

        orders = kpt.plan_paper_orders(
            ledger["cash"],
            ledger["positions"],
            candidates,
            price_map,
            kr_regime=kr_regime,
            today_date=today_str,
            now_hhmm="10:05",
            sector_map=sector_map,
            allow_new_buys=not cb_active,
        )

        # 실전 슬리피지(매수 +0.3%, 매도 -0.3%) 반영 체결가 적용
        slipped_orders = []
        for od in orders:
            od_copy = dict(od)
            raw_p = float(od_copy["price"])
            if od_copy["side"] == "BUY":
                od_copy["price"] = round(raw_p * (1.0 + slippage_rate), 2)
            else:
                od_copy["price"] = round(raw_p * (1.0 - slippage_rate), 2)
            slipped_orders.append(od_copy)

        now_kst_str = f"{today_str} 10:05 KST"
        ledger, summary = kpt.apply_orders_to_local_ledger(
            ledger, slipped_orders, price_map, now_kst_str
        )
        for tr in ledger["trades"]:
            if tr.get("time") != now_kst_str:
                break
            if tr.get("side") == "SELL":
                pnl = float(tr.get("pnl", 0))
                if pnl > 0:
                    gross_profit += pnl
                elif pnl < 0:
                    gross_loss += abs(pnl)

        equity_curve.append(float(summary["total_equity"]))

    if not equity_curve:
        return {}

    eq_s = pd.Series(equity_curve)
    total_ret_pct = ((float(eq_s.iloc[-1]) / float(kpt.INITIAL_SIM_CASH)) - 1.0) * 100.0
    mdd_pct = float(((eq_s / eq_s.cummax()) - 1.0).min()) * 100.0
    wins = int(ledger.get("wins", 0))
    losses = int(ledger.get("losses", 0))
    closed_trades = wins + losses
    win_rate_pct = (wins / closed_trades * 100.0) if closed_trades > 0 else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    # 실전 전환 합격 기준 체크
    pass_sample = closed_trades >= 30
    pass_win = (win_rate_pct >= 50.0) or (win_rate_pct >= 45.0 and profit_factor >= 1.8)
    pass_pf = profit_factor >= 1.5
    pass_mdd = mdd_pct >= -10.0
    live_ready = pass_sample and pass_win and pass_pf and pass_mdd

    return {
        "period_days": len(equity_curve),
        "start_date": pd.Timestamp(sim_dates[0]).strftime("%Y-%m-%d"),
        "end_date": pd.Timestamp(sim_dates[-1]).strftime("%Y-%m-%d"),
        "initial_cash": kpt.INITIAL_SIM_CASH,
        "final_equity": round(float(eq_s.iloc[-1])),
        "total_return_pct": round(total_ret_pct, 2),
        "benchmark_bh_pct": benchmark_bh_pct,
        "alpha_pct": round(total_ret_pct - benchmark_bh_pct, 2),
        "closed_trades": closed_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate_pct, 1),
        "profit_factor": profit_factor,
        "mdd_pct": round(mdd_pct, 2),
        "slippage_pct": round(slippage_rate * 100.0, 2),
        "circuit_breaker_weeks": len(cb_trigger_weeks),
        "live_ready_pass": live_ready,
    }


def fetch_3y_yfinance(symbol: str) -> pd.DataFrame:
    if str(symbol).endswith((".KS", ".KQ")):
        df_kr = fetch_kr_ohlcv_exact(symbol, count=750)
        if not df_kr.empty:
            return df_kr
    import yfinance as yf
    df = yf.Ticker(symbol).history(period="3y", auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


def run_backtest(config_path: pathlib.Path, out_dir: pathlib.Path, data_dir: pathlib.Path, fetcher=None):
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    strategy = cfg.get("strategy", {})
    _fetch = fetcher or fetch_3y_yfinance

    results = []
    kr_dfs = {}
    for market, key in (("KR", "kr_tickers"), ("US", "us_tickers")):
        for item in cfg.get(key, []):
            df = _fetch(item["symbol"])
            if df is None or df.empty or len(df) < 80:
                continue
            if market == "KR":
                kr_dfs[item["symbol"]] = df
            bt = backtest_single_series(df, strategy)
            if not bt:
                continue
            results.append({"market": market, "symbol": item["symbol"], "name": item["name"], **bt})

    results.sort(key=lambda x: x["strategy_ret_pct"], reverse=True)

    # v6.3 동일 코드 1년 포트폴리오 백테스트 (실전 슬리피지 0.3% 반영)
    v63_summary = backtest_v63_portfolio(cfg.get("kr_tickers", []), kr_dfs, strategy)

    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / "backtest_results.csv"
    if results:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)

    if v63_summary:
        (data_dir / "v63_portfolio_backtest.json").write_text(
            json.dumps(v63_summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    lines = [
        "# 한국·미국 주식 정량 전략 백테스트 리포트",
        "",
    ]

    if v63_summary:
        verdict = "✅ 실전 소액 전환 합격선 통과" if v63_summary["live_ready_pass"] else "⚠️ 합격선 미달 (모의 검증 및 파라미터 점검 필요)"
        lines += [
            "## 1. v6.3 모의 자동매매 동일 코드 1년 포트폴리오 백테스트 (실전 슬리피지 0.3% 반영)",
            "",
            f"- **기간:** {v63_summary['start_date']} ~ {v63_summary['end_date']} ({v63_summary['period_days']}영업일)",
            f"- **최종 판정:** {verdict}",
            "",
            "| 지표 | 결과 | 실전 합격 기준 |",
            "| :--- | ---: | :--- |",
            f"| **전략 수익률 (최종 자산)** | **{v63_summary['total_return_pct']:+.2f}%** ({v63_summary['final_equity']:,}원) | 지수/균등보유 대비 초과수익 |",
            f"| 단순 균등보유(B&H) 대비 알파 | {v63_summary['benchmark_bh_pct']:+.2f}% (알파 **{v63_summary['alpha_pct']:+.2f}%p**) | 0%p 이상 |",
            f"| 청산 거래 수 | {v63_summary['closed_trades']}건 ({v63_summary['wins']}승 {v63_summary['losses']}패) | 30건 이상 |",
            f"| 승률 | {v63_summary['win_rate_pct']:.1f}% | 50% 이상 (손익비 1.8+ 시 45%+) |",
            f"| 손익비 (Profit Factor) | {v63_summary['profit_factor']:.2f} | 1.50 이상 |",
            f"| 최대 낙폭 (MDD) | {v63_summary['mdd_pct']:.2f}% | -10.0% 이내 |",
            f"| 주간 -5% 서킷브레이커 발동 | {v63_summary['circuit_breaker_weeks']}주 | 계좌 급락 방어 횟수 |",
            "",
            "---",
            "",
        ]

    lines += [
        "## 2. 종목별 3년 단일 전략 백테스트",
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
    print(f"[백테스트 완료] {len(results)}종목 + v6.3 포트폴리오 -> {md_path}")
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
