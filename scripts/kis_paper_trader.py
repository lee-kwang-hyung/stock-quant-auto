"""
v6.2 한국투자증권(KIS) 모의투자 전용 자동매매 엔진 (수익 실현형 스윙·로테이션 · 실계좌 주문 원천 차단).
- 모의투자 전용 도메인(https://openapivts.koreainvestment.com:29443) 및 모의 TR_ID(VTTC0012U/VTTC0011U/VTTC8434R)만 사용
- KIS 모의투자 API 키가 아직 없을 때는 로컬 가상 시뮬레이터(1,000만 원 시작 · 수수료 0.015% + 매도세 0.18% 반영)로 즉시 자동 검증
- 수익 실현 + 원금 방어 핵심 구조:
  1) 빠른 수익 확정 사다리: +5.0% 도달 시 절반(50%) 즉시 익절, +10.0% 도달 시 전량 익절
  2) 고점 수익 잠금(트레일링): 최고 수익률 +4.0% 이상 기록 후 고점 대비 -2.5%p 밀리면 즉시 익절 (수익 -> 손실 전환 원천 차단)
     · 1차 익절 후에는 잔여 물량에 +1.5% 본전 이상 방어선 고정
  3) 정체 종목 자동 교체(타임스톱): 10일 이상 보유했는데 수익률이 -2.0% ~ +1.5%에 갇혀 있으면 매도 후 강한 종목으로 교체
  4) 원금 방어 강화: 손절선 -5.0% 즉시 전량 매도 (약세장 -4.5%) + 당일 +8% 이상 급등주 추격매수 금지
  5) 국면별 유동적 비중: 상승장(4종목·25%), 혼조장(4종목·20%), 약세장(2종목·12.5%, 현금 75% 보호)
"""
import datetime as dt
import json
import os
import pathlib
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_pwa_feed import decrypt_envelope

# [영구 안전 잠금] 실전 도메인(:9443) 및 실전 TR_ID(TTTC*) 사용 금지 — 오직 모의투자 전용!
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
TR_BUY_PAPER = "VTTC0012U"      # 2026 신형 모의투자 국내주식 현금 매수
TR_SELL_PAPER = "VTTC0011U"     # 2026 신형 모의투자 국내주식 현금 매도
TR_BALANCE_PAPER = "VTTC8434R"  # 모의투자 국내주식 잔고 조회

MAX_POSITIONS = 4
MAX_WEIGHT_PER_STOCK = 0.25
STOP_LOSS_PCT = -5.0            # 원금 방어 강화 (-7.0% -> -5.0%)
BEAR_STOP_LOSS_PCT = -4.5       # 약세장 추가 방어
TAKE_PROFIT_1_PCT = 5.0         # 1차 절반 익절 (+5.0% 빠른 수익 확정)
TAKE_PROFIT_2_PCT = 10.0        # 2차 전량 익절 (+10.0%)
PEAK_TRAIL_ACTIVATE_PCT = 4.0   # 최고 수익 +4.0% 이상 달성 시 수익 잠금 가동
PEAK_TRAIL_DROP_PCT = 2.5       # 고점 대비 -2.5%p 하락 시 즉시 수익 확정
TRAILING_FLOOR_PCT = 1.5        # 1차 익절 후 잔여 물량 최소 수익 방어선(+1.5%)
STAGNANT_HOLD_DAYS = 10         # 10일 이상 횡보 시 자금 회전(교체 매도)
STAGNANT_LOW_PCT = -2.0
STAGNANT_HIGH_PCT = 1.5
MAX_INTRADAY_CHASE_PCT = 8.0    # 당일 +8% 이상 급등주 추격매수 금지
OPENING_NO_BUY_START = (9, 0)   # 09:00 ~ 09:15 장 초반 호가 공백 구간 신규 매수 회피
OPENING_NO_BUY_END = (9, 15)
MAX_SAME_SECTOR = 2             # 동일 섹터 최대 2종목 분산
WEEKLY_CIRCUIT_BREAKER_PCT = -5.0  # 주간 누적 손실 -5% 도달 시 해당 주 신규 매수 중단


