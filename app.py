import os
import time
from dotenv import load_dotenv
import ccxt
import pandas as pd
from pprint import pprint
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

#타겟 심볼
symbol = 'BTCDOM/USDT'

#가격 소숫점 자릿수 제한 설정
exchange.load_markets()
price_precision = exchange.markets[symbol]['precision']['price']

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
            currClose = ohlcv[-1][4]
            targetBuyPrice = currClose*0.997  
            takeProfitPrice1 = targetBuyPrice*1.03
            stopLossPrice = targetBuyPrice*0.98
            df = pd.DataFrame(ohlcv,columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            rsi = calculate_rsi(df,14)
            ema_99 = calculate_ema(df['close'],window=99)
            ema_150 = calculate_ema(df['close'],window=150)
            highest_last_150 = df['high'].rolling(window=150).max().iloc[-1]
            highest_last_35 = df['high'].rolling(window=35).max().iloc[-1]
            print(df)
            print('close : ', currClose)
            print('entryPrice : ', entryPrice)
            print('positionAmt : ', positionAmt)
            print('avbl : ', avbl)
            print('rsi : ', rsi.iloc[-1])
            print('ema_99 : ', ema_99.iloc[-1])
            print('ema_150 : ', ema_150.iloc[-1])
            print(buy_count)
            print('high 150 : ', highest_last_150)
            print('high 35 : ', highest_last_35)
            print('test % : ', 0.2449*0.9750)

            # #조건판별 후 buy
            # init_cond = buy_count == 0 and entryPrice == None and highest_last_150*0.95 >= currClose and highest_last_35*0.975 >= currClose and rsi < 35
            
            # if init_cond:
            #      buy_count += 1
            #      print('init buy, buy_count : ', buy_count) 

            # if buy_count == 1  and entryPrice != None :
                 
            if buy_count == 0 and entryPrice == None : 
                 
                 order_usdt = avbl*0.1 # 주문할 양 잔고의 10%
                 amount = order_usdt / targetBuyPrice

                 
                #  exchange.cancel_all_orders(symbol=symbol)
                 
                 exchange.create_order( symbol = symbol, type = "LIMIT", side = "buy", amount = amount, price = targetBuyPrice )

                 # take profit
                 exchange.create_order( symbol = symbol, type = "TAKE_PROFIT", side = "sell", amount = amount,
                                        price = targetBuyPrice*1.3, params = {'stopPrice': targetBuyPrice*1.2} )
                 # stop loss 
                #  exchange.create_order( symbol = symbol, type = "STOP", side = "sell", amount = 0.001, price = None, params={'stopPrice': 19200} )

                 buy_count += 1

                 
            open_orders = exchange.fetch_open_orders(symbol)
            pprint(len(open_orders))
            



        # except Exception as e:
        #     print(f"error occurered: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()