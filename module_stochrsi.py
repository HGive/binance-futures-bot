import pandas as pd

def calc_stoch_rsi(close_prices, period=14, k_period=3, d_period=3):
    """
    Stochastic RSI를 계산하는 함수.
    
    Parameters:
    close_prices (pd.Series): 종가 데이터 시리즈
    period (int): RSI를 계산하는 기간 (기본값: 14)
    k_period (int): K 라인에 대한 이동 평균 기간 (기본값: 3)
    d_period (int): D 라인에 대한 이동 평균 기간 (기본값: 3)
    
    Returns:
    pd.DataFrame: Stochastic RSI와 K, D 값을 포함한 데이터프레임
    """
    # RSI 계산
    delta = close_prices.diff(1)
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    # Stochastic RSI 계산
    stoch_rsi = (rsi - rsi.rolling(window=period).min()) / (rsi.rolling(window=period).max() - rsi.rolling(window=period).min())
    stoch_rsi = stoch_rsi * 100  # 0~100 범위로 변환

    # K, D 라인 계산
    k = stoch_rsi.rolling(window=k_period).mean()
    d = k.rolling(window=d_period).mean()

    # 결과를 데이터프레임으로 반환
    return pd.DataFrame({
        'StochRSI': stoch_rsi,
        'K': k,
        'D': d
    })