def _is_opening_no_buy_window(now_hhmm: str) -> bool:
    """평일 09:00~09:14 KST 장 초반 호가 공백 구간 여부 판별 (매도는 허용, 신규 매수만 제한)."""
    if not now_hhmm or ":" not in str(now_hhmm):
        return False
    try:
        hh, mm = str(now_hhmm).strip().split(":")[:2]
        cur = (int(hh), int(mm))
        return OPENING_NO_BUY_START <= cur < OPENING_NO_BUY_END
    except Exception:
        return False


def _resolve_sector(code: str, info: dict | None, sector_map: dict | None) -> str:
    sec = ""
    if isinstance(info, dict):
        sec = str(info.get("sector") or "").strip()
    if not sec and sector_map:
        sec = str(sector_map.get(code) or "").strip()
    return sec


def _update_weekly_circuit_breaker(ledger: dict, total_equity: float, today_date: str) -> tuple[bool, float]:
    """ISO 주차 기준 주간 누적 수익률을 추적하고 -5.0% 이하 시 서킷브레이커를 발동합니다."""
    try:
        d = dt.date.fromisoformat(str(today_date)[:10])
    except Exception:
        d = (dt.datetime.utcnow() + dt.timedelta(hours=9)).date()
    iso_year, iso_week, _ = d.isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"

    if ledger.get("weekly_key") != week_key or float(ledger.get("weekly_start_equity") or 0) <= 0:
        ledger["weekly_key"] = week_key
        ledger["weekly_start_equity"] = round(float(total_equity), 2)

    start_eq = float(ledger["weekly_start_equity"])
    weekly_ret = ((float(total_equity) / start_eq) - 1.0) * 100.0 if start_eq > 0 else 0.0
    cb_active = weekly_ret <= WEEKLY_CIRCUIT_BREAKER_PCT
    ledger["weekly_return_pct"] = round(weekly_ret, 2)
    ledger["weekly_circuit_breaker"] = cb_active
    return cb_active, round(weekly_ret, 2)
FEE_RATE = 0.00015              # 매수/매도 위탁수수료 0.015%
SELL_TAX_RATE = 0.0018          # 코스피/코스닥 매도 증권거래세 0.18%
INITIAL_SIM_CASH = 10_000_000


def get_dynamic_regime_rules(kr_regime: str) -> dict:
    """한국 시장 국면에 따라 최대 보유 종목 수, 종목당 비중, 최소 진입 점수, 손절선을 유동적으로 반환."""
    regime_str = kr_regime or "혼조세"
    if "약세" in regime_str or "하락" in regime_str:
        return {
            "label": "방어형 정찰 모드(약세장)",
            "max_positions": 2,
            "max_weight": 0.125,     # 최대 25%만 투자, 현금 75% 보호
            "buy_min_score": 68,     # buy 신호일 때 최소 점수
            "strong_min_score": 72,  # 비-buy 우량주 허용 점수
            "stop_loss_pct": BEAR_STOP_LOSS_PCT,
        }
    if "강세" in regime_str or "상승" in regime_str:
        return {
            "label": "적극 수익 모드(상승장)",
            "max_positions": 4,
            "max_weight": 0.25,      # 최대 100% 활용
            "buy_min_score": 58,
            "strong_min_score": 65,
            "stop_loss_pct": STOP_LOSS_PCT,
        }
    return {
        "label": "균형 스윙 모드(혼조장)",
        "max_positions": 4,
        "max_weight": 0.20,          # 최대 80% 활용, 현금 20% 유지
        "buy_min_score": 60,
        "strong_min_score": 66,
        "stop_loss_pct": STOP_LOSS_PCT,
    }


