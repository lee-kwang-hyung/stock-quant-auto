"""v4.0 정밀 분석 엔진: 시장 국면(KOSPI/S&P500) 필터 + 지지/저항선 기반 매수·매도·손절 + 손익비(R/R) 검증 + 사후 적중률 집계."""
import csv
import pathlib
import numpy as np
import pandas as pd


def detect_market_regime(df: pd.DataFrame, market_name: str) -> dict:
    """지수(KOSPI/S&P500) 일봉으로 현재 시장 국면(강세/혼조/약세) 판별."""
    if df is None or df.empty or len(df) < 60:
        return {"market": market_name, "regime": "혼조", "emoji": "⛅", "score_adj": 0, "note": "지수 데이터 부족(중립 적용)"}
    close = df["Close"].astype(float)
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    cur = float(close.iloc[-1])
    ret20 = (cur / float(close.iloc[-21]) - 1.0) * 100.0 if len(close) >= 21 else 0.0

    if cur > ma20 and ma20 > ma60:
        return {"market": market_name, "regime": "강세장", "emoji": "☀️", "score_adj": 4, "note": f"20·60일선 정배열 (최근 20일 {ret20:+.1f}%)"}
    if cur < ma20 and cur < ma60:
        return {"market": market_name, "regime": "약세 주의", "emoji": "🌧️", "score_adj": -10, "note": f"20·60일선 하향 이탈 (최근 20일 {ret20:+.1f}%) · 보수적 분할 필수"}
    return {"market": market_name, "regime": "혼조세", "emoji": "⛅", "score_adj": -2, "note": f"단기 방향성 탐색 구간 (최근 20일 {ret20:+.1f}%)"}


def compute_sr_precision_plan(df: pd.DataFrame, market: str = "KR") -> dict:
    """최근 20일/60일 지지·저항선과 ATR을 결합한 정밀 매수·손절·1/2차 목표가 및 손익비 계산."""
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    cur = float(close.iloc[-1])

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    if np.isnan(atr) or atr <= 0:
        atr = cur * 0.025

    support_20 = float(low.tail(20).min())
    resist_20 = float(high.tail(20).max())
    resist_60 = float(high.tail(60).max()) if len(high) >= 60 else resist_20 * 1.05

    # 진입 구간: 현재가 - 0.6*ATR ~ 현재가 + 0.2*ATR (지지선 아래로 내려가지 않게 보정)
    entry_lo = max(support_20, cur - 0.6 * atr)
    entry_hi = cur + 0.2 * atr
    entry_mid = (entry_lo + entry_hi) / 2.0

    # 손절가: max(최근 20일 지지선 * 0.985, 현재가 - 1.6*ATR) 하되 최소 -2.5% ~ 최대 -7.5% 제한
    raw_stop = min(support_20 * 0.985, cur - 1.5 * atr)
    stop = max(cur * 0.925, min(cur * 0.975, raw_stop))

    # 목표가: 1차는 20일 저항선과 +1.8*ATR 중 합리적 상단, 2차는 60일 저항선과 +3.0*ATR
    risk = max(cur - stop, cur * 0.02)
    t1 = max(cur + risk * 1.6, min(resist_20 * 1.01, cur + risk * 2.2))
    t2 = max(t1 * 1.035, max(resist_60, cur + risk * 2.8))

    stop_pct = (stop / cur - 1.0) * 100.0
    t1_pct = (t1 / cur - 1.0) * 100.0
    t2_pct = (t2 / cur - 1.0) * 100.0
    rr_ratio = round((t1 - entry_mid) / max(entry_mid - stop, 1e-6), 2)

    def _fmt(v: float) -> str:
        return f"{int(round(v)):,}원" if market == "KR" else f"${v:,.2f}"

    return {
        "entry_zone": f"{_fmt(entry_lo)} ~ {_fmt(entry_hi)}",
        "stop_loss": f"{_fmt(stop)} ({stop_pct:+.1f}%)",
        "target_1": f"{_fmt(t1)} ({t1_pct:+.1f}%)",
        "target_2": f"{_fmt(t2)} ({t2_pct:+.1f}%)",
        "stop_num": round(stop, 2),
        "target1_num": round(t1, 2),
        "rr_ratio": rr_ratio,
        "rr_ok": rr_ratio >= 1.5,
        "support_text": _fmt(support_20),
        "resist_text": _fmt(resist_20),
    }


def compute_historical_accuracy(csv_path: pathlib.Path) -> dict:
    """data/signals_history.csv의 누적 D+5 사후 수익률로 실제 적중률 투명 집계."""
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return {"tracked": 0, "win_rate": None, "avg_d5": None, "label": "누적 집계 대기 중"}
    d5_vals = []
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                v = (r.get("d5_ret_pct") or "").strip()
                if v:
                    try:
                        d5_vals.append(float(v))
                    except ValueError:
                        pass
    except Exception:
        pass
    if not d5_vals:
        return {"tracked": 0, "win_rate": None, "avg_d5": None, "label": "5거래일 경과 후 자동 집계"}
    wins = sum(1 for x in d5_vals if x > 0)
    wr = round(wins / len(d5_vals) * 100.0, 1)
    avg = round(sum(d5_vals) / len(d5_vals), 2)
    return {
        "tracked": len(d5_vals),
        "win_rate": wr,
        "avg_d5": avg,
        "label": f"누적 {len(d5_vals)}건 · 5일 승률 {wr:.1f}% (평균 {avg:+.2f}%)",
    }
