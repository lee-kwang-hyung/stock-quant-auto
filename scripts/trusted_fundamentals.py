"""v3.5 공신력 기반 재무·수급·공시(DART) 신뢰도 분석 모듈.
- 한국 주식: 네이버 금융(KRX 공식 요약) PER/PBR/시가총액/영업이익/외국인·기관 수급 + (선택) 금융감독원 DART 공시
- 미국 주식: Yahoo Finance 기업 재무(시가총액·영업이익률·매출성장률·애널리스트 목표가)
"""
import datetime as dt
import os
import re
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
RISK_DISCLOSURE_WORDS = ("유상증자", "전환사채", "신주인수권부사채", "관리종목", "상장폐지", "감사의견", "횡령", "배임", "영업정지")
POSITIVE_DISCLOSURE_WORDS = ("단일판매", "공급계약", "자기주식", "무상증자", "영업(잠정)실적", "신규시설투자")


def parse_kr_fundamental_html(html: str) -> dict:
    """네이버 금융 종목 메인 HTML에서 시가총액·PER·PBR·외국인소진율 등 핵심 재무 수치를 추출."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    def _find_num(pattern: str):
        m = re.search(pattern, text)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", ""))
        except Exception:
            return None

    per = _find_num(r"PER\s*l?\s*EPS[^\d\-]*([\-]?\d+(?:\.\d+)?)배")
    if per is None:
        el = soup.select_one("#_per")
        if el:
            try:
                per = float(el.get_text(strip=True).replace(",", ""))
            except Exception:
                per = None
    pbr = None
    el_pbr = soup.select_one("#_pbr")
    if el_pbr:
        try:
            pbr = float(el_pbr.get_text(strip=True).replace(",", ""))
        except Exception:
            pbr = None

    mcap_text = ""
    el_mc = soup.select_one("#_market_sum")
    if el_mc:
        mcap_text = re.sub(r"\s+", " ", el_mc.get_text(" ", strip=True))

    return {
        "per": per,
        "pbr": pbr,
        "market_cap_text": mcap_text,
    }


def fetch_kr_fundamentals(symbol: str) -> dict:
    code = str(symbol).split(".")[0].zfill(6)
    base = {"per": None, "pbr": None, "market_cap_text": "", "trust_bonus": 0, "trust_label": "보통", "trust_note": "기본 시세 기준"}
    try:
        r = requests.get(f"https://finance.naver.com/item/main.naver?code={code}", headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return base
        parsed = parse_kr_fundamental_html(r.text)
        base.update(parsed)
        return score_fundamentals(base, market="KR")
    except Exception:
        return base


def fetch_us_fundamentals(symbol: str) -> dict:
    base = {"per": None, "pbr": None, "profit_margin": None, "rev_growth": None, "target_mean": None, "trust_bonus": 0, "trust_label": "보통", "trust_note": "기본 시세 기준"}
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
        base["per"] = info.get("forwardPE") or info.get("trailingPE")
        base["pbr"] = info.get("priceToBook")
        base["profit_margin"] = info.get("profitMargins")
        base["rev_growth"] = info.get("revenueGrowth")
        base["target_mean"] = info.get("targetMeanPrice")
        return score_fundamentals(base, market="US")
    except Exception:
        return base


def score_fundamentals(fund: dict, market: str = "KR", disclosures: list | None = None) -> dict:
    """재무 건전성과 공시 리스크를 초보자용 3단계(재무 탄탄 / 보통 / 고위험 주의)와 보정 점수로 환산."""
    bonus = 0
    notes = []
    per = fund.get("per")
    pbr = fund.get("pbr")
    pm = fund.get("profit_margin")
    rg = fund.get("rev_growth")

    if per is not None:
        if 0 < per <= 25:
            bonus += 6
            notes.append(f"흑자·적정 PER({per:.1f}배)")
        elif per > 60:
            bonus -= 4
            notes.append(f"고평가 주의(PER {per:.1f}배)")
        elif per < 0:
            bonus -= 12
            notes.append("적자 기업 주의")
    if pbr is not None and 0 < pbr <= 2.5:
        bonus += 3
        notes.append(f"PBR {pbr:.2f}배")

    if market == "US":
        if pm is not None:
            if pm >= 0.12:
                bonus += 5
                notes.append(f"높은 수익성({pm*100:.0f}%)")
            elif pm < 0:
                bonus -= 10
                notes.append("적자 기업 주의")
        if rg is not None and rg >= 0.10:
            bonus += 4
            notes.append(f"매출 성장(+{rg*100:.0f}%)")

    risk_hit = False
    pos_hit = False
    for d in (disclosures or []):
        title = str(d.get("report_nm", d.get("title", "")))
        if any(w in title for w in RISK_DISCLOSURE_WORDS):
            risk_hit = True
            bonus -= 15
            notes.append(f"위험공시({title[:18]})")
        elif any(w in title for w in POSITIVE_DISCLOSURE_WORDS):
            pos_hit = True
            bonus += 8
            notes.append(f"호재공시({title[:18]})")

    if risk_hit or (per is not None and per < 0) or (pm is not None and pm < 0):
        label = "적자·리스크 주의"
    elif bonus >= 6 or pos_hit:
        label = "재무 탄탄"
    else:
        label = "보통"

    fund["trust_bonus"] = max(-20, min(15, bonus))
    fund["trust_label"] = label
    fund["trust_note"] = " · ".join(notes[:3]) if notes else "기본 재무 확인"
    return fund


def fetch_dart_recent_disclosures(days: int = 3) -> list:
    """금융감독원 DART Open API(무료 키 DART_API_KEY 설정 시)에서 최근 주요 공시 수집."""
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        return []
    end_d = dt.date.today()
    bgn_d = end_d - dt.timedelta(days=days)
    url = "https://opendart.fss.or.kr/api/list.json"
    params = {
        "crtfc_key": api_key,
        "bgn_de": bgn_d.strftime("%Y%m%d"),
        "end_de": end_d.strftime("%Y%m%d"),
        "page_count": 100,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        js = r.json()
        if js.get("status") != "000":
            return []
        out = []
        for it in js.get("list", []):
            title = it.get("report_nm", "")
            if any(w in title for w in POSITIVE_DISCLOSURE_WORDS + RISK_DISCLOSURE_WORDS):
                out.append(
                    {
                        "corp_name": it.get("corp_name", ""),
                        "stock_code": it.get("stock_code", ""),
                        "report_nm": title,
                        "rcept_dt": it.get("rcept_dt", ""),
                        "link": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it.get('rcept_no', '')}",
                        "is_risk": any(w in title for w in RISK_DISCLOSURE_WORDS),
                    }
                )
        return out
    except Exception:
        return []


def parse_kr_investor_flow_html(html: str, days: int = 5) -> dict:
    """네이버 금융 투자자별 매매동향(frgn.naver)에서 최근 5거래일 기관·외국인 순매수 합계 추출."""
    soup = BeautifulSoup(html, "html.parser")
    inst_sum = 0
    frgn_sum = 0
    counted = 0
    for tr in soup.select("table.type2 tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) >= 7 and re.match(r"^\d{4}\.\d{2}\.\d{2}$", tds[0]):
            try:
                inst_v = int(tds[5].replace(",", "").replace("+", ""))
                frgn_v = int(tds[6].replace(",", "").replace("+", ""))
                inst_sum += inst_v
                frgn_sum += frgn_v
                counted += 1
                if counted >= days:
                    break
            except Exception:
                continue
    return {"days": counted, "inst_5d": inst_sum, "frgn_5d": frgn_sum}


def score_investor_flow(flow: dict) -> dict:
    days = flow.get("days", 0)
    inst = int(flow.get("inst_5d", 0))
    frgn = int(flow.get("frgn_5d", 0))
    if days == 0:
        return {"flow_bonus": 0, "flow_label": "수급 데이터 없음"}
    if inst > 0 and frgn > 0:
        return {"flow_bonus": 10, "flow_label": "외국인·기관 쌍끌이 매수"}
    if inst < 0 and frgn < 0:
        return {"flow_bonus": -8, "flow_label": "외국인·기관 동반 매도 주의"}
    if frgn > 0:
        return {"flow_bonus": 4, "flow_label": "외국인 5일 순매수"}
    if inst > 0:
        return {"flow_bonus": 4, "flow_label": "기관 5일 순매수"}
    return {"flow_bonus": 0, "flow_label": "수급 중립"}


def fetch_kr_investor_flow(symbol: str, days: int = 5) -> dict:
    code = str(symbol).split(".")[0].zfill(6)
    try:
        r = requests.get(f"https://finance.naver.com/item/frgn.naver?code={code}", headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return {"flow_bonus": 0, "flow_label": "수급 조회 대기"}
        parsed = parse_kr_investor_flow_html(r.text, days=days)
        res = score_investor_flow(parsed)
        res.update(parsed)
        return res
    except Exception:
        return {"flow_bonus": 0, "flow_label": "수급 조회 대기"}
