"""v3.5 신뢰 언론 교차 검증 기반 이슈·급등 후보 정밀 선별 엔진."""
import urllib.parse
import xml.etree.ElementTree as ET
import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}

TRUSTED_MEDIA_HINTS = (
    "연합뉴스", "한국경제", "매일경제", "서울경제", "이데일리", "머니투데이",
    "파이낸셜뉴스", "헤럴드경제", "조선비즈", "뉴시스", "뉴스1", "아시아경제",
    "Reuters", "Bloomberg", "CNBC", "WSJ",
)

STRONG_CATALYST_WORDS = (
    "수주", "계약", "실적", "흑자전환", "사상 최대", "FDA", "임상", "승인",
    "자사주", "배당", "인수", "합병", "정부 정책", "국책", "엔비디아", "AI", "HBM", "MRO",
)

DANGER_WORDS = (
    "유상증자", "전환사채", "CB", "관리종목", "거래정지", "적자확대",
    "감사의견", "횡령", "배임", "상장폐지", "불성실공시",
)


def fetch_trusted_issue_news(query: str, limit: int = 8) -> list:
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    items = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        root = ET.fromstring(r.text)
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            source = (item.findtext("source") or "").strip()
            is_trusted = any(m in source or m in title for m in TRUSTED_MEDIA_HINTS)
            items.append({"title": title, "link": link, "source": source, "trusted": is_trusted})
    except Exception:
        pass
    # 공신력 매체 우선 정렬
    items.sort(key=lambda x: 0 if x["trusted"] else 1)
    return items[:limit]


def score_issue_mover(mover: dict, dart_list: list | None = None) -> dict:
    """급등 후보 종목에 대해 거래대금·뉴스 신뢰도·위험 키워드·DART 공시를 교차 채점."""
    title = str(mover.get("news_title", ""))
    trade_eok = float(mover.get("trade_val_eok", 0))
    chg = float(mover.get("chg_pct", 0))
    name = str(mover.get("name", ""))

    score = 50
    tags = []
    risk_flag = False

    if trade_eok >= 300:
        score += 18
        tags.append(f"대형 수급({trade_eok:.0f}억)")
    elif trade_eok >= 100:
        score += 10
        tags.append(f"탄탄한 거래대금({trade_eok:.0f}억)")
    else:
        score -= 8
        tags.append("거래대금 소형 주의")

    if 3.0 <= chg <= 12.0:
        score += 12
        tags.append("초입 구간")
    elif chg > 18.0:
        score -= 12
        risk_flag = True
        tags.append("단기 급등 추격 주의")

    if any(m in title for m in TRUSTED_MEDIA_HINTS):
        score += 8
        tags.append("주요 언론 보도")

    pos_hits = [w for w in STRONG_CATALYST_WORDS if w in title]
    if pos_hits:
        score += 12
        tags.append(f"핵심 호재({pos_hits[0]})")

    bad_hits = [w for w in DANGER_WORDS if w in title]
    if bad_hits:
        score -= 30
        risk_flag = True
        tags.append(f"위험 신호({bad_hits[0]})")

    for d in (dart_list or []):
        if d.get("corp_name") and d["corp_name"] in name:
            if d.get("is_risk"):
                score -= 25
                risk_flag = True
                tags.append(f"DART 위험공시")
            else:
                score += 15
                tags.append(f"DART 공식호재")

    score = max(0, min(100, score))
    if risk_flag or score < 50:
        verdict = "고위험 · 관망"
        guide = "변동성 또는 위험 공시 신호가 있습니다. 초보자는 추격 매수를 피하세요."
    elif score >= 72:
        verdict = "이슈 상승 유력 (소액 분할)"
        guide = "거래대금과 공식 호재가 동반되었습니다. 반드시 소액 분할·손절가를 지키며 접근하세요."
    else:
        verdict = "이슈 관찰"
        guide = "테마 흐름은 좋으나 눌림목(매수 구간)에서 거래량이 유지되는지 확인 후 판단하세요."

    enriched = dict(mover)
    enriched["issue_score"] = score
    enriched["issue_verdict"] = verdict
    enriched["issue_tags"] = " · ".join(tags[:4])
    enriched["risk_flag"] = risk_flag
    enriched["beginner_guide"] = guide
    return enriched
