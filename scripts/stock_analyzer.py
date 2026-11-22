"""1단계: 한국·미국 주식 일일 정량 스크리닝, 뉴스 수집, 시그널 누적 DB 기록 및 리포트 생성."""
import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.parse
import xml.etree.ElementTree as ET

import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from indicators import compute_indicators, score_latest_row


def fetch_ohlcv_yfinance(symbol: str, period: str = "6mo") -> pd.DataFrame:
    import yfinance as yf
    t = yf.Ticker(symbol)
    df = t.history(period=period, auto_adjust=True)
    if df is None or df.empty or len(df) < 30:
        return pd.DataFrame()
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


def fetch_google_news_titles(query: str, market: str = "KR", limit: int = 2) -> list:
    try:
        hl, gl, ceid = ("ko", "KR", "KR:ko") if market == "KR" else ("en-US", "US", "US:en")
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        out = []
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title:
                out.append((title, link))
        return out
    except Exception:
        return []


def append_signals_history(rows: list, csv_path: pathlib.Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date", "market", "symbol", "name", "sector",
        "close", "ret_1d", "ret_20d", "rsi", "vol_ratio",
        "score", "grade", "reasons",
        "d1_ret_pct", "d5_ret_pct", "d20_ret_pct"
    ]
    existing = set()
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0
    if file_exists:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                existing.add((r.get("date"), r.get("symbol")))

    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            w.writeheader()
        for r in rows:
            key = (r["date"], r["symbol"])
            if key in existing:
                continue
            w.writerow({k: r.get(k, "") for k in fieldnames})


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


def run_analysis(config_path: pathlib.Path, out_dir: pathlib.Path, data_dir: pathlib.Path, fetcher=None, news_fetcher=None):
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    strategy = cfg.get("strategy", {})
    kst_now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    today_str = kst_now.strftime("%Y-%m-%d")

    _fetch = fetcher or fetch_ohlcv_yfinance
    _news = news_fetcher or fetch_google_news_titles

    all_rows = []
    for market, key in (("KR", "kr_tickers"), ("US", "us_tickers")):
        for item in cfg.get(key, []):
            sym = item["symbol"]
            df = _fetch(sym, period="6mo")
            if df is None or df.empty or len(df) < 25:
                continue
            ind = compute_indicators(df, rsi_period=int(strategy.get("rsi_period", 14)))
            res = score_latest_row(ind, strategy)
            q_news = f"{item['name']} 주가" if market == "KR" else f"{item['symbol']} stock"
            news_list = _news(q_news, market=market, limit=2)
            row = {
                "date": today_str,
                "market": market,
                "symbol": sym,
                "name": item["name"],
                "sector": item.get("sector", ""),
                "news": news_list,
                **res,
            }
            all_rows.append(row)

    all_rows.sort(key=lambda x: x["score"], reverse=True)
    append_signals_history(all_rows, data_dir / "signals_history.csv")

    lines = [
        f"# 일일 한국·미국 주식 정량 스캔 리포트 ({today_str} KST)",
        "",
        "> 본 리포트는 기술적 지표·수급 데이터 기반 자동화 분석 자료이며, 투자 판단의 최종 책임은 본인에게 있습니다.",
        "",
    ]

    for market_code, title_kr in (("KR", "한국 주식 (코스피/코스닥)"), ("US", "미국 주식 (나스닥/S&P500)")):
        sub = [r for r in all_rows if r["market"] == market_code]
        lines.append(f"## {title_kr}")
        lines.append("")
        lines.append("| 순위 | 종목 | 섹터 | 종가 | 일간 | 20일 | RSI | 거래량배수 | 점수 | 등급 | 핵심 근거 |")
        lines.append("| :---: | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :--- |")
        for idx, r in enumerate(sub, 1):
            price_s = f"{r['close']:,.0f}원" if market_code == "KR" else f"${r['close']:,.2f}"
            lines.append(
                f"| {idx} | **{r['name']}** (`{r['symbol']}`) | {r['sector']} | {price_s} | "
                f"{r['ret_1d']:+.2f}% | {r['ret_20d']:+.2f}% | {r['rsi']:.1f} | {r['vol_ratio']:.1f}x | "
                f"**{r['score']:.1f}** | {r['grade']} | {r['reasons']} |"
            )
        lines.append("")
        top_news_items = [r for r in sub[:3] if r.get("news")]
        if top_news_items:
            lines.append("### 상위 종목 최근 뉴스")
            for r in top_news_items:
                for nt, nl in r["news"]:
                    if nl:
                        lines.append(f"- **{r['name']}**: [{nt}]({nl})")
                    else:
                        lines.append(f"- **{r['name']}**: {nt}")
            lines.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"daily_report_{today_str}.md"
    latest_path = out_dir / "latest_daily_report.md"
    report_md = "\n".join(lines) + "\n"
    report_path.write_text(report_md, encoding="utf-8")
    latest_path.write_text(report_md, encoding="utf-8")

    # GitHub Actions Summary 출력
    gh_sum = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if gh_sum:
        with open(gh_sum, "a", encoding="utf-8") as sf:
            sf.write(report_md)

    # 텔레그램 요약 전송 (설정된 경우)
    top_picks = [r for r in all_rows if r["grade"] == "매수 관심"][:5]
    tg_lines = [f"[{today_str} 한·미 주식 정량 스캔]"]
    if top_picks:
        for r in top_picks:
            tg_lines.append(f"- [{r['market']}] {r['name']}({r['score']}점): {r['reasons']}")
    else:
        tg_lines.append("- 오늘 70점 이상 '매수 관심' 종목 없음 (관찰 구간)")
    send_telegram_if_configured("\n".join(tg_lines))
    print(f"[스캔 완료] 총 {len(all_rows)}종목 분석 -> {report_path}")
    return report_path, all_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config/watchlist.json"))
    ap.add_argument("--out", default=str(ROOT / "reports"))
    ap.add_argument("--data", default=str(ROOT / "data"))
    a = ap.parse_args()
    run_analysis(pathlib.Path(a.config), pathlib.Path(a.out), pathlib.Path(a.data))


if __name__ == "__main__":
    main()
