"""v2.1 한국 전 종목 급등 초입(+3~15%)·상한가(+22~30%) 스캐너 및 5대 투자 촉매 뉴스 수집기.
- KRX 로그인 오류를 유발하는 pykrx 대신 네이버 금융 모바일 실시간 랭킹 API(KOSPI/KOSDAQ)를 1순위로 사용합니다.
"""
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


def _parse_num(val) -> float:
    if val is None:
        return 0.0
    s = str(val).replace(",", "").replace("%", "").replace("+", "").strip()
    try:
        return float(s)
    except Exception:
        return 0.0


def _fetch_naver_up_stocks(market: str = "KOSPI", page_size: int = 60) -> list[dict]:
    """네이버 금융 모바일 상승률 상위 API에서 종목 리스트를 조회합니다."""
    url = f"https://m.stock.naver.com/api/stocks/up/{market}?page=1&pageSize={page_size}"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("stocks") or []
    except Exception:
        return []


def _is_excluded_name(name: str) -> bool:
    bad_tokens = ("스팩", "SPAC", "ETN", "ETF", "KODEX", "TIGER", "KBSTAR", "ACE ", "SOL ", "ARIRANG", "HANARO", "RISE ", "PLUS ", "리츠", "인버스", "레버리지")
    if any(tok in name for tok in bad_tokens):
        return True
    if name.endswith("우") or name.endswith("우B") or name.endswith("우(전환)"):
        return True
    return False


def scan_kr_full_market_movers(min_trade_val_krw: int = 5_000_000_000, news_fetcher=None, rank_fetcher=None) -> dict:
    """
    코스피·코스닥 전 종목에서
    1) 급등 초입(+3.0% ~ +15.0%, 거래대금 50억 이상) 스몰캡·중형주 돌파 후보
    2) 당일 상한가/초급등(+22.0% ~ +30.5%) 종목 + 급등 원인 뉴스
    를 네이버 금융 실시간 랭킹 API로 자동 스캔합니다.
    """
    _news = news_fetcher or fetch_rss_news
    _rank = rank_fetcher or _fetch_naver_up_stocks
    now_kst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    target_date = now_kst.strftime("%Y%m%d")

    raw_items = []
    seen = set()
    for mkt in ("KOSPI", "KOSDAQ"):
        for s in _rank(mkt, 60):
            code = str(s.get("itemCode") or "").strip()
            name = str(s.get("stockName") or "").strip()
            if not code or len(code) != 6 or code in seen or not name or _is_excluded_name(name):
                continue
            seen.add(code)
            close = _parse_num(s.get("closePrice"))
            chg_pct = _parse_num(s.get("fluctuationsRatio"))
            # 네이버 API accumulatedTradingValue는 백만원 단위인 경우가 많고, 없을 경우 거래량*현재가로 계산
            vol = _parse_num(s.get("accumulatedTradingVolume"))
            val_raw = _parse_num(s.get("accumulatedTradingValue"))
            if val_raw > 0:
                # 백만원 단위(< 10^8)면 1,000,000 곱함
                trade_val_krw = val_raw * 1_000_000 if val_raw < 100_000_000 else val_raw
            else:
                trade_val_krw = close * vol

            if close < 1000 or trade_val_krw < min_trade_val_krw:
                continue
            raw_items.append({
                "ticker": code,
                "name": name,
                "close": close,
                "chg_pct": chg_pct,
                "trade_val_krw": trade_val_krw,
            })

    if not raw_items:
        return {"date": target_date, "breakout": [], "limit_up": []}

    # 1) 급등 초입 (+3.0% ~ +15.0%), 거래대금 상위 12종목
    bo_list = sorted(
        [x for x in raw_items if 3.0 <= x["chg_pct"] <= 15.0],
        key=lambda x: x["trade_val_krw"],
        reverse=True,
    )[:12]

    breakout_picks = []
    for item in bo_list:
        close = item["close"]
        low_p = close * 0.95
        est_atr = close * 0.04
        plan = compute_trade_plan(close, est_atr, low_p, market="KR")
        news = _news(f"{item['name']} 주가 급등 OR 수주 OR 실적", market="KR", limit=1)
        breakout_picks.append({
            "ticker": item["ticker"],
            "name": item["name"],
            "close": int(close),
            "chg_pct": round(item["chg_pct"], 2),
            "trade_val_eok": round(item["trade_val_krw"] / 1e8, 1),
            "news_title": news[0]["title"] if news else "수급·거래대금 급증",
            "news_link": news[0]["link"] if news else "",
            **plan,
        })

    # 2) 상한가 / 초급등 (+22.0% ~ +30.5%), 등락률·거래대금 상위 8종목
    lu_list = sorted(
        [x for x in raw_items if 22.0 <= x["chg_pct"] <= 30.5],
        key=lambda x: (x["chg_pct"], x["trade_val_krw"]),
        reverse=True,
    )[:8]

    limit_up_movers = []
    for item in lu_list:
        news = _news(f"{item['name']} 상한가 OR 급등", market="KR", limit=1)
        limit_up_movers.append({
            "ticker": item["ticker"],
            "name": item["name"],
            "close": int(item["close"]),
            "chg_pct": round(item["chg_pct"], 2),
            "trade_val_eok": round(item["trade_val_krw"] / 1e8, 1),
            "news_title": news[0]["title"] if news else "강한 수급 유입 상한가/초급등",
            "news_link": news[0]["link"] if news else "",
        })

    return {"date": target_date, "breakout": breakout_picks, "limit_up": limit_up_movers}
