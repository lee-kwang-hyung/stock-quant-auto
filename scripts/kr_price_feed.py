"""한국 주식(KRX) 1원 단위 정확 시세 로더 (시세 조회 전용 · 주문 기능 없음):
- 당일 실시간 현재가: 한국투자증권(KIS) 공식 API (1순위: 실전 도메인 :9443 -> 2순위: 모의 도메인 :29443 자동 판별)
- 일봉 히스토리 및 폴백: 네이버 금융 차트 XML + pykrx
"""
import datetime as dt
import os
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

# 시세 조회 전용 엔드포인트 (국내주식 현재가 시세 FHKST01010100 — 주문/계좌 기능 일절 없음)
KIS_REAL_QUOTE_URL = "https://openapi.koreainvestment.com:9443"
KIS_PAPER_QUOTE_URL = "https://openapivts.koreainvestment.com:29443"
TR_INQUIRE_PRICE = "FHKST01010100"

# 프로세스 실행 중 토큰 재사용 캐시 (KIS 1분당 1회 발급 제한 보호)
_KIS_TOKEN_CACHE = {
    "token": "",
    "base_url": "",
    "mode_label": "",
    "expires_at": 0.0,
    "failed": False,
    "logged_success": False,
}


def _clean_kr_code(symbol: str) -> str:
    return str(symbol).split(".")[0].strip()


def _get_kis_credentials() -> tuple[str, str]:
    """GitHub Secrets에서 시세 조회용 APP KEY / APP SECRET을 읽습니다."""
    app_key = (
        os.environ.get("KIS_REAL_APP_KEY", "").strip()
        or os.environ.get("KIS_PAPER_APP_KEY", "").strip()
    )
    app_secret = (
        os.environ.get("KIS_REAL_APP_SECRET", "").strip()
        or os.environ.get("KIS_PAPER_APP_SECRET", "").strip()
    )
    return app_key, app_secret


def _get_kis_session() -> tuple[str, str]:
    """
    시세 조회용 KIS 접근 토큰과 접속 도메인을 반환합니다.
    - 등록된 키로 실전 시세 도메인(:9443) 인증을 먼저 시도하고,
      실패 시 모의 시세 도메인(:29443)으로 자동 전환합니다.
    """
    if _KIS_TOKEN_CACHE["failed"]:
        return "", ""
    now = time.time()
    if _KIS_TOKEN_CACHE["token"] and now < _KIS_TOKEN_CACHE["expires_at"]:
        return _KIS_TOKEN_CACHE["token"], _KIS_TOKEN_CACHE["base_url"]

    app_key, app_secret = _get_kis_credentials()
    if not app_key or not app_secret:
        return "", ""

    payload = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }
    last_err = ""
    candidates = [
        (KIS_REAL_QUOTE_URL, "실전 도메인(:9443)"),
        (KIS_PAPER_QUOTE_URL, "모의 도메인(:29443)"),
    ]
    for base_url, label in candidates:
        try:
            r = requests.post(f"{base_url}/oauth2/tokenP", json=payload, timeout=10)
            r.raise_for_status()
            token = r.json().get("access_token", "")
            if token:
                _KIS_TOKEN_CACHE["token"] = token
                _KIS_TOKEN_CACHE["base_url"] = base_url
                _KIS_TOKEN_CACHE["mode_label"] = label
                _KIS_TOKEN_CACHE["expires_at"] = now + 3600  # 1시간 캐시
                if not _KIS_TOKEN_CACHE["logged_success"]:
                    print(f"[KIS 공식 시세 연동 성공] {label} · 시세 조회 전용 (주문 기능 없음)")
                    _KIS_TOKEN_CACHE["logged_success"] = True
                return token, base_url
        except Exception as e:
            last_err = f"{label}: {e}"

    print(f"[KIS 시세 토큰 발급 실패 -> 네이버/pykrx 폴백] {last_err}")
    _KIS_TOKEN_CACHE["failed"] = True
    return "", ""


