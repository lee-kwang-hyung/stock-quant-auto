"""v3.0 나만의 모바일 주식 앱(PWA)용 초보자 친화 데이터 생성 및 AES-256-GCM 암호화 빌더."""
import base64
import datetime as dt
import json
import os
import pathlib

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KDF_ITERATIONS = 100_000


def _beginner_status(grade: str, score: float) -> tuple[str, str, str]:
    if grade == "매수 관심" or score >= 65:
        return (
            "buy",
            "분할매수 관심",
            "흐름이 양호합니다. 한 번에 사지 말고 매수 구간에서 2~3번 나눠 접근해 보세요.",
        )
    if grade == "관찰" or score >= 45:
        return (
            "watch",
            "관망 (대기)",
            "아직 확실한 돌파 전입니다. 매수 구간까지 눌리거나 거래량이 붙을 때 살펴보세요.",
        )
    return (
        "caution",
        "주의 (추격 금지)",
        "하락 추세거나 변동성이 큽니다. 바닥을 확인하기 전에는 무리하게 진입하지 마세요.",
    )


def _format_price(val: float, market: str) -> str:
    if market == "KR":
        return f"{int(round(float(val))):,}원"
    return f"${float(val):,.2f}"


def _build_beginner_item(r: dict) -> dict:
    market = r.get("market", "KR")
    score = float(r.get("score", 0))
    grade = r.get("grade", "관찰")
    status, badge, guide = _beginner_status(grade, score)
    return {
        "market": market,
        "symbol": r.get("symbol", ""),
        "name": r.get("name", ""),
        "sector": r.get("sector", ""),
        "price_num": float(r.get("close", 0)),
        "price_text": _format_price(r.get("close", 0), market),
        "ret_1d": round(float(r.get("ret_1d", 0)), 2),
        "ret_20d": round(float(r.get("ret_20d", 0)), 2),
        "score": int(round(score)),
        "grade": grade,
        "status": status,
        "badge": badge,
        "buy_zone": r.get("entry_zone", "-"),
        "target_1": r.get("target_1", "-"),
        "target_2": r.get("target_2", "-"),
        "stop_loss": r.get("stop_loss", "-"),
        "reasons": r.get("reasons", ""),
        "beginner_guide": guide,
        "trust_label": r.get("trust_label", "보통"),
        "trust_note": r.get("trust_note", "기본 시세·추세 분석"),
        "rr_ratio": r.get("rr_ratio"),
        "support_text": r.get("support_text", ""),
        "resist_text": r.get("resist_text", ""),
        "flow_label": r.get("flow_label", ""),
        "stop_num": r.get("stop_num"),
        "target1_num": r.get("target1_num"),
        "is_new_bluechip": bool(r.get("is_new_bluechip", False)),
    }


