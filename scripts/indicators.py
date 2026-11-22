"""공통 기술적 지표 계산 및 정량 스코어링 엔진 (국장·미장 공용)."""
import numpy as np
import pandas as pd


def compute_indicators(df: pd.DataFrame, rsi_period: int = 14) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"].astype(float)
    vol = out["Volume"].astype(float)

    out["MA5"] = close.rolling(5).mean()
    out["MA20"] = close.rolling(20).mean()
    out["MA60"] = close.rolling(60).mean()

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / rsi_period, min_periods=rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / rsi_period, min_periods=rsi_period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["RSI"] = 100.0 - (100.0 / (1.0 + rs))
    out["RSI"] = out["RSI"].fillna(50.0)

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


def score_latest_row(df: pd.DataFrame, strategy: dict) -> dict:
    """최신 거래일 기준 0~100점 정량 점수 및 근거 태그 산출."""
    row = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else row

    close = float(row["Close"])
    ma5 = float(row["MA5"]) if pd.notna(row["MA5"]) else close
    ma20 = float(row["MA20"]) if pd.notna(row["MA20"]) else close
    ma60 = float(row["MA60"]) if pd.notna(row["MA60"]) else close
    rsi = float(row["RSI"])
    macd_h = float(row["MACD_HIST"])
    prev_macd_h = float(prev["MACD_HIST"])
    vol_ratio = float(row["VOL_RATIO"])
    bb_pct = float(row["BB_PCT"])
    ret_1d = ((close / float(prev["Close"])) - 1.0) * 100.0 if float(prev["Close"]) > 0 else 0.0
    ret_20d = float(row["RET_20D"]) if pd.notna(row["RET_20D"]) else 0.0

    score = 50.0
    reasons = []

    # 1. 추세 정배열 (최대 +20 / -15)
    if close > ma20 > ma60:
        score += 18
        reasons.append("중기 상승 정배열(종가>20일>60일)")
    elif close > ma20:
        score += 8
        reasons.append("20일선 상회")
    elif close < ma20 < ma60:
        score -= 15
        reasons.append("하락 역배열 주의")

    if ma5 > ma20 and close > ma5:
        score += 6

    # 2. RSI 과매도 반등 / 과열 경계 (최대 +14 / -15)
    if rsi <= strategy.get("rsi_buy_low", 32):
        score += 14
        reasons.append(f"RSI 과매도 구간({rsi:.1f})")
    elif strategy.get("rsi_buy_low", 32) < rsi <= strategy.get("rsi_buy_high", 62):
        score += 8
    elif rsi >= strategy.get("rsi_overbought", 72):
        score -= 15
        reasons.append(f"RSI 단기 과열({rsi:.1f})")

    # 3. MACD 모멘텀 전환 (최대 +12 / -8)
    if macd_h > 0 and macd_h > prev_macd_h:
        score += 12
        reasons.append("MACD 상승 가속")
    elif macd_h > prev_macd_h:
        score += 5
    elif macd_h < 0 and macd_h < prev_macd_h:
        score -= 8

    # 4. 거래량 수급 유입 (최대 +10)
    if vol_ratio >= strategy.get("vol_surge_ratio", 1.5) and ret_1d > 0:
        score += 10
        reasons.append(f"양봉 거래량 급증({vol_ratio:.1f}배)")
    elif vol_ratio >= strategy.get("vol_surge_ratio", 1.5) and ret_1d < -1.5:
        score -= 8
        reasons.append(f"장대음봉 투매 거래량({vol_ratio:.1f}배)")

    score = max(0.0, min(100.0, round(score, 1)))
    min_buy = strategy.get("min_score_buy_interest", 70)
    if score >= min_buy:
        grade = "매수 관심"
    elif score >= 45:
        grade = "관찰"
    else:
        grade = "주의"

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
    }
