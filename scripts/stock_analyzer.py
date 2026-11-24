"""v2.0 통합 일일 주식 분석기: 투자 촉매 뉴스 + 국장 전종목 급등 초입 시나리오 + 상한가 원인 + 한·미 핵심 종목 가격 플랜."""
import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import sys

import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from indicators import compute_indicators, score_latest_row
from kr_price_feed import fetch_kr_ohlcv_exact
from market_scanner_v2 import collect_catalyst_issues, fetch_rss_news, scan_kr_full_market_movers
from build_pwa_feed import build_and_save_pwa_feed
from trusted_fundamentals import fetch_kr_fundamentals, fetch_us_fundamentals, fetch_dart_recent_disclosures, fetch_kr_investor_flow
from issue_catalyst_engine import score_issue_mover
from precision_engine import detect_market_regime, compute_sr_precision_plan, compute_historical_accuracy
from dynamic_universe import build_active_universe


def fetch_ohlcv_yfinance(symbol: str, period: str = "6mo") -> pd.DataFrame:
    # [v2.1 정확 시세 보정]: 한국 주식(.KS/.KQ)은 네이버 금융/KRX 실제 시세 최우선! (yfinance 왜곡 원천 차단)
    if str(symbol).endswith((".KS", ".KQ")):
        df_kr = fetch_kr_ohlcv_exact(symbol, count=180)
        if not df_kr.empty:
            return df_kr
    import yfinance as yf
    df = yf.Ticker(symbol).history(period=period, auto_adjust=False)
    if df is None or df.empty or len(df) < 30:
        return pd.DataFrame()
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


def append_signals_history(rows: list, csv_path: pathlib.Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date", "market", "symbol", "name", "sector",
        "close", "ret_1d", "ret_20d", "rsi", "vol_ratio",
        "score", "grade", "reasons", "entry_zone", "stop_loss", "target_1", "target_2",
        "d1_ret_pct", "d5_ret_pct", "d20_ret_pct",
    ]
    # [v2.1 CSV 스키마 자동 마이그레이션]: 구버전(v1.0) CSV 컬럼 수와 달라도 안전하게 병합 재저장!
    merged = []
    existing = set()
    if csv_path.exists() and csv_path.stat().st_size > 0:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                clean_r = {k: r.get(k, "") for k in fieldnames}
                merged.append(clean_r)
                existing.add((clean_r["date"], clean_r["symbol"]))
    for r in rows:
        key = (r["date"], r["symbol"])
        if key not in existing:
            merged.append({k: r.get(k, "") for k in fieldnames})
            existing.add(key)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(merged)


def send_telegram_if_configured(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"[Telegram 알림 경고] {e}")


