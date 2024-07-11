import os
import time
from dotenv import load_dotenv
import ccxt
import pandas as pd
from module_rsi import calculate_rsi
from module_ema import calculate_ema

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
timeframe = '5m'
buy_count = 0

def main() :
    global buy_count
    while True:
        # try:
            #USDT Avbl balance
            balance = exchange.fetch_balance()
            avbl = balance['USDT']['free']

            positions = exchange.fetch_positions(symbols=[symbol])
            entryPrice = positions[0]['entryPrice'] if len(positions) > 0 else None 
            positionAmt = positions[0]['contracts'] if len(positions) > 0 else None

            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=200)
            df = pd.DataFrame(ohlcv,columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            rsi = calculate_rsi(df,14)
            ema_99 = calculate_ema(df['close'],window=99)
            ema_150 = calculate_ema(df['close'],window=150)
            print(df)
            print('close : ', ohlcv[-1][4])
            print('entryPrice : ', entryPrice)
            print('positionAmt : ', positionAmt)
            print('avbl : ', avbl)
            print('rsi : ', rsi.iloc[-1])
            print('ema_99 : ', ema_99.iloc[-1])
            print('ema_150 : ', ema_150.iloc[-1])
            print(buy_count)
            #조건판별 후 buy
            # if rsi < 40 and


        # except Exception as e:
        #     print(f"error occurered: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()