"""
v6.2 모의 자동매매 엔진(scripts/kis_paper_trader.py) 전용 자체 검증 스크립트.
- 기존 scripts/self_test.py(23개 통합 검증)를 덮어쓰지 않고 독립 실행할 수 있습니다.
- 실행 방법: python scripts/test_kis_paper_trader.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import kis_paper_trader as kpt


def test_safety_locks():
    """1. 실계좌 주문 원천 차단 및 모의투자 도메인·TR_ID 고정 검증."""
    assert kpt.PAPER_BASE_URL == "https://openapivts.koreainvestment.com:29443"
    assert ":9443" not in kpt.PAPER_BASE_URL
    assert kpt.TR_BUY_PAPER == "VTTC0012U"
    assert kpt.TR_SELL_PAPER == "VTTC0011U"
    assert kpt.TR_BALANCE_PAPER == "VTTC8434R"
    for tr in (kpt.TR_BUY_PAPER, kpt.TR_SELL_PAPER, kpt.TR_BALANCE_PAPER):
        assert tr.startswith("VTTC") and not tr.startswith("TTTC")


def test_dynamic_regime_buying():
    """2. 국면별 유동적 비중·진입 기준 및 급등주/주의종목 차단 검증."""
    cands = [
        {"market": "KR", "symbol": "005930.KS", "name": "삼성전자", "status": "buy", "score": 68, "price_num": 60000, "change_pct": 1.2, "trust_label": "우량"},
        {"market": "KR", "symbol": "000660.KS", "name": "SK하이닉스", "status": "watch", "score": 74, "price_num": 180000, "change_pct": 2.0, "trust_label": "우량"},
        {"market": "KR", "symbol": "035420.KS", "name": "NAVER", "status": "buy", "score": 61, "price_num": 200000, "change_pct": 0.8, "trust_label": "우량"},
        {"market": "KR", "symbol": "005380.KS", "name": "현대차", "status": "buy", "score": 78, "price_num": 240000, "change_pct": 1.0, "trust_label": "주의"},
        {"market": "KR", "symbol": "035720.KS", "name": "카카오", "status": "buy", "score": 76, "price_num": 40000, "change_pct": 9.5, "trust_label": "우량"},
    ]
    prices = {"005930": 60000, "000660": 180000, "035420": 200000, "005380": 240000, "035720": 40000}

    # 약세장: 최대 2종목, 종목당 12.5%(125만 원 이하), 주의/급등주/61점 제외
    bear_orders = kpt.plan_paper_orders(10_000_000, {}, cands, prices, kr_regime="약세 주의", today_date="2026-11-27")
    assert len(bear_orders) == 2
    assert {o["code"] for o in bear_orders} == {"005930", "000660"}
    assert all(o["qty"] * o["price"] <= 1_250_000 for o in bear_orders)

    # 혼조장: 61점 매수신호(NAVER)까지 포함 3종목, 종목당 20%(200만 원 이하)
    side_orders = kpt.plan_paper_orders(10_000_000, {}, cands, prices, kr_regime="혼조세", today_date="2026-11-27")
    assert len(side_orders) == 3
    assert {o["code"] for o in side_orders} == {"005930", "000660", "035420"}
    assert all(o["qty"] * o["price"] <= 2_000_000 for o in side_orders)


def test_stop_loss_normal_and_bear():
    """3. 원금 방어 손절(-5.0% 일반 / -4.5% 약세장) 검증."""
    pos = {"005930": {"name": "삼성전자", "qty": 20, "avg_price": 100000, "cur_price": 95400, "entry_date": "2026-11-25"}}
    # -4.6%: 약세장(-4.5%)에서는 즉시 손절
    o_bear = kpt.plan_paper_orders(5_000_000, dict(pos), [], {"005930": 95400}, kr_regime="약세 주의", today_date="2026-11-27")
    assert len(o_bear) == 1 and o_bear[0]["side"] == "SELL" and "손절" in o_bear[0]["reason"]

    # -4.6%: 혼조장(-5.0%)에서는 아직 유지
    o_side_hold = kpt.plan_paper_orders(5_000_000, dict(pos), [], {"005930": 95400}, kr_regime="혼조세", today_date="2026-11-27")
    assert len(o_side_hold) == 0

    # -5.1%: 혼조장에서도 즉시 손절
    o_side_sl = kpt.plan_paper_orders(5_000_000, dict(pos), [], {"005930": 94900}, kr_regime="혼조세", today_date="2026-11-27")
    assert len(o_side_sl) == 1 and o_side_sl[0]["side"] == "SELL" and "손절" in o_side_sl[0]["reason"]


def test_take_profit_and_trailing_lock():
    """4. +5% 절반 익절, +10% 전량 익절, 고점 수익 잠금, 1차 익절 후 +1.5% 방어 검증."""
    # +5.2% -> 1차 절반 익절
    p1 = {"005930": {"name": "삼성전자", "qty": 20, "avg_price": 100000, "cur_price": 105200, "entry_date": "2026-11-25"}}
    o1 = kpt.plan_paper_orders(5_000_000, p1, [], {"005930": 105200}, kr_regime="혼조세", today_date="2026-11-27")
    assert len(o1) == 1 and o1[0]["qty"] == 10 and "1차 목표 절반 익절" in o1[0]["reason"]

    # +10.2% -> 2차 전량 익절
    p2 = {"005930": {"name": "삼성전자", "qty": 10, "avg_price": 100000, "cur_price": 110200, "half_sold": True, "entry_date": "2026-11-25"}}
    o2 = kpt.plan_paper_orders(5_000_000, p2, [], {"005930": 110200}, kr_regime="혼조세", today_date="2026-11-27")
    assert len(o2) == 1 and o2[0]["qty"] == 10 and "2차 목표 전량 익절" in o2[0]["reason"]

    # 고점 +6.5% 찍고 +3.5%로 하락(-3.0%p) -> 수익 잠금 트레일링
    p3 = {"005930": {"name": "삼성전자", "qty": 20, "avg_price": 100000, "cur_price": 103500, "peak_ret_pct": 6.5, "entry_date": "2026-11-25"}}
    o3 = kpt.plan_paper_orders(5_000_000, p3, [], {"005930": 103500}, kr_regime="혼조세", today_date="2026-11-27")
    assert len(o3) == 1 and o3[0]["qty"] == 20 and "수익 잠금 트레일링" in o3[0]["reason"]

    # 1차 익절 후 +1.4%로 회귀 -> 수익 방어 전량 매도
    p4 = {"005930": {"name": "삼성전자", "qty": 10, "avg_price": 100000, "cur_price": 101400, "half_sold": True, "entry_date": "2026-11-25"}}
    o4 = kpt.plan_paper_orders(5_000_000, p4, [], {"005930": 101400}, kr_regime="혼조세", today_date="2026-11-27")
    assert len(o4) == 1 and o4[0]["qty"] == 10 and "1차 익절 후 수익 방어" in o4[0]["reason"]


def test_stagnant_rotation_and_signal_exit():
    """5. 10일 이상 정체 종목 교체 및 신호 약화 선제 방어 검증."""
    # 12일 보유, +0.5% 횡보 -> 정체 종목 자금 회전
    p_stag = {"005930": {"name": "삼성전자", "qty": 20, "avg_price": 100000, "cur_price": 100500, "entry_date": "2026-11-15"}}
    o_stag = kpt.plan_paper_orders(5_000_000, p_stag, [], {"005930": 100500}, kr_regime="혼조세", today_date="2026-11-27")
    assert len(o_stag) == 1 and "정체 종목 자금 회전" in o_stag[0]["reason"]

    # 점수 40점 하락 + 수익권(+0.8%) -> 신호 약화 선제 방어
    p_weak = {"005930": {"name": "삼성전자", "qty": 20, "avg_price": 100000, "cur_price": 100800, "entry_date": "2026-11-25"}}
    c_weak = [{"market": "KR", "symbol": "005930.KS", "name": "삼성전자", "status": "sell", "score": 40, "price_num": 100800}]
    o_weak = kpt.plan_paper_orders(5_000_000, p_weak, c_weak, {"005930": 100800}, kr_regime="혼조세", today_date="2026-11-27")
    assert len(o_weak) == 1 and "신호 약화 선제 방어" in o_weak[0]["reason"]


def test_local_ledger_accounting():
    """6. 로컬 원장 수수료·세금 공제, 승률 계산, 보유일·최고수익률 기록 검증."""
    ledger = {"initial_cash": 10_000_000, "cash": 10_000_000, "positions": {}, "trades": [], "wins": 0, "losses": 0}
    buy_orders = [{"side": "BUY", "code": "005930", "name": "삼성전자", "qty": 20, "price": 100000, "reason": "테스트 매수"}]
    ledger, summary = kpt.apply_orders_to_local_ledger(ledger, buy_orders, {"005930": 100000}, "2026-11-27 10:00 KST")
    assert "005930" in ledger["positions"]
    assert ledger["positions"]["005930"]["entry_date"] == "2026-11-27"
    assert ledger["cash"] < 8_000_000  # 수수료 0.015% 반영

    sell_orders = [{"side": "SELL", "code": "005930", "name": "삼성전자", "qty": 20, "price": 106000, "reason": "테스트 익절"}]
    ledger, summary = kpt.apply_orders_to_local_ledger(ledger, sell_orders, {"005930": 106000}, "2026-11-28 10:00 KST")
    assert len(ledger["positions"]) == 0
    assert summary["closed_trades"] == 1
    assert summary["win_rate"] == 100.0
    assert summary["total_return_pct"] > 0




def test_opening_window_blocks_buy_allows_sell():
    """7. [v6.3] 09:00~09:15 장 초반 신규 매수 차단 및 손절/익절 매도 허용 검증."""
    cands = [
        {"market": "KR", "symbol": "005930.KS", "name": "삼성전자", "status": "buy", "score": 78, "price_num": 60000, "change_pct": 1.0, "trust_label": "우량", "sector": "반도체"},
    ]
    pos_sl = {"000660": {"name": "SK하이닉스", "qty": 10, "avg_price": 100000, "cur_price": 94000, "entry_date": "2026-11-25"}}
    prices = {"005930": 60000, "000660": 94000}

    # 09:05: 손절 매도는 즉시 실행되지만 신규 매수는 차단
    o_0905 = kpt.plan_paper_orders(10_000_000, dict(pos_sl), cands, prices, kr_regime="혼조세", today_date="2026-11-27", now_hhmm="09:05")
    assert len(o_0905) == 1 and o_0905[0]["side"] == "SELL" and o_0905[0]["code"] == "000660"

    # 09:15: 장 초반 구간 종료 -> 매도 + 신규 매수 모두 허용
    o_0915 = kpt.plan_paper_orders(10_000_000, dict(pos_sl), cands, prices, kr_regime="혼조세", today_date="2026-11-27", now_hhmm="09:15")
    sides = {o["side"] for o in o_0915}
    assert sides == {"SELL", "BUY"}


def test_sector_diversification_limit():
    """8. [v6.3] 동일 섹터 최대 2종목 분산 제한 검증."""
    cands = [
        {"market": "KR", "symbol": "005930.KS", "name": "삼성전자", "status": "buy", "score": 85, "price_num": 60000, "change_pct": 1.0, "trust_label": "우량", "sector": "반도체"},
        {"market": "KR", "symbol": "000660.KS", "name": "SK하이닉스", "status": "buy", "score": 82, "price_num": 180000, "change_pct": 1.2, "trust_label": "우량", "sector": "반도체"},
        {"market": "KR", "symbol": "042700.KS", "name": "한미반도체", "status": "buy", "score": 80, "price_num": 100000, "change_pct": 1.5, "trust_label": "우량", "sector": "반도체"},
        {"market": "KR", "symbol": "005380.KS", "name": "현대차", "status": "buy", "score": 75, "price_num": 240000, "change_pct": 0.9, "trust_label": "우량", "sector": "자동차"},
    ]
    prices = {"005930": 60000, "000660": 180000, "042700": 100000, "005380": 240000}
    orders = kpt.plan_paper_orders(10_000_000, {}, cands, prices, kr_regime="혼조세", today_date="2026-11-27", now_hhmm="10:05")
    codes = {o["code"] for o in orders}
    # 반도체 3개 중 상위 2개(005930, 000660)만 매수하고 042700은 스킵, 자동차(005380) 매수
    assert codes == {"005930", "000660", "005380"}


def test_weekly_circuit_breaker():
    """9. [v6.3] 주간 누적 손실 -5% 서킷브레이커 발동 및 차주 자동 해제 검증."""
    ledger = {"initial_cash": 10_000_000, "cash": 10_000_000, "positions": {}, "trades": []}
    cb1, ret1 = kpt._update_weekly_circuit_breaker(ledger, 10_000_000, "2026-11-23")
    assert cb1 is False and ret1 == 0.0

    # 같은 주 수요일 총자산 945만 원(-5.5%) -> 서킷브레이커 발동
    cb2, ret2 = kpt._update_weekly_circuit_breaker(ledger, 9_450_000, "2026-11-25")
    assert cb2 is True and ret2 == -5.5

    cands = [{"market": "KR", "symbol": "005930.KS", "name": "삼성전자", "status": "buy", "score": 85, "price_num": 60000, "change_pct": 1.0, "trust_label": "우량"}]
    orders_blocked = kpt.plan_paper_orders(9_450_000, {}, cands, {"005930": 60000}, kr_regime="혼조세", today_date="2026-11-25", now_hhmm="10:05", allow_new_buys=not cb2)
    assert len(orders_blocked) == 0

    # 다음 주 월요일(2026-11-30) -> 주차 변경으로 서킷브레이커 자동 해제
    cb3, ret3 = kpt._update_weekly_circuit_breaker(ledger, 9_450_000, "2026-11-30")
    assert cb3 is False and ret3 == 0.0




def test_intraday_crash_filter_and_daily_idempotency():
    """10. [v6.4] 당일 -4% 이하 급락주 진입 차단 및 당일 동일 종목 중복 매수 방지(멱등성) 검증."""
    cands = [
        {"market": "KR", "symbol": "005930.KS", "name": "급락악재주", "status": "buy", "score": 88, "price_num": 60000, "change_pct": -5.2, "trust_label": "우량"},
        {"market": "KR", "symbol": "000660.KS", "name": "중복매수대상", "status": "buy", "score": 85, "price_num": 150000, "change_pct": 1.1, "trust_label": "우량"},
        {"market": "KR", "symbol": "005380.KS", "name": "정상우량주", "status": "buy", "score": 80, "price_num": 240000, "change_pct": 0.8, "trust_label": "우량"},
    ]
    prices = {"005930": 60000, "000660": 150000, "005380": 240000}
    orders = kpt.plan_paper_orders(
        10_000_000, {}, cands, prices, kr_regime="혼조세",
        today_date="2026-11-28", now_hhmm="10:05", blocked_buy_codes={"000660"},
    )
    codes = {o["code"] for o in orders}
    assert codes == {"005380"}, f"Expected only 005380, got {codes}"


def run_all():
    tests = [
        test_safety_locks,
        test_dynamic_regime_buying,
        test_stop_loss_normal_and_bear,
        test_take_profit_and_trailing_lock,
        test_stagnant_rotation_and_signal_exit,
        test_local_ledger_accounting,
        test_opening_window_blocks_buy_allows_sell,
        test_sector_diversification_limit,
        test_weekly_circuit_breaker,
        test_intraday_crash_filter_and_daily_idempotency,
    ]
    for fn in tests:
        fn()
        print(f"[PASS] {fn.__name__}")
    print(f"ALL {len(tests)}/{len(tests)} KIS PAPER TRADER v6.4 TESTS PASSED")


if __name__ == "__main__":
    run_all()
