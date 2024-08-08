import ccxt
import pandas as pd
import pprint
import numpy as np
import time

# 초기 설정
ticker = 'TAO/USDT'
initial_balance = 1000  # 초기 잔고 (달러)
risk_per_trade = 0.02  # 각 거래에 투자할 초기 잔고의 백분율
dca_percentage_drop = 4  # 물타기 퍼센티지 드랍
take_profit_percentage = 1.5  # 테이크 프로핏 퍼센티지
stop_loss_percentage = 4  # 스탑 로스 퍼센티지
rsi_threshold = 40  # RSI 기준값 (%)
previous_high_threshold = 10  # 이전 50개봉 close의 최고가 기준 (%)

# CCXT 설정
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future'  # 바이너리 선물 거래 설정
    }
})
ohlcv = exchange.fetch_ohlcv(ticker, '5m', limit=200)
currClose = ohlcv[-1][4]
df = pd.DataFrame(ohlcv,columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
middle_value = (df['high'] + df['close']) / 2
highest_last_40 = df['high'].rolling(window=40).max().iloc[-1]
highest_middle_40 = middle_value.rolling(window=40).max().iloc[-1]
print(df)
print(middle_value)
print(highest_last_40)
print(highest_middle_40)

# historical_data = exchange.fetch_ohlcv(ticker, '5m', limit= 1000)
# df = pd.DataFrame(historical_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
# print(df)