def fetch_kis_current_price(symbol: str) -> dict | None:
    """한국투자증권(KIS) 공식 API로 국내주식 실시간 현재가(시가·고가·저가·현재가·거래량)를 조회합니다."""
    token, base_url = _get_kis_session()
    if not token or not base_url:
        return None

    code = _clean_kr_code(symbol)
    if len(code) != 6 or not code.isdigit():
        return None

    app_key, app_secret = _get_kis_credentials()

    try:
        url = f"{base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": TR_INQUIRE_PRICE,
            "custtype": "P",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        }
        r = requests.get(url, headers=headers, params=params, timeout=8)
        r.raise_for_status()
        out = r.json().get("output") or {}
        close_p = float(out.get("stck_prpr") or 0)
        if close_p <= 0:
            return None
        open_p = float(out.get("stck_oprc") or close_p)
        high_p = float(out.get("stck_hgpr") or close_p)
        low_p = float(out.get("stck_lwpr") or close_p)
        vol = float(out.get("acml_vol") or 0)
        # KIS API 초당 호출 제한(TPS) 보호
        time.sleep(0.06)
        return {
            "Open": open_p,
            "High": high_p,
            "Low": low_p,
            "Close": close_p,
            "Volume": vol,
        }
    except Exception:
        return None


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


def _merge_kis_realtime_bar(df: pd.DataFrame, kis_quote: dict) -> pd.DataFrame:
    """일봉 DataFrame의 최신 거래일(오늘) 행에 한투 공식 실시간 현재가를 반영합니다."""
    if df is None or df.empty or not kis_quote:
        return df
    today_kst = pd.Timestamp(dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date())
    out = df.copy()
    last_idx = out.index[-1]
    if last_idx == today_kst:
        out.loc[last_idx, ["Open", "High", "Low", "Close", "Volume"]] = [
            kis_quote["Open"],
            kis_quote["High"],
            kis_quote["Low"],
            kis_quote["Close"],
            kis_quote["Volume"],
        ]
    elif today_kst > last_idx:
        # 주말/공휴일 장외 시간에는 기존 마지막 거래일 종가 유지, 평일 장중/마감 후에만 당일 봉 반영
        if today_kst.weekday() < 5:
            out.loc[today_kst] = [
                kis_quote["Open"],
                kis_quote["High"],
                kis_quote["Low"],
                kis_quote["Close"],
                kis_quote["Volume"],
            ]
    return out.sort_index()


def fetch_kr_ohlcv_exact(symbol: str, count: int = 180) -> pd.DataFrame:
    """
    1순위: KIS 공식 현재가(당일 실시간, 실전 :9443 우선 / 모의 :29443 자동 폴백) + 네이버 금융 일봉 결합
    2순위(폴백): 네이버 금융 fchart API 단독
    3순위(폴백): pykrx 공식 KRX 일봉
    """
    code = _clean_kr_code(symbol)
    df = pd.DataFrame()

    # 일봉 히스토리 로드 (네이버 금융 차트 XML 우선)
    try:
        url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and "<item " in r.text:
            parsed = parse_naver_fchart_xml(r.text)
            if len(parsed) >= 25:
                df = parsed
    except Exception:
        pass

    # 일봉 히스토리 폴백 (pykrx)
    if df.empty:
        try:
            from pykrx import stock as krx
            end_d = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
            start_d = end_d - dt.timedelta(days=int(count * 1.6))
            raw = krx.get_market_ohlcv_by_date(start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d"), code)
            if raw is not None and not raw.empty:
                raw = raw.rename(columns={"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"})
                df = raw[["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            pass

    if df.empty:
        return df

    # 1순위 실시간 현재가: 한국투자증권(KIS) 공식 API 덮어쓰기 (키 미등록/오류 시 자동 생략 -> 네이버/pykrx 유지)
    kis_quote = fetch_kis_current_price(code)
    if kis_quote:
        df = _merge_kis_realtime_bar(df, kis_quote)

    return df
