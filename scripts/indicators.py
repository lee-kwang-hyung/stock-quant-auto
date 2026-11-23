"""공통 기술적 지표(MA/RSI/MACD/BB/ATR) 및 실전 매매 가격 시나리오 엔진 (v2.0)."""
import numpy as np
import pandas as pd


def compute_indicators(df: pd.DataFrame, rsi_period: int = 14) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"].astype(float)
    high = out["High"].astype(float)
    low = out["Low"].astype(float)
    vol = out["Volume"].astype(float)

    out["MA5"] = close.rolling(5).mean()
    out["MA20"] = close.rolling(20).mean()
    out["MA60"] = close.rolling(60).mean()

    # ATR(14) 변동성 지표 (손절가·목표가 산출용)
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["ATR14"] = tr.rolling(14, min_periods=5).mean().fillna(close * 0.03)
    out["LOW10"] = low.rolling(10, min_periods=3).min().fillna(close * 0.94)

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / rsi_period, min_periods=rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / rsi_period, min_periods=rsi_period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["RSI"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["MACD"] = ema12 - ema26
    out["MACD_SIG"] = out["MACD"].ewm(span=9, adjust=False).mean()
    out["MACD_HIST"] = out["MACD"] - out["MACD_SIG"]

    std20 = close.rolling(20).std()
    out["BB_UP"] = out["MA20"] + 2.0 * std20
    out["BB_LOW"] = out["MA20"] - 2.0 * std20
    bb_span = (out["BB_UP"] - out["BB_LOW"]).replace(0, np.nan)
    out["BB_PCT"] = ((close - out["BB_LOW"]) / bb_span).clip(0.0, 1.0).fillna(0.5)

    out["VOL_MA20"] = vol.rolling(20).mean().replace(0, np.nan)
    out["VOL_RATIO"] = (vol / out["VOL_MA20"]).fillna(1.0)
    out["RET_5D"] = close.pct_change(5) * 100.0
    out["RET_20D"] = close.pct_change(20) * 100.0
    return out


def compute_trade_plan(close: float, atr14: float, low10: float, market: str = "KR") -> dict:
    """변동성(ATR14)과 최근 10일 지지 저점을 조합해 진입 참고 구간·손절가·1/2차 목표가·손익비 산출."""
    close = float(close)
    atr = max(float(atr14), close * 0.018)
    entry_low = close * 0.985
    entry_high = close * 1.005

    # 손절가: 현재가 - 1.5*ATR 과 최근 10일 저점 중 높은 값, 단 -3.5% ~ -8.0% 범위로 안전 제한
    raw_stop = max(close - 1.5 * atr, float(low10) * 0.99)
    min_stop = close * 0.92
    max_stop = close * 0.965
    stop_loss = max(min_stop, min(max_stop, raw_stop))

    risk = max(close - stop_loss, close * 0.035)
    target_1 = close + risk * 1.8
    target_2 = close + risk * 3.0
    rr_1 = (target_1 - close) / risk
    rr_2 = (target_2 - close) / risk
    stop_pct = ((stop_loss / close) - 1.0) * 100.0
    t1_pct = ((target_1 / close) - 1.0) * 100.0
    t2_pct = ((target_2 / close) - 1.0) * 100.0

    def _fmt(v):
        return f"{v:,.0f}원" if market == "KR" else f"${v:,.2f}"

    return {
        "entry_zone": f"{_fmt(entry_low)} ~ {_fmt(entry_high)}",
        "stop_loss": f"{_fmt(stop_loss)} ({stop_pct:.1f}%)",
        "target_1": f"{_fmt(target_1)} (+{t1_pct:.1f}%)",
        "target_2": f"{_fmt(target_2)} (+{t2_pct:.1f}%)",
        "rr_ratio": f"1 : {rr_1:.1f} ~ {rr_2:.1f}",
    }


def score_latest_row(df: pd.DataFrame, strategy: dict, market: str = "KR") -> dict:
    row = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else row

    close = float(row["Close"])
    ma5 = float(row["MA5"]) if pd.notna(row["MA5"]) else close
    ma20 = float(row["MA20"]) if pd.notna(row["MA20"]) else close
    ma60 = float(row["MA60"]) if pd.notna(row["MA60"]) else close
    atr14 = float(row["ATR14"]) if "ATR14" in row and pd.notna(row["ATR14"]) else close * 0.03
    low10 = float(row["LOW10"]) if "LOW10" in row and pd.notna(row["LOW10"]) else close * 0.94
    rsi = float(row["RSI"])
    macd_h = float(row["MACD_HIST"])
    prev_macd_h = float(prev["MACD_HIST"])
    vol_ratio = float(row["VOL_RATIO"])
    bb_pct = float(row["BB_PCT"])
    ret_1d = ((close / float(prev["Close"])) - 1.0) * 100.0 if float(prev["Close"]) > 0 else 0.0
    ret_20d = float(row["RET_20D"]) if pd.notna(row["RET_20D"]) else 0.0

    score = 50.0
    reasons = []

    if close > ma20 > ma60:
        score += 18
        reasons.append("중기 상승 정배열")
    elif close > ma20:
        score += 8
        reasons.append("20일선 상회")
    elif close < ma20 < ma60:
        score -= 15
        reasons.append("하락 역배열 주의")

    if ma5 > ma20 and close > ma5:
        score += 6

    if rsi <= strategy.get("rsi_buy_low", 32):
        score += 14
        reasons.append(f"RSI 과매도({rsi:.1f})")
    elif strategy.get("rsi_buy_low", 32) < rsi <= strategy.get("rsi_buy_high", 62):
        score += 8
    elif rsi >= strategy.get("rsi_overbought", 72):
        score -= 15
        reasons.append(f"RSI 과열({rsi:.1f})")

    if macd_h > 0 and macd_h > prev_macd_h:
        score += 12
        reasons.append("MACD 상승 가속")
    elif macd_h > prev_macd_h:
        score += 5
    elif macd_h < 0 and macd_h < prev_macd_h:
        score -= 8

    if vol_ratio >= strategy.get("vol_surge_ratio", 1.5) and ret_1d > 0:
        score += 10
        reasons.append(f"양봉 수급 급증({vol_ratio:.1f}배)")
    elif vol_ratio >= strategy.get("vol_surge_ratio", 1.5) and ret_1d < -1.5:
        score -= 8
        reasons.append(f"음봉 투매({vol_ratio:.1f}배)")

    score = max(0.0, min(100.0, round(score, 1)))
    min_buy = strategy.get("min_score_buy_interest", 70)
    grade = "매수 관심" if score >= min_buy else ("관찰" if score >= 45 else "주의")
    plan = compute_trade_plan(close, atr14, low10, market=market)

    return {
        "close": round(close, 2),
        "ret_1d": round(ret_1d, 2),
        "ret_20d": round(ret_20d, 2),
        "rsi": round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2),
        "bb_pct": round(bb_pct * 100.0, 1),
        "score": score,
        "grade": grade,
        "reasons": " / ".join(reasons) if reasons else "중립 흐름",
        **plan,
    }
