import pandas as pd


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    ATR (Average True Range) 계산 - 최신 값 반환.

    Args:
        df: OHLCV DataFrame (columns: open, high, low, close)
        period: ATR 기간 (기본 14)

    Returns:
        현재(마지막 봉) ATR 값
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return float(atr.iloc[-1])
