import os
import time
from dotenv import load_dotenv
import ccxt
import pandas as pd
from pprint import pprint
from module_rsi import rsi_calc

#바이낸스 객체 생성
load_dotenv() 
api_key = os.environ['BINANCE_API_KEY']
api_secret = os.environ['BINANCE_API_SECRET']
exchange = ccxt.binance(config = {
    'apiKey' : api_key,
    'secret' : api_secret,
    'enableRateLimit' : True,
    'options' : {
        'defaultType' : 'future'
    }
})

symbol = 'CHR/USDT'
timeframe = '1m'

def main() :
    while True:
        #USDT Avbl balance
        balance = exchange.fetch_balance()
        avbl = balance['USDT']['free']
        positions = exchange.fetch_positions(symbols=[symbol])
        entryPrice = positions[0]['entryPrice']
        positionAmt = positions[0]['contracts']
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=150)
        df = pd.DataFrame(ohlcv)
        rsi = rsi_calc(df,14)
        print('close : ',ohlcv[-1][4])
        print('entryPrice : ',entryPrice)
        print('positionAmt : ',positionAmt)
        print('avbl : ',avbl)
        # pprint(rsi)
        print(rsi.iloc[-1])
        time.sleep(3)

if __name__ == "__main__":
    main()