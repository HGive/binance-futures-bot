import pandas as pd


def calc_atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR 시리즈 계산 (백테스트용 - 봉별 ATR 전체 반환)"""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    ATR (Average True Range) 계산 - 최신 값 반환.

    Args:
        df: OHLCV DataFrame (columns: open, high, low, close)
        period: ATR 기간 (기본 14)

    Returns:
        현재(마지막 봉) ATR 값
    """
    atr = calc_atr_series(
        df["high"].astype(float),
        df["low"].astype(float),
        df["close"].astype(float),
        period,
    )
    return float(atr.iloc[-1])
