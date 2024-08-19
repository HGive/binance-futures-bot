import ccxt
import os
import comm
import time
import datetime 
import pandas as pd
from pprint import pprint
from dotenv import load_dotenv
from module_stochrsi import calc_stoch_rsi
from module_rsi import calc_rsi
load_dotenv()  # read file from local .env

api_key = os.environ['BINANCE_API_KEY']
api_secret = os.environ['BINANCE_API_SECRET']
symbol = 'CRV/USDC:USDC'
timeframe = '1h'

exchange = ccxt.binance(config = {
    'apiKey' : api_key,
    'secret' : api_secret,
    'enableRateLimit' : True,
    'options' : {
        'defaultType' : 'future'
    }
})

ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=200)
currClose = ohlcv[-1][4]
df= pd.DataFrame(ohlcv,columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
# df['date'] = pd.to_datetime(df['timestamp'], unit='ms').dt.strftime('%Y-%m-%d %H:%M:%S')
# Stochastic RSI 계산
df['stoch_k'], df['stoch_d'] = calc_stoch_rsi(df, 14, 3, 3)
rsi = calc_rsi(df, 14)
print(df)
print(currClose)