def _calc_hold_days(entry_date: str, today_date: str) -> int:
    if not entry_date or not today_date:
        return 0
    try:
        d1 = dt.date.fromisoformat(entry_date[:10])
        d2 = dt.date.fromisoformat(today_date[:10])
        return max(0, (d2 - d1).days)
    except Exception:
        return 0


class KisPaperClient:
    """한국투자증권 KIS Developers 모의투자 REST API 클라이언트."""

    def __init__(self, app_key: str, app_secret: str, account_no: str):
        self.app_key = app_key.strip()
        self.app_secret = app_secret.strip()
        clean_acc = account_no.replace("-", "").strip()
        self.cano = clean_acc[:8]
        self.acnt_prdt_cd = clean_acc[8:10] if len(clean_acc) >= 10 else "01"
        self.token = ""

    def authenticate(self) -> str:
        url = f"{PAPER_BASE_URL}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        self.token = r.json()["access_token"]
        return self.token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def get_balance(self) -> tuple[float, dict]:
        """모의계좌 예수금과 보유 종목 딕셔너리({code: {name, qty, avg_price, cur_price}}) 반환."""
        url = f"{PAPER_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        r = requests.get(url, headers=self._headers(TR_BALANCE_PAPER), params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        out2 = (data.get("output2") or [{}])[0]
        cash = float(out2.get("dnca_tot_amt") or out2.get("prvs_rcdl_excc_amt") or 0)
        holdings = {}
        for row in data.get("output1") or []:
            qty = int(float(row.get("hldg_qty") or 0))
            if qty <= 0:
                continue
            code = str(row.get("pdno") or "").strip()
            holdings[code] = {
                "code": code,
                "name": str(row.get("prdt_name") or code),
                "qty": qty,
                "avg_price": float(row.get("pchs_avg_pric") or 0),
                "cur_price": float(row.get("prpr") or 0),
                "half_sold": False,
            }
        return cash, holdings

    def place_market_order(self, side: str, code: str, qty: int) -> dict:
        """모의투자 시장가 현금 매수/매도 주문."""
        assert side in ("BUY", "SELL")
        tr_id = TR_BUY_PAPER if side == "BUY" else TR_SELL_PAPER
        assert tr_id.startswith("VTTC"), "Safety lock: Only paper TR_IDs (VTTC*) allowed!"
        url = f"{PAPER_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": code,
            "ORD_DVSN": "01",  # 시장가
            "ORD_QTY": str(int(qty)),
            "ORD_UNPR": "0",
        }
        r = requests.post(url, headers=self._headers(tr_id), json=body, timeout=10)
        r.raise_for_status()
        return r.json()


