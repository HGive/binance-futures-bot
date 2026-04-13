import pandas as pd


def calc_bb_series(close: pd.Series, period: int = 20, std: float = 2.0):
    """
    볼린저 밴드 계산.

    Returns:
        (mid, upper, lower): 각각 pd.Series
    """
    mid = close.rolling(window=period).mean()
    sigma = close.rolling(window=period).std()
    upper = mid + std * sigma
    lower = mid - std * sigma
    return mid, upper, lower
