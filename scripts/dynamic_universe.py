"""v5.3 자동 우량주 편입·퇴출 엔진: 고정 보호층(내 보유 + 지수 ETF) + 동적 한국 시총/주도주 자동 로테이션."""
import json
import os
import re

import requests

EXCLUDE_NAME_PATTERNS = (
    "스팩", "SPAC", "ETN", "ETF", "KODEX", "TIGER", "ACE ", "RISE ", "PLUS ", "SOL ", "HANARO", "KOSEF", "ARIRANG", "KBSTAR", "리츠", "인버스", "레버리지"
)


def is_eligible_bluechip_name(name: str, raw_meta: str = "") -> bool:
    """우선주·스팩·ETF·관리종목·거래정지·투자경고 종목을 우량주 풀에서 자동 제외합니다."""
    n = (name or "").strip()
    if not n:
        return False
    # 우선주(예: 삼성전자우, 현대차2우B) 제외
    if re.search(r"(\d*우[A-Za-z]?|\(전환\))$", n):
        return False
    upper_n = n.upper()
    for pat in EXCLUDE_NAME_PATTERNS:
        if pat.upper() in upper_n:
            return False
    meta = raw_meta or ""
    for bad in ("관리종목", "거래정지", "투자위험", "투자경고", "SUSPENSION"):
        if bad in meta:
            return False
    return True


def fetch_kr_dynamic_bluechips(top_kospi_mcap: int = 25, top_kosdaq_mcap: int = 6, top_kospi_val: int = 10) -> list:
    """
    네이버 금융 모바일 API에서 실시간 코스피/코스닥 시총 상위 + 대형 거래대금 주도주를 자동 수집합니다.
    네트워크 불가 또는 오류 시 빈 리스트를 반환해 안전 폴백합니다.
    """
    headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36"}
    endpoints = [
        ("KOSPI", "marketValue", top_kospi_mcap, "코스피 시총상위"),
        ("KOSPI", "priceTop", top_kospi_val, "코스피 주도주"),
        ("KOSDAQ", "marketValue", top_kosdaq_mcap, "코스닥 시총상위"),
    ]
    out = []
    seen = set()
    for mkt, ep, limit, sector_lbl in endpoints:
        try:
            url = f"https://m.stock.naver.com/api/stocks/{ep}/{mkt}?page=1&pageSize={limit + 8}"
            r = requests.get(url, timeout=8, headers=headers)
            if r.status_code != 200:
                continue
            stocks = (r.json() or {}).get("stocks") or []
            added = 0
            for st in stocks:
                if added >= limit:
                    break
                code = str(st.get("itemCode") or "").strip()
                name = str(st.get("stockName") or "").strip()
                if not code or len(code) != 6 or code in seen:
                    continue
                raw_meta = json.dumps(st, ensure_ascii=False)
                if st.get("suspension") or st.get("administration") or not is_eligible_bluechip_name(name, raw_meta):
                    continue
                suffix = ".KS" if mkt == "KOSPI" else ".KQ"
                sym = f"{code}{suffix}"
                seen.add(code)
                out.append({
                    "symbol": sym,
                    "name": name,
                    "sector": sector_lbl,
                })
                added += 1
        except Exception:
            continue
    return out


def _load_my_holdings_env() -> list:
    raw = os.environ.get("MY_HOLDINGS_JSON", "").strip()
    if not raw:
        return []
    try:
        arr = json.loads(raw)
        return arr if isinstance(arr, list) else []
    except Exception:
        return []


def build_active_universe(cfg: dict, my_holdings: list | None = None, dynamic_fetcher=None) -> tuple[list, list]:
    """
    2단 자동 로테이션 우량주 풀 구성:
    - 고정 보호층: 내 보유 주식(MY_HOLDINGS_JSON) + config의 protected=True 종목(지수 ETF 등) -> 절대 퇴출 안 함
    - 자동 교체층: 최신 시총/거래대금 상위 우량주 자동 편입(is_new_bluechip 표시), 순위 이탈 종목 자동 퇴출
    - 오프라인/조회 실패 시 기존 config/watchlist.json으로 안전 폴백
    """
    base_kr = cfg.get("kr_tickers", [])
    base_us = cfg.get("us_tickers", [])
    base_kr_syms = {x["symbol"].upper() for x in base_kr if x.get("symbol")}

    holdings = my_holdings if my_holdings is not None else _load_my_holdings_env()
    protected_kr = []
    seen_kr = set()

    # 1) 고정 보호층: 내 보유 주식 중 한국 종목
    for h in holdings:
        if h.get("delisted"):
            continue
        sym = str(h.get("symbol") or "").strip()
        if sym.endswith((".KS", ".KQ")) and sym.upper() not in seen_kr:
            seen_kr.add(sym.upper())
            protected_kr.append({
                "symbol": sym,
                "name": str(h.get("name") or sym),
                "sector": "내 보유",
                "protected": True,
                "is_new_bluechip": False,
            })

    # 2) 고정 보호층: config에서 protected=True로 지정된 종목(지수 ETF 등)
    for item in base_kr:
        if item.get("protected") and item["symbol"].upper() not in seen_kr:
            seen_kr.add(item["symbol"].upper())
            protected_kr.append({**item, "is_new_bluechip": False})

    # 3) 자동 교체층: 실시간 시총/주도주 우량주 자동 편입 (기존 목록에 없던 종목은 is_new_bluechip=True)
    _dyn = dynamic_fetcher or fetch_kr_dynamic_bluechips
    dyn_list = []
    try:
        dyn_list = _dyn() or []
    except Exception:
        dyn_list = []

    if len(dyn_list) >= 10:
        active_kr = list(protected_kr)
        for d in dyn_list:
            sym_u = d["symbol"].upper()
            if sym_u in seen_kr:
                continue
            seen_kr.add(sym_u)
            active_kr.append({
                "symbol": d["symbol"],
                "name": d["name"],
                "sector": d.get("sector", "우량주"),
                "protected": False,
                "is_new_bluechip": sym_u not in base_kr_syms,
            })
    else:
        # 오프라인 또는 API 일시 오류 시 기존 config 전체로 안전 폴백
        active_kr = list(protected_kr)
        for item in base_kr:
            sym_u = item["symbol"].upper()
            if sym_u not in seen_kr:
                seen_kr.add(sym_u)
                active_kr.append({**item, "is_new_bluechip": False})

    # 미국 주식은 확장된 24개 핵심 대형주·ETF 풀 사용
    active_us = [{**item, "is_new_bluechip": False} for item in base_us]
    return active_kr, active_us