def plan_paper_orders(
    cash: float,
    positions: dict,
    candidates: list,
    price_map: dict,
    kr_regime: str = "혼조세",
    today_date: str = "",
    now_hhmm: str = "",
    sector_map: dict | None = None,
    allow_new_buys: bool = True,
) -> list:
    """
    수익 실현형 스윙·로테이션 주문 계획 생성:
    1) 보유 종목 점검: 손절(-5%/-4.5%) -> 2차 익절(+10%) -> 고점 수익 잠금 -> 1차 절반 익절(+5%) -> 정체 종목 교체(10일+)
    2) 여유 슬롯에 시장 국면별 비중으로 고득점 우량주 분할 매수 (급등주 추격 차단)
    """
    orders = []
    est_cash = float(cash)
    remaining_pos = dict(positions)
    rules = get_dynamic_regime_rules(kr_regime)
    stop_loss_line = rules["stop_loss_pct"]

    if not today_date:
        today_date = (dt.datetime.utcnow() + dt.timedelta(hours=9)).strftime("%Y-%m-%d")

    cand_by_code = {}
    for c in candidates:
        if c.get("market") == "KR":
            sym = str(c.get("symbol", "")).split(".")[0].strip()
            if sym:
                cand_by_code[sym] = c

    # 1) 보유 종목 매도 조건 점검
    for code, pos in list(positions.items()):
        qty = int(pos.get("qty", 0))
        avg = float(pos.get("avg_price", 0))
        cur = float(price_map.get(code) or pos.get("cur_price") or 0)
        if qty <= 0 or avg <= 0 or cur <= 0:
            continue

        ret_pct = ((cur / avg) - 1.0) * 100.0
        peak_ret = max(float(pos.get("peak_ret_pct", ret_pct)), ret_pct)
        pos["peak_ret_pct"] = round(peak_ret, 2)
        half_sold = bool(pos.get("half_sold", False))
        hold_days = _calc_hold_days(str(pos.get("entry_date", "")), today_date)

        # (A) 원금 방어 손절
        if ret_pct <= stop_loss_line:
            orders.append({
                "side": "SELL", "code": code, "name": pos.get("name", code),
                "qty": qty, "price": cur, "reason": f"원금 방어 손절 ({ret_pct:.1f}%)", "full_exit": True,
            })
            est_cash += cur * qty * (1.0 - FEE_RATE - SELL_TAX_RATE)
            remaining_pos.pop(code, None)

        # (B) 2차 목표 전량 익절 (+10%)
        elif ret_pct >= TAKE_PROFIT_2_PCT:
            orders.append({
                "side": "SELL", "code": code, "name": pos.get("name", code),
                "qty": qty, "price": cur, "reason": f"2차 목표 전량 익절 (+{ret_pct:.1f}%)", "full_exit": True,
            })
            est_cash += cur * qty * (1.0 - FEE_RATE - SELL_TAX_RATE)
            remaining_pos.pop(code, None)

        # (C) 고점 수익 잠금 트레일링 (최고 +4% 이상 찍고 고점 대비 -2.5%p 밀리면 즉시 수익 확정)
        elif peak_ret >= PEAK_TRAIL_ACTIVATE_PCT and (peak_ret - ret_pct) >= PEAK_TRAIL_DROP_PCT and ret_pct > 0:
            orders.append({
                "side": "SELL", "code": code, "name": pos.get("name", code),
                "qty": qty, "price": cur,
                "reason": f"수익 잠금 트레일링 (고점 +{peak_ret:.1f}% -> +{ret_pct:.1f}%)",
                "full_exit": True,
            })
            est_cash += cur * qty * (1.0 - FEE_RATE - SELL_TAX_RATE)
            remaining_pos.pop(code, None)

        # (D) 1차 익절 후 잔여 물량 본전 이상(+1.5%) 방어
        elif half_sold and ret_pct <= TRAILING_FLOOR_PCT:
            orders.append({
                "side": "SELL", "code": code, "name": pos.get("name", code),
                "qty": qty, "price": cur, "reason": f"1차 익절 후 수익 방어 (+{ret_pct:.1f}%)", "full_exit": True,
            })
            est_cash += cur * qty * (1.0 - FEE_RATE - SELL_TAX_RATE)
            remaining_pos.pop(code, None)

        # (E) 1차 목표 절반 익절 (+5%)
        elif not half_sold and ret_pct >= TAKE_PROFIT_1_PCT and qty >= 2:
            sell_q = max(1, qty // 2)
            orders.append({
                "side": "SELL", "code": code, "name": pos.get("name", code),
                "qty": sell_q, "price": cur, "reason": f"1차 목표 절반 익절 (+{ret_pct:.1f}%)", "full_exit": False,
            })
            est_cash += cur * sell_q * (1.0 - FEE_RATE - SELL_TAX_RATE)

        # (F) 10일 이상 정체 종목 자동 교체 (한 달 묵히기 방지 -> 강한 종목으로 자금 회전)
        elif hold_days >= STAGNANT_HOLD_DAYS and STAGNANT_LOW_PCT <= ret_pct <= STAGNANT_HIGH_PCT:
            orders.append({
                "side": "SELL", "code": code, "name": pos.get("name", code),
                "qty": qty, "price": cur,
                "reason": f"정체 종목 자금 회전 ({hold_days}일 보유 · {ret_pct:+.1f}%)",
                "full_exit": True,
            })
            est_cash += cur * qty * (1.0 - FEE_RATE - SELL_TAX_RATE)
            remaining_pos.pop(code, None)

        # (G) 신호 약화 조기 이탈 (수익권이거나 약보합일 때 매도 신호/저점수 전환 시 선제 방어)
        else:
            c_info = cand_by_code.get(code)
            if c_info:
                c_status = str(c_info.get("status", "")).lower()
                c_score = int(c_info.get("score", 60))
                if (c_status == "sell" or "매도" in c_status or c_score < 45) and ret_pct >= -1.5:
                    orders.append({
                        "side": "SELL", "code": code, "name": pos.get("name", code),
                        "qty": qty, "price": cur,
                        "reason": f"신호 약화 선제 방어 (점수 {c_score}점 · {ret_pct:+.1f}%)",
                        "full_exit": True,
                    })
                    est_cash += cur * qty * (1.0 - FEE_RATE - SELL_TAX_RATE)
                    remaining_pos.pop(code, None)

    # 2) 실전 방어 게이트: 장 초반(09:00~09:15) 또는 주간 서킷브레이커 발동 시 신규 매수 중단 (매도/손절은 정상 실행)
    if (not allow_new_buys) or _is_opening_no_buy_window(now_hhmm):
        return orders

    # 총자산 계산 및 국면별 유동적 비중으로 고득점 우량주 분할 매수
    total_equity = est_cash + sum(
        int(p["qty"]) * float(price_map.get(c) or p.get("cur_price") or p.get("avg_price") or 0)
        for c, p in remaining_pos.items()
    )
    max_per_stock = total_equity * rules["max_weight"]
    max_slots = rules["max_positions"]

    # 동일 섹터 최대 2종목 분산 카운트
    sector_counts: dict[str, int] = {}
    for c_code, p_info in remaining_pos.items():
        sec = _resolve_sector(c_code, p_info, sector_map) or _resolve_sector(c_code, cand_by_code.get(c_code), sector_map)
        if sec and sec != "기타":
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

    # 방금 매도한 종목은 같은 실행에서 바로 재매수하지 않도록 제외
    just_sold_codes = {o["code"] for o in orders if o["side"] == "SELL"}

    # 점수 높은 순으로 우선 검토
    sorted_cands = sorted(candidates, key=lambda x: int(x.get("score", 0)), reverse=True)

    for cand in sorted_cands:
        if len(remaining_pos) >= max_slots:
            break
        if cand.get("market") != "KR":
            continue
        if "주의" in str(cand.get("trust_label", "")):
            continue
        status = str(cand.get("status", "")).lower()
        if status == "sell" or "매도" in status:
            continue

        # 당일 급등주 추격매수 금지 (고점 물림 방지)
        day_chg = float(cand.get("change_pct") or cand.get("chg_pct") or 0)
        if day_chg >= MAX_INTRADAY_CHASE_PCT:
            continue

        score = int(cand.get("score", 0))
        is_buy_signal = (status == "buy") and (score >= rules["buy_min_score"])
        is_strong_pick = score >= rules["strong_min_score"]
        if not (is_buy_signal or is_strong_pick):
            continue

        code = str(cand.get("symbol", "")).split(".")[0].strip()
        if not code or len(code) != 6 or code in remaining_pos or code in just_sold_codes:
            continue

        # 동일 섹터 최대 2종목 분산 제한
        cand_sector = _resolve_sector(code, cand, sector_map)
        if cand_sector and cand_sector != "기타" and sector_counts.get(cand_sector, 0) >= MAX_SAME_SECTOR:
            continue
        price = float(price_map.get(code) or cand.get("price_num") or 0)
        if price <= 0:
            continue
        alloc = min(est_cash * 0.98, max_per_stock)
        qty = int(alloc // (price * (1.0 + FEE_RATE)))
        if qty <= 0:
            continue

        tag = "매수신호" if is_buy_signal else "고득점우량"
        orders.append({
            "side": "BUY", "code": code, "name": cand.get("name", code),
            "qty": qty, "price": price,
            "reason": f"{rules['label']} · {tag} ({score}점)",
        })
        est_cash -= price * qty * (1.0 + FEE_RATE)
        remaining_pos[code] = {
            "code": code, "name": cand.get("name", code),
            "qty": qty, "avg_price": price, "cur_price": price,
            "entry_date": today_date, "peak_ret_pct": 0.0,
            "sector": cand_sector,
        }
        if cand_sector and cand_sector != "기타":
            sector_counts[cand_sector] = sector_counts.get(cand_sector, 0) + 1

    return orders


def apply_orders_to_local_ledger(ledger: dict, orders: list, price_map: dict, now_kst: str) -> dict:
    """로컬 모의투자 원장에 체결 반영 (수수료 0.015% + 매도세 0.18% 정확 공제, 보유일·최고수익률 추적)."""
    cash = float(ledger.get("cash", INITIAL_SIM_CASH))
    positions = dict(ledger.get("positions") or {})
    trades = list(ledger.get("trades") or [])
    wins = int(ledger.get("wins", 0))
    losses = int(ledger.get("losses", 0))
    today_date = now_kst[:10]

    for od in orders:
        code = od["code"]
        qty = int(od["qty"])
        price = float(od["price"])
        if od["side"] == "SELL" and code in positions:
            pos = positions[code]
            sell_q = min(qty, int(pos["qty"]))
            if sell_q <= 0:
                continue
            avg = float(pos["avg_price"])
            gross = price * sell_q
            net = gross * (1.0 - FEE_RATE - SELL_TAX_RATE)
            pnl = net - (avg * sell_q)
            cash += net
            if pnl >= 0:
                wins += 1
            else:
                losses += 1
            rem = int(pos["qty"]) - sell_q
            if rem <= 0:
                positions.pop(code, None)
            else:
                pos["qty"] = rem
                pos["half_sold"] = True
                positions[code] = pos
            trades.insert(0, {
                "time": now_kst, "side": "SELL", "code": code, "name": od["name"],
                "qty": sell_q, "price": int(round(price)), "pnl": int(round(pnl)), "reason": od["reason"],
            })
        elif od["side"] == "BUY":
            cost = price * qty * (1.0 + FEE_RATE)
            if cost > cash or qty <= 0:
                continue
            cash -= cost
            positions[code] = {
                "code": code, "name": od["name"], "qty": qty,
                "avg_price": price, "cur_price": price, "half_sold": False,
                "entry_date": today_date, "peak_ret_pct": 0.0,
            }
            trades.insert(0, {
                "time": now_kst, "side": "BUY", "code": code, "name": od["name"],
                "qty": qty, "price": int(round(price)), "pnl": 0, "reason": od["reason"],
            })

    # 최신 평가가 및 최고 수익률(peak_ret_pct) 갱신
    eval_total = 0.0
    pos_list = []
    for code, p in positions.items():
        cur = float(price_map.get(code) or p.get("cur_price") or p.get("avg_price") or 0)
        p["cur_price"] = cur
        if not p.get("entry_date"):
            p["entry_date"] = today_date
        eval_total += cur * int(p["qty"])
        ret = ((cur / float(p["avg_price"])) - 1.0) * 100.0 if float(p["avg_price"]) > 0 else 0.0
        p["peak_ret_pct"] = round(max(float(p.get("peak_ret_pct", ret)), ret), 2)
        pos_list.append({
            "code": code, "name": p["name"], "qty": int(p["qty"]),
            "avg_price": int(round(float(p["avg_price"]))), "cur_price": int(round(cur)),
            "ret_pct": round(ret, 2),
        })

    initial = float(ledger.get("initial_cash", INITIAL_SIM_CASH))
    total_equity = cash + eval_total
    total_ret = ((total_equity / initial) - 1.0) * 100.0 if initial > 0 else 0.0
    closed = wins + losses
    win_rate = round((wins / closed) * 100.0, 1) if closed > 0 else None

    ledger.update({
        "initial_cash": initial,
        "cash": round(cash, 2),
        "positions": positions,
        "trades": trades[:40],
        "wins": wins,
        "losses": losses,
        "updated_kst": now_kst,
    })
    summary = {
        "mode": ledger.get("mode", "로컬 모의시뮬레이터"),
        "updated_kst": now_kst,
        "initial_cash": int(round(initial)),
        "cash": int(round(cash)),
        "total_equity": int(round(total_equity)),
        "total_return_pct": round(total_ret, 2),
        "closed_trades": closed,
        "win_rate": win_rate,
        "open_positions": pos_list,
        "recent_trades": trades[:10],
    }
    return ledger, summary


def run_paper_autotrader(
    feed_path: pathlib.Path = ROOT / "docs/data/live_feed.json",
    ledger_path: pathlib.Path = ROOT / "data/paper_ledger.json",
    summary_path: pathlib.Path = ROOT / "data/paper_trading_summary.json",
):
    now_dt = dt.datetime.utcnow() + dt.timedelta(hours=9)
    now_kst = now_dt.strftime("%Y-%m-%d %H:%M KST")
    today_date = now_dt.strftime("%Y-%m-%d")

    if not feed_path.exists():
        print("[WARN] live_feed.json 없음 — 먼저 스캔을 실행하세요.")
        return None

    env = json.loads(feed_path.read_text(encoding="utf-8"))
    pin = os.environ.get("APP_PIN", "").strip()
    payload = decrypt_envelope(env, pin) if env.get("encrypted") else env.get("payload", env)

    watchlist = payload.get("watchlist") or []
    candidates = payload.get("recommendations") or watchlist
    kr_regime = ((payload.get("market_regime") or {}).get("KR") or {}).get("regime", "혼조세")
    rules = get_dynamic_regime_rules(kr_regime)

    kr_cands = [c for c in candidates if c.get("market") == "KR"]
    print(
        f"[진단] 한국국면={kr_regime} -> {rules['label']} "
        f"(최대 {rules['max_positions']}종목 · 비중 {int(rules['max_weight']*100)}% · "
        f"손절 {rules['stop_loss_pct']}% · 1차익절 +{TAKE_PROFIT_1_PCT}% / 2차 +{TAKE_PROFIT_2_PCT}%) | KR후보={len(kr_cands)}개"
    )

    now_hhmm = now_dt.strftime("%H:%M")
    price_map = {}
    sector_map = {}
    for w in watchlist:
        if w.get("market") == "KR":
            c_code = str(w.get("symbol", "")).split(".")[0].strip()
            if float(w.get("price_num") or 0) > 0:
                price_map[c_code] = float(w["price_num"])
            if w.get("sector"):
                sector_map[c_code] = str(w["sector"]).strip()
    for c in candidates:
        if c.get("market") == "KR" and c.get("sector"):
            c_code = str(c.get("symbol", "")).split(".")[0].strip()
            if c_code and c_code not in sector_map:
                sector_map[c_code] = str(c["sector"]).strip()

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    else:
        ledger = {"initial_cash": INITIAL_SIM_CASH, "cash": INITIAL_SIM_CASH, "positions": {}, "trades": [], "wins": 0, "losses": 0}

    est_total_eq = float(ledger.get("cash", INITIAL_SIM_CASH)) + sum(
        int(p.get("qty", 0)) * float(price_map.get(c) or p.get("cur_price") or p.get("avg_price") or 0)
        for c, p in (ledger.get("positions") or {}).items()
    )
    cb_active, weekly_ret_pct = _update_weekly_circuit_breaker(ledger, est_total_eq, today_date)
    if _is_opening_no_buy_window(now_hhmm):
        print(f"[v6.3 방어] 장 초반({now_hhmm} KST) 호가 공백 구간 -> 매도/손절만 실행, 신규 매수 보류")
    if cb_active:
        print(f"[v6.3 서킷브레이커] 주간 수익률 {weekly_ret_pct:+.2f}% (기준 {WEEKLY_CIRCUIT_BREAKER_PCT}%) -> 이번 주 신규 매수 중단")

    app_key = os.environ.get("KIS_PAPER_APP_KEY", "").strip()
    app_secret = os.environ.get("KIS_PAPER_APP_SECRET", "").strip()
    acc_no = os.environ.get("KIS_PAPER_ACCOUNT_NO", "").strip()

    if app_key and app_secret and acc_no:
        try:
            client = KisPaperClient(app_key, app_secret, acc_no)
            client.authenticate()
            cash, positions = client.get_balance()
            # 기존 원장에 기록된 entry_date, peak_ret_pct, half_sold 메타데이터 유지
            prev_pos = ledger.get("positions") or {}
            for code, pos in positions.items():
                if code in prev_pos:
                    pos["entry_date"] = prev_pos[code].get("entry_date", today_date)
                    pos["peak_ret_pct"] = prev_pos[code].get("peak_ret_pct", 0.0)
                    pos["half_sold"] = prev_pos[code].get("half_sold", False)
                else:
                    pos["entry_date"] = today_date
                    pos["peak_ret_pct"] = 0.0
            orders = plan_paper_orders(cash, positions, candidates, price_map, kr_regime=kr_regime, today_date=today_date, now_hhmm=now_hhmm, sector_map=sector_map, allow_new_buys=not cb_active)
            executed = []
            for od in orders:
                try:
                    resp = client.place_market_order(od["side"], od["code"], od["qty"])
                    if str(resp.get("rt_cd")) == "0":
                        executed.append(od)
                except Exception as e:
                    print(f"[KIS 모의 주문 경고] {od['name']}: {e}")
            ledger["mode"] = "한국투자증권 KIS 모의투자 API"
            ledger["cash"] = cash
            ledger["positions"] = positions
            ledger, summary = apply_orders_to_local_ledger(ledger, executed, price_map, now_kst)
            print(f"[KIS 모의투자 완료] 체결 {len(executed)}건 · 총자산 {summary['total_equity']:,}원 ({summary['total_return_pct']}%)")
        except Exception as e:
            print(f"[KIS API 연결 실패 -> 로컬 모의시뮬레이터 폴백] {e}")
            ledger["mode"] = "로컬 가상 시뮬레이터 (KIS 키 확인 필요)"
            orders = plan_paper_orders(ledger["cash"], ledger["positions"], candidates, price_map, kr_regime=kr_regime, today_date=today_date, now_hhmm=now_hhmm, sector_map=sector_map, allow_new_buys=not cb_active)
            ledger, summary = apply_orders_to_local_ledger(ledger, orders, price_map, now_kst)
    else:
        ledger["mode"] = "로컬 가상 시뮬레이터 (1,000만 원 · 수익 실현형 스윙)"
        orders = plan_paper_orders(ledger["cash"], ledger["positions"], candidates, price_map, kr_regime=kr_regime, today_date=today_date, now_hhmm=now_hhmm, sector_map=sector_map, allow_new_buys=not cb_active)
        ledger, summary = apply_orders_to_local_ledger(ledger, orders, price_map, now_kst)
        print(f"[로컬 모의시뮬 완료] 가상 체결 {len(orders)}건 · 총자산 {summary['total_equity']:,}원 ({summary['total_return_pct']}%)")

    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    run_paper_autotrader()
