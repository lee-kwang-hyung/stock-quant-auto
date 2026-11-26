"""
v6.0 한국투자증권(KIS) 모의투자 전용 자동매매 엔진 (실계좌 주문 원천 차단).
- 모의투자 전용 도메인(https://openapivts.koreainvestment.com:29443) 및 모의 TR_ID(VTTC0012U/VTTC0011U/VTTC8434R)만 사용
- KIS 모의투자 API 키가 아직 없을 때는 로컬 가상 시뮬레이터(1,000만 원 시작 · 수수료 0.015% + 매도세 0.18% 반영)로 즉시 자동 검증
- 매매 원칙:
  1) 최대 4종목 분산, 종목당 최대 25% 비중
  2) -7.0% 손절 즉시 전량 매도
  3) +10.0% 1차 목표가 절반(50%) 익절
  4) +18.0% 2차 목표가 전량 익절 또는 1차 익절 후 +2.0% 회귀 시 본전 트레일링 매도
  5) 코스피 약세 주의 국면에서는 신규 매수 자동 차단(현금 보호)
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
STOP_LOSS_PCT = -7.0
TAKE_PROFIT_1_PCT = 10.0
TAKE_PROFIT_2_PCT = 18.0
TRAILING_FLOOR_PCT = 2.0
FEE_RATE = 0.00015       # 매수/매도 위탁수수료 0.015%
SELL_TAX_RATE = 0.0018   # 코스피/코스닥 매도 증권거래세 0.18%
INITIAL_SIM_CASH = 10_000_000


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
) -> list:
    """
    보유 포지션 점검(손절/1차·2차 익절/트레일링) 후 여유 슬롯에 안정형 매수 주문 계획 생성.
    반환: [{"side": "SELL"|"BUY", "code": "005930", "name": "...", "qty": 10, "price": 75000, "reason": "..."}]
    """
    orders = []
    est_cash = float(cash)
    remaining_pos = dict(positions)

    # 1) 보유 종목 매도 조건 점검 (손절 -> 2차 익절 -> 본전 트레일링 -> 1차 절반 익절)
    for code, pos in list(positions.items()):
        qty = int(pos.get("qty", 0))
        avg = float(pos.get("avg_price", 0))
        cur = float(price_map.get(code) or pos.get("cur_price") or 0)
        if qty <= 0 or avg <= 0 or cur <= 0:
            continue
        ret_pct = ((cur / avg) - 1.0) * 100.0
        half_sold = bool(pos.get("half_sold", False))

        if ret_pct <= STOP_LOSS_PCT:
            orders.append({
                "side": "SELL", "code": code, "name": pos.get("name", code),
                "qty": qty, "price": cur, "reason": f"자동 손절 ({ret_pct:.1f}%)", "full_exit": True,
            })
            est_cash += cur * qty * (1.0 - FEE_RATE - SELL_TAX_RATE)
            remaining_pos.pop(code, None)
        elif ret_pct >= TAKE_PROFIT_2_PCT:
            orders.append({
                "side": "SELL", "code": code, "name": pos.get("name", code),
                "qty": qty, "price": cur, "reason": f"2차 목표 전량 익절 (+{ret_pct:.1f}%)", "full_exit": True,
            })
            est_cash += cur * qty * (1.0 - FEE_RATE - SELL_TAX_RATE)
            remaining_pos.pop(code, None)
        elif half_sold and ret_pct <= TRAILING_FLOOR_PCT:
            orders.append({
                "side": "SELL", "code": code, "name": pos.get("name", code),
                "qty": qty, "price": cur, "reason": f"본전 트레일링 방어 (+{ret_pct:.1f}%)", "full_exit": True,
            })
            est_cash += cur * qty * (1.0 - FEE_RATE - SELL_TAX_RATE)
            remaining_pos.pop(code, None)
        elif not half_sold and ret_pct >= TAKE_PROFIT_1_PCT and qty >= 2:
            sell_q = max(1, qty // 2)
            orders.append({
                "side": "SELL", "code": code, "name": pos.get("name", code),
                "qty": sell_q, "price": cur, "reason": f"1차 목표 절반 익절 (+{ret_pct:.1f}%)", "full_exit": False,
            })
            est_cash += cur * sell_q * (1.0 - FEE_RATE - SELL_TAX_RATE)

    # 2) 하락 주의 국면에서는 신규 매수 차단 (현금 보호)
    if "약세" in (kr_regime or ""):
        return orders

    # 3) 총자산 계산 및 빈 슬롯에 안정형 추천주 분할 매수
    total_equity = est_cash + sum(
        int(p["qty"]) * float(price_map.get(c) or p.get("cur_price") or p.get("avg_price") or 0)
        for c, p in remaining_pos.items()
    )
    max_per_stock = total_equity * MAX_WEIGHT_PER_STOCK

    for cand in candidates:
        if len(remaining_pos) >= MAX_POSITIONS:
            break
        if cand.get("market") != "KR":
            continue
        if cand.get("status") != "buy" or int(cand.get("score", 0)) < 65:
            continue
        if "주의" in str(cand.get("trust_label", "")):
            continue
        code = str(cand.get("symbol", "")).split(".")[0].strip()
        if not code or len(code) != 6 or code in remaining_pos:
            continue
        price = float(price_map.get(code) or cand.get("price_num") or 0)
        if price <= 0:
            continue
        alloc = min(est_cash * 0.98, max_per_stock)
        qty = int(alloc // (price * (1.0 + FEE_RATE)))
        if qty <= 0:
            continue
        orders.append({
            "side": "BUY", "code": code, "name": cand.get("name", code),
            "qty": qty, "price": price, "reason": f"안정형 매수 신호 ({cand.get('score')}점)",
        })
        est_cash -= price * qty * (1.0 + FEE_RATE)
        remaining_pos[code] = {"code": code, "name": cand.get("name", code), "qty": qty, "avg_price": price, "cur_price": price}

    return orders


def apply_orders_to_local_ledger(ledger: dict, orders: list, price_map: dict, now_kst: str) -> dict:
    """로컬 모의투자 원장에 체결 반영 (수수료 0.015% + 매도세 0.18% 정확 공제)."""
    cash = float(ledger.get("cash", INITIAL_SIM_CASH))
    positions = dict(ledger.get("positions") or {})
    trades = list(ledger.get("trades") or [])
    wins = int(ledger.get("wins", 0))
    losses = int(ledger.get("losses", 0))

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
            }
            trades.insert(0, {
                "time": now_kst, "side": "BUY", "code": code, "name": od["name"],
                "qty": qty, "price": int(round(price)), "pnl": 0, "reason": od["reason"],
            })

    # 최신 평가가 반영
    eval_total = 0.0
    pos_list = []
    for code, p in positions.items():
        cur = float(price_map.get(code) or p.get("cur_price") or p.get("avg_price") or 0)
        p["cur_price"] = cur
        eval_total += cur * int(p["qty"])
        ret = ((cur / float(p["avg_price"])) - 1.0) * 100.0 if float(p["avg_price"]) > 0 else 0.0
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
    now_kst = (dt.datetime.utcnow() + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")
    if not feed_path.exists():
        print("[WARN] live_feed.json 없음 — 먼저 스캔을 실행하세요.")
        return None

    env = json.loads(feed_path.read_text(encoding="utf-8"))
    pin = os.environ.get("APP_PIN", "").strip()
    payload = decrypt_envelope(env, pin) if env.get("encrypted") else env.get("payload", env)

    watchlist = payload.get("watchlist") or []
    candidates = payload.get("recommendations") or watchlist
    kr_regime = ((payload.get("market_regime") or {}).get("KR") or {}).get("regime", "혼조세")

    # 매수 보류 원인 확인용 진단 로그 (전략/로직 변경 없음)
    kr_cands = [c for c in candidates if c.get("market") == "KR"]
    buy_cands = [
        c for c in kr_cands
        if c.get("status") == "buy"
        and int(c.get("score", 0)) >= 65
        and "주의" not in str(c.get("trust_label", ""))
    ]
    print(f"[진단] 한국국면={kr_regime} | KR후보={len(kr_cands)}개 | 매수조건통과(65점+)={len(buy_cands)}개")
    if "약세" in (kr_regime or ""):
        print("[진단] 약세장 현금 보호 발동 -> 신규 매수 자동 차단")

    price_map = {}
    for w in watchlist:
        if w.get("market") == "KR" and float(w.get("price_num") or 0) > 0:
            price_map[str(w.get("symbol", "")).split(".")[0]] = float(w["price_num"])

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    else:
        ledger = {"initial_cash": INITIAL_SIM_CASH, "cash": INITIAL_SIM_CASH, "positions": {}, "trades": [], "wins": 0, "losses": 0}

    app_key = os.environ.get("KIS_PAPER_APP_KEY", "").strip()
    app_secret = os.environ.get("KIS_PAPER_APP_SECRET", "").strip()
    acc_no = os.environ.get("KIS_PAPER_ACCOUNT_NO", "").strip()

    if app_key and app_secret and acc_no:
        try:
            client = KisPaperClient(app_key, app_secret, acc_no)
            client.authenticate()
            cash, positions = client.get_balance()
            orders = plan_paper_orders(cash, positions, candidates, price_map, kr_regime=kr_regime)
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
            orders = plan_paper_orders(ledger["cash"], ledger["positions"], candidates, price_map, kr_regime=kr_regime)
            ledger, summary = apply_orders_to_local_ledger(ledger, orders, price_map, now_kst)
    else:
        ledger["mode"] = "로컬 가상 시뮬레이터 (1,000만 원 · KIS 키 등록 전 자동 검증)"
        orders = plan_paper_orders(ledger["cash"], ledger["positions"], candidates, price_map, kr_regime=kr_regime)
        ledger, summary = apply_orders_to_local_ledger(ledger, orders, price_map, now_kst)
        print(f"[로컬 모의시뮬 완료] 가상 체결 {len(orders)}건 · 총자산 {summary['total_equity']:,}원 ({summary['total_return_pct']}%)")

    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    run_paper_autotrader()
