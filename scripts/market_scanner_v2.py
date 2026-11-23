"""v2.0 한국 전 종목 급등 초입(+3~15%)·상한가(+22~30%) 스캐너 및 5대 투자 촉매 뉴스 수집기."""
import datetime as dt
import urllib.parse
import xml.etree.ElementTree as ET

import requests

from indicators import compute_trade_plan


CATALYST_QUERIES = [
    ("대형 수주 · 공급계약 공시", "단일판매 공급계약 OR 대규모 수주 주가", "KR"),
    ("실적 서프라이즈 · 어닝", "어닝 서프라이즈 OR 영업이익 급증 주가", "KR"),
    ("신약 · 임상 · FDA 바이오", "임상 성공 OR FDA 승인 바이오 주가", "KR"),
    ("정부 정책 · 주도 테마 수혜", "정책 수혜주 OR 테마 급등 코스닥", "KR"),
    ("미국 증시 핵심 급등 촉매", "stock surges catalyst earnings guidance AI biotech", "US"),
]


def fetch_rss_news(query: str, market: str = "KR", limit: int = 3) -> list:
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
            pub = (item.findtext("pubDate") or "").strip()
            if title:
                out.append({"title": title, "link": link, "pub": pub})
        return out
    except Exception:
        return []


def collect_catalyst_issues(news_fetcher=None) -> list:
    """5대 급등 촉매 카테고리별 최신 투자 이슈 수집."""
    _fetch = news_fetcher or fetch_rss_news
    sections = []
    for cat_name, q, mkt in CATALYST_QUERIES:
        items = _fetch(q, market=mkt, limit=3)
        sections.append({"category": cat_name, "market": mkt, "items": items})
    return sections


def scan_kr_full_market_movers(min_trade_val_krw: int = 5_000_000_000, news_fetcher=None) -> dict:
    """
    코스피·코스닥 전 종목에서
    1) 급등 초입(+3.0% ~ +15.0%, 거래대금 50억 이상) 스몰캡·중형주 돌파 후보
    2) 당일 상한가/초급등(+22.0% ~ +30.5%) 종목 + 급등 원인 뉴스
    를 자동 스캔합니다. (pykrx 사용, 실패 시 빈 리스트 안전 폴백)
    """
    _news = news_fetcher or fetch_rss_news
    breakout_picks = []
    limit_up_movers = []
    try:
        from pykrx import stock as krx
        now_kst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        target_date = None
        df_all = None
        # 최근 7일 중 직전 영업일 자동 탐색
        for back in range(0, 8):
            d_str = (now_kst - dt.timedelta(days=back)).strftime("%Y%m%d")
            try:
                tmp = krx.get_market_ohlcv_by_ticker(d_str, market="ALL")
                if tmp is not None and not tmp.empty and float(tmp["거래대금"].sum()) > 0:
                    target_date = d_str
                    df_all = tmp
                    break
            except Exception:
                continue

        if df_all is None or df_all.empty:
            return {"date": "", "breakout": [], "limit_up": []}

        # 거래대금 50억 이상 + 종가 1,000원 이상(동전주·극소형 작전주 제외)
        liquid = df_all[(df_all["거래대금"] >= min_trade_val_krw) & (df_all["종가"] >= 1000)].copy()

        # 1) 급등 초입 (+3.0% ~ +15.0%), 거래대금 상위 12종목
        bo = liquid[(liquid["등락률"] >= 3.0) & (liquid["등락률"] <= 15.0)].sort_values("거래대금", ascending=False).head(12)
        for ticker, row in bo.iterrows():
            name = krx.get_market_ticker_name(ticker) or ticker
            close = float(row["종가"])
            low_p = float(row["저가"]) if float(row["저가"]) > 0 else close * 0.95
            est_atr = max((float(row["고가"]) - low_p), close * 0.035)
            plan = compute_trade_plan(close, est_atr, low_p, market="KR")
            news = _news(f"{name} 주가 급등 OR 수주 OR 실적", market="KR", limit=1)
            breakout_picks.append({
                "ticker": ticker,
                "name": name,
                "close": int(close),
                "chg_pct": round(float(row["등락률"]), 2),
                "trade_val_eok": round(float(row["거래대금"]) / 1e8, 1),
                "news_title": news[0]["title"] if news else "수급·거래대금 급증",
                "news_link": news[0]["link"] if news else "",
                **plan,
            })

        # 2) 당일 상한가/초급등 (+22.0% 이상) 원인 정리 (상위 8종목)
        lu = liquid[liquid["등락률"] >= 22.0].sort_values("등락률", ascending=False).head(8)
        for ticker, row in lu.iterrows():
            name = krx.get_market_ticker_name(ticker) or ticker
            news = _news(f"{name} 상한가 OR 급등 이유", market="KR", limit=1)
            limit_up_movers.append({
                "ticker": ticker,
                "name": name,
                "close": int(row["종가"]),
                "chg_pct": round(float(row["등락률"]), 2),
                "trade_val_eok": round(float(row["거래대금"]) / 1e8, 1),
                "news_title": news[0]["title"] if news else "관련 공시·테마 확인 필요",
                "news_link": news[0]["link"] if news else "",
            })

        return {"date": target_date, "breakout": breakout_picks, "limit_up": limit_up_movers}
    except Exception as e:
        print(f"[전 종목 스캔 참고] {e}")
        return {"date": "", "breakout": [], "limit_up": []}
