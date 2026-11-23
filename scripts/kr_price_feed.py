"""한국 주식(KRX) 1원 단위 정확 시세 로더: 네이버 금융 차트 API 최우선 + pykrx 폴백 (yfinance 한국주 왜곡 원천 차단)."""
import datetime as dt
import xml.etree.ElementTree as ET

import pandas as pd
import requests


def _clean_kr_code(symbol: str) -> str:
    return str(symbol).split(".")[0].strip()


def parse_naver_fchart_xml(xml_text: str) -> pd.DataFrame:
    root = ET.fromstring(xml_text)
    rows = []
    for item in root.findall(".//item"):
        data = (item.attrib.get("data") or "").split("|")
        if len(data) < 6:
            continue
        try:
            d_str = data[0].strip()
            dt_idx = pd.to_datetime(d_str, format="%Y%m%d")
            o, h, l, c, v = float(data[1]), float(data[2]), float(data[3]), float(data[4]), float(data[5])
            if c > 0:
                rows.append((dt_idx, o, h, l, c, v))
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")
    return df.sort_index()


def fetch_kr_ohlcv_exact(symbol: str, count: int = 180) -> pd.DataFrame:
    """네이버 금융 fchart API로 증권사 앱과 1원까지 일치하는 실제 KRX 일봉(당일 장중 현재가 포함)을 가져옵니다."""
    code = _clean_kr_code(symbol)
    # 1순위: 네이버 금융 차트 XML (장중 실시간 현재가 반영 + 분할/배당 왜곡 없음!)
    try:
        url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and "<item " in r.text:
            df = parse_naver_fchart_xml(r.text)
            if len(df) >= 25:
                return df
    except Exception:
        pass

    # 2순위: pykrx 공식 KRX 일봉
    try:
        from pykrx import stock as krx
        end_d = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        start_d = end_d - dt.timedelta(days=int(count * 1.6))
        df = krx.get_market_ohlcv_by_date(start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d"), code)
        if df is not None and not df.empty:
            df = df.rename(columns={"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"})
            return df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    except Exception:
        pass

    return pd.DataFrame()