def build_pwa_payload(all_rows: list, kr_movers: dict, catalysts: list, updated_kst: str | None = None, market_regime: dict | None = None, accuracy: dict | None = None) -> dict:
    if not updated_kst:
        updated_kst = (dt.datetime.utcnow() + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")

    watchlist = [_build_beginner_item(r) for r in all_rows]
    # 초보자 추천 TOP: 점수 순 정렬 (매수 관심 우선, 단 주의 등급은 추천에서 제외)
    eligible = [x for x in watchlist if x["status"] in ("buy", "watch") and "주의" not in x.get("trust_label", "") and (x.get("rr_ratio") is None or float(x.get("rr_ratio", 0)) >= 1.5)]
    eligible.sort(key=lambda x: (0 if x["status"] == "buy" else 1, -x["score"]))
    kr_recs = [x for x in eligible if x["market"] == "KR"][:10]
    us_recs = [x for x in eligible if x["market"] == "US"][:8]
    recommendations = kr_recs + us_recs

    breakout = []
    for m in (kr_movers or {}).get("breakout", [])[:15]:
        breakout.append(
            {
                "ticker": m.get("ticker", ""),
                "name": m.get("name", ""),
                "price_text": f"{int(round(float(m.get('close', 0)))):,}원",
                "chg_pct": round(float(m.get("chg_pct", 0)), 2),
                "trade_val_eok": int(round(float(m.get("trade_val_eok", 0)))),
                "buy_zone": m.get("entry_zone", "-"),
                "stop_loss": m.get("stop_loss", "-"),
                "target_1": m.get("target_1", "-"),
                "target_2": m.get("target_2", "-"),
                "rr_ratio": m.get("rr_ratio", "-"),
                "news_title": m.get("news_title", ""),
                "news_link": m.get("news_link", ""),
                "issue_score": m.get("issue_score", 60),
                "issue_verdict": m.get("issue_verdict", "이슈 관찰"),
                "issue_tags": m.get("issue_tags", ""),
                "risk_flag": bool(m.get("risk_flag", False)),
                "beginner_guide": m.get("beginner_guide", "거래대금이 몰린 급등 초입입니다. 변동성이 크니 소액·손절 준수가 필수입니다."),
            }
        )

    limit_up = []
    for m in (kr_movers or {}).get("limit_up", [])[:15]:
        limit_up.append(
            {
                "ticker": m.get("ticker", ""),
                "name": m.get("name", ""),
                "price_text": f"{int(round(float(m.get('close', 0)))):,}원",
                "chg_pct": round(float(m.get("chg_pct", 0)), 2),
                "trade_val_eok": int(round(float(m.get("trade_val_eok", 0)))),
                "news_title": m.get("news_title", ""),
                "news_link": m.get("news_link", ""),
            }
        )

    buy_cnt = sum(1 for x in watchlist if x["status"] == "buy")
    watch_cnt = sum(1 for x in watchlist if x["status"] == "watch")
    caution_cnt = sum(1 for x in watchlist if x["status"] == "caution")

    return {
        "schema_version": "4.6",
        "my_holdings": [],
        "market_regime": market_regime or {},
        "accuracy": accuracy or {},
        "updated_kst": updated_kst,
        "summary": {
            "total": len(watchlist),
            "buy_count": buy_cnt,
            "watch_count": watch_cnt,
            "caution_count": caution_cnt,
            "breakout_count": len(breakout),
            "limit_up_count": len(limit_up),
        },
        "disclaimer": "본 화면은 정량 지표 기반 투자 참고 정보이며, 실제 매수·매도 판단과 책임은 투자자 본인에게 있습니다.",
        "recommendations": recommendations,
        "watchlist": watchlist,
        "breakout": breakout,
        "limit_up": limit_up,
        "catalysts": catalysts or [],
    }


def _derive_key(pin: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(pin.encode("utf-8"))


def encrypt_payload(payload: dict, pin: str) -> dict:
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = _derive_key(pin, salt, KDF_ITERATIONS)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ct = AESGCM(key).encrypt(iv, raw, None)
    return {
        "encrypted": True,
        "alg": "AES-256-GCM",
        "kdf": "PBKDF2-SHA256",
        "iterations": KDF_ITERATIONS,
        "updated_kst": payload.get("updated_kst", ""),
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "ciphertext": base64.b64encode(ct).decode("ascii"),
    }


def decrypt_envelope(envelope: dict, pin: str) -> dict:
    if not envelope.get("encrypted"):
        return envelope.get("payload", envelope)
    salt = base64.b64decode(envelope["salt"])
    iv = base64.b64decode(envelope["iv"])
    ct = base64.b64decode(envelope["ciphertext"])
    iters = int(envelope.get("iterations", KDF_ITERATIONS))
    key = _derive_key(pin, salt, iters)
    raw = AESGCM(key).decrypt(iv, ct, None)
    return json.loads(raw.decode("utf-8"))


def build_and_save_pwa_feed(
    all_rows: list,
    kr_movers: dict,
    catalysts: list,
    out_path: pathlib.Path,
    pin: str | None = None,
    updated_kst: str | None = None,
    market_regime: dict | None = None,
    accuracy: dict | None = None,
) -> pathlib.Path:
    payload = build_pwa_payload(all_rows, kr_movers, catalysts, updated_kst=updated_kst, market_regime=market_regime, accuracy=accuracy)
    mh_raw = os.environ.get("MY_HOLDINGS_JSON", "").strip()
    if mh_raw:
        try:
            mh = json.loads(mh_raw)
            if isinstance(mh, list):
                payload["my_holdings"] = mh
        except Exception:
            pass
    active_pin = (pin if pin is not None else os.environ.get("APP_PIN", "")).strip()
    if active_pin:
        envelope = encrypt_payload(payload, active_pin)
    else:
        envelope = {
            "encrypted": False,
            "updated_kst": payload.get("updated_kst", ""),
            "payload": payload,
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
