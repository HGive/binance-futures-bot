import pandas as pd

def calculate_rsi(ohlc: pd.DataFrame, period: int = 14):
    ohlc = ohlc['close'].astype(float)
    delta = ohlc.diff()
    gains, declines = delta.copy(), delta.copy()
    gains[gains < 0] = 0
    declines[declines > 0] = 0

    _gain = gains.ewm(com=(period-1), min_periods=period).mean()
    _loss = declines.abs().ewm(com=(period-1), min_periods=period).mean()

    RS = _gain / _loss
    return pd.Series(100-(100/(1+RS)), name="RSI")


# def calculate_rsi(data, period=14):
#     changes = []
#     gains = []
#     losses = []
    
#     # Calculate price changes
#     for i in range(1, len(data)):
#         change = data[i][4] - data[i-1][4]
#         changes.append(change)
    
#     # Separate gains and losses
#     for change in changes:
#         if change > 0:
#             gains.append(change)
#             losses.append(0)
#         else:
#             gains.append(0)
#             losses.append(abs(change))
    
#     # Calculate average gains and losses over the period
#     average_gain = sum(gains[:period]) / period
#     average_loss = sum(losses[:period]) / period
    
#     # Calculate RS (Relative Strength)
#     if average_loss != 0:
#         rs = average_gain / average_loss
#     else:
#         rs = 1000000  # Handle division by zero case
    
#     # Calculate RSI
#     rsi = 100 - (100 / (1 + rs))
    
#     return rsi