def run_analysis(config_path: pathlib.Path, out_dir: pathlib.Path, data_dir: pathlib.Path, fetcher=None, news_fetcher=None, full_scanner=None, intraday: bool = False):
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    strategy = cfg.get("strategy", {})
    kst_now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    today_str = kst_now.strftime("%Y-%m-%d")

    _fetch = fetcher or fetch_ohlcv_yfinance
    _news = news_fetcher or fetch_rss_news
    _full_scan = full_scanner or (lambda: scan_kr_full_market_movers(news_fetcher=_news))

    # 1) 5대 급등 촉매 투자 이슈 수집
    catalysts = collect_catalyst_issues(news_fetcher=_news)

    # 2) 국장 전 종목 급등 초입(+3~15%) & 상한가(+22~30%) 스캔
    kr_movers = _full_scan()

    # 3) 한·미 동적 우량주 풀(고정 보호층 + 시총/주도주 자동 편입·퇴출) 정량 스코어
    if fetcher is None:
        active_kr, active_us = build_active_universe(cfg)
    else:
        active_kr, active_us = cfg.get("kr_tickers", []), cfg.get("us_tickers", [])

    all_rows = []
    for market, items in (("KR", active_kr), ("US", active_us)):
        for item in items:
            sym = item["symbol"]
            df = _fetch(sym, period="6mo")
            if df is None or df.empty or len(df) < 25:
                continue
            ind = compute_indicators(df, rsi_period=int(strategy.get("rsi_period", 14)))
            res = score_latest_row(ind, strategy, market=market)
            q_news = f"{item['name']} 주가" if market == "KR" else f"{item['symbol']} stock"
            news_list = _news(q_news, market=market, limit=1)
            all_rows.append({
                "date": today_str,
                "market": market,
                "symbol": sym,
                "name": item["name"],
                "sector": item.get("sector", ""),
                "is_new_bluechip": bool(item.get("is_new_bluechip", False)),
                "news": news_list,
                **res,
            })

    all_rows.sort(key=lambda x: x["score"], reverse=True)
    append_signals_history(all_rows, data_dir / "signals_history.csv")

    lines = [
        f"# v2.0 한국·미국 실전 매매 시나리오 & 급등 촉매 브리핑 ({today_str} KST)",
        "",
        "> 본 리포트는 변동성(ATR)·수급·공시 뉴스 기반 정량 참고 시나리오입니다. 분할 매수 및 손절 기준 준수 등 최종 투자 판단은 본인 책임 하에 진행하세요.",
        "",
        "## 1. 오늘의 5대 투자 이슈 · 급등 촉매 뉴스 (공시 · 수주 · 실적 · 바이오 · 미장)",
        "",
    ]
    for sec in catalysts:
        lines.append(f"### {sec['category']}")
        if sec["items"]:
            for it in sec["items"]:
                if it.get("link"):
                    lines.append(f"- [{it['title']}]({it['link']})")
                else:
                    lines.append(f"- {it['title']}")
        else:
            lines.append("- 수집된 신규 기사 없음")
        lines.append("")

    # 섹션 2: 코스피·코스닥 전 종목 급등 초입(+3~15%, 거래대금 50억+)
    lines.append("## 2. 한국 전 종목 급등 초입 포착 (+3~15% · 거래대금 50억+ · 실전 가격 시나리오)")
    lines.append("")
    if kr_movers.get("breakout"):
        lines.append("| 종목 | 종가 | 등락률 | 거래대금 | 진입 참고 구간 | 손절 기준가 | 1차 목표가 | 2차 목표가 | 손익비 | 관련 촉매 뉴스 |")
        lines.append("| :--- | ---: | ---: | ---: | :--- | :--- | :--- | :--- | :---: | :--- |")
        for m in kr_movers["breakout"]:
            nt = f"[{m['news_title'][:38]}]({m['news_link']})" if m.get("news_link") else m["news_title"][:38]
            lines.append(
                f"| **{m['name']}** (`{m['ticker']}`) | {m['close']:,}원 | +{m['chg_pct']:.2f}% | {m['trade_val_eok']:.0f}억 | "
                f"{m['entry_zone']} | {m['stop_loss']} | {m['target_1']} | {m['target_2']} | {m['rr_ratio']} | {nt} |"
            )
    else:
        lines.append("- 휴장일 또는 조건 충족 종목 없음")
    lines.append("")

    # 섹션 3: 당일 상한가/초급등(+22~30%) 원인 일괄 정리
    lines.append("## 3. 당일 상한가 · 초급등(+22~30%) 소형주 원인 정리 (추격 매수 주의 · 테마 파악용)")
    lines.append("")
    if kr_movers.get("limit_up"):
        lines.append("| 종목 | 종가 | 등락률 | 거래대금 | 급등 원인 뉴스 / 공시 |")
        lines.append("| :--- | ---: | ---: | ---: | :--- |")
        for m in kr_movers["limit_up"]:
            nt = f"[{m['news_title']}]({m['news_link']})" if m.get("news_link") else m["news_title"]
            lines.append(f"| **{m['name']}** (`{m['ticker']}`) | {m['close']:,}원 | **+{m['chg_pct']:.2f}%** | {m['trade_val_eok']:.0f}억 | {nt} |")
    else:
        lines.append("- 당일 거래대금 50억 이상 +22% 급등 종목 없음")
    lines.append("")

    # 섹션 4: 한·미 핵심 관심 종목 정량 스코어 & 실전 가격 시나리오
    for market_code, title_kr in (("KR", "4. 한국 핵심 관심 종목 실전 가격 시나리오"), ("US", "5. 미국 핵심 관심 종목 실전 가격 시나리오")):
        sub = [r for r in all_rows if r["market"] == market_code]
        lines.append(f"## {title_kr}")
        lines.append("")
        lines.append("| 순위 | 종목 | 점수(등급) | 종가(일간) | 진입 참고 구간 | 손절 기준가 | 1차 목표가 | 2차 목표가 | 핵심 근거 |")
        lines.append("| :---: | :--- | :---: | ---: | :--- | :--- | :--- | :--- | :--- |")
        for idx, r in enumerate(sub, 1):
            p_s = f"{r['close']:,.0f}원" if market_code == "KR" else f"${r['close']:,.2f}"
            lines.append(
                f"| {idx} | **{r['name']}** (`{r['symbol']}`) | **{r['score']:.0f}점** ({r['grade']}) | "
                f"{p_s} ({r['ret_1d']:+.1f}%) | {r['entry_zone']} | {r['stop_loss']} | {r['target_1']} | {r['target_2']} | {r['reasons']} |"
            )
        lines.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    report_md = "\n".join(lines) + "\n"
    report_path = out_dir / f"daily_report_{today_str}.md"
    (out_dir / "latest_daily_report.md").write_text(report_md, encoding="utf-8")
    report_path.write_text(report_md, encoding="utf-8")

    gh_sum = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if gh_sum:
        with open(gh_sum, "a", encoding="utf-8") as sf:
            sf.write(report_md)

    top_picks = [r for r in all_rows if r["grade"] == "매수 관심"][:4]
    tg_lines = [f"[{today_str} v2.0 한·미 주식 브리핑]"]
    for r in top_picks:
        tg_lines.append(f"- [{r['market']}] {r['name']}({r['score']:.0f}점) 진입:{r['entry_zone']} / 손절:{r['stop_loss']} / 목표1:{r['target_1']}")
    if kr_movers.get("breakout"):
        tg_lines.append(f"- 국장 급등초입 포착: {', '.join(x['name'] for x in kr_movers['breakout'][:5])}")
    # [v4.0 시장 국면(KOSPI/S&P500) + 지지/저항선 정밀 매매가 + 손익비 검증 + 사후 승률]
    market_regime = {}
    if fetcher is None:
        try:
            kr_idx = fetch_ohlcv_yfinance("^KS11", period="6mo")
            us_idx = fetch_ohlcv_yfinance("^GSPC", period="6mo")
            market_regime["KR"] = detect_market_regime(kr_idx, "국장(KOSPI)")
            market_regime["US"] = detect_market_regime(us_idx, "미장(S&P500)")
        except Exception:
            pass
    else:
        market_regime["KR"] = detect_market_regime(fetcher("^KS11"), "국장(KOSPI)")
        market_regime["US"] = detect_market_regime(fetcher("^GSPC"), "미장(S&P500)")

    for r in all_rows:
        reg = market_regime.get(r["market"])
        if reg:
            r["score"] = max(0, min(100, float(r["score"]) + reg.get("score_adj", 0)))
        try:
            df_sr = fetcher(r["symbol"]) if fetcher is not None else fetch_ohlcv_yfinance(r["symbol"], period="6mo")
            if df_sr is not None and not df_sr.empty and len(df_sr) >= 20:
                sr = compute_sr_precision_plan(df_sr, market=r["market"])
                r["entry_zone"] = sr["entry_zone"]
                r["stop_loss"] = sr["stop_loss"]
                r["target_1"] = sr["target_1"]
                r["target_2"] = sr["target_2"]
                r["rr_ratio"] = sr["rr_ratio"]
                r["stop_num"] = sr["stop_num"]
                r["target1_num"] = sr["target1_num"]
                r["support_text"] = sr["support_text"]
                r["resist_text"] = sr["resist_text"]
        except Exception:
            pass

    accuracy_info = compute_historical_accuracy(data_dir / "signals_history.csv")

    # [v3.5 공신력 재무/공시 + 이슈 상승 후보 정밀 채점]
    dart_items = [] if fetcher is not None else fetch_dart_recent_disclosures(days=3)
    for r in all_rows:
        if fetcher is None:
            finfo = fetch_kr_fundamentals(r["symbol"]) if r["market"] == "KR" else fetch_us_fundamentals(r["symbol"])
            r["trust_label"] = finfo.get("trust_label", "보통")
            r["trust_note"] = finfo.get("trust_note", "기본 재무 확인")
            r["score"] = max(0, min(100, float(r["score"]) + finfo.get("trust_bonus", 0)))
            if r["market"] == "KR":
                fl = fetch_kr_investor_flow(r["symbol"], days=5)
                r["flow_label"] = fl.get("flow_label", "")
                r["score"] = max(0, min(100, float(r["score"]) + fl.get("flow_bonus", 0)))
        else:
            r.setdefault("trust_label", "보통")
            r.setdefault("trust_note", "정량 추세 검증")
    if kr_movers and kr_movers.get("breakout"):
        scored_br = [score_issue_mover(m, dart_list=dart_items) for m in kr_movers["breakout"]]
        scored_br.sort(key=lambda x: (-x["issue_score"], -x.get("trade_val_eok", 0)))
        kr_movers["breakout"] = scored_br
    pwa_out = ROOT / "docs/data/live_feed.json"
    build_and_save_pwa_feed(all_rows, kr_movers, catalysts, out_path=pwa_out, market_regime=market_regime, accuracy=accuracy_info)
    if not intraday:
        send_telegram_if_configured("\n".join(tg_lines))
    print(f"[v3.0 스캔 완료] 관심 {len(all_rows)}종목 + 급등초입 {len(kr_movers.get('breakout', []))}종목 -> {report_path} & {pwa_out}")
    return report_path, all_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config/watchlist.json"))
    ap.add_argument("--out", default=str(ROOT / "reports"))
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--intraday", action="store_true")
    a = ap.parse_args()
    run_analysis(pathlib.Path(a.config), pathlib.Path(a.out), pathlib.Path(a.data), intraday=a.intraday)


if __name__ == "__main__":
    main()
