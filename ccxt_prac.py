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
symbol = 'CHR/USDT:USDT'
timeframe = '5m'

exchange = ccxt.binance(config = {
    'apiKey' : api_key,
    'secret' : api_secret,
    'enableRateLimit' : True,
    'options' : {
        'defaultType' : 'future',
        'adjustForTimeDifference': True
    }
})

positions = exchange.fetch_positions(symbols=[symbol])
entryPrice = positions[0]['entryPrice'] if len(positions) > 0 else None
positionAmt = positions[0]['contracts'] if len(positions) > 0 else None  

pprint(positions)
print(entryPrice)
print(positionAmt)