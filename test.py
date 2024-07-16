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
symbol = 'BTCDOM/USDT:USDT'

#가격 소숫점 자릿수 제한 설정
exchange.load_markets()
price_precision = exchange.markets[symbol]['precision']['price']
amount_precision = exchange.markets[symbol]['precision']['amount']
min_cost = exchange.markets[symbol]['limits']['cost']['min']

timeframe = '5m'
buy_count = 0

def main() :

    global buy_count
    global price_precision
    global amount_precision
    global min_cost

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

            # #조건판별 후 buy
            init_cond = buy_count == 0 and entryPrice == None and highest_last_150*0.95 >= currClose and highest_last_35*0.975 >= currClose and rsi < 35
            
            # if init_cond:
            #      buy_count += 1
            #      print('init buy, buy_count : ', buy_count) 

            # if buy_count == 1  and entryPrice != None :
                 
            if buy_count == 0 and entryPrice == None : 
                targetBuyPrice = currClose - 2*price_precision
                avbl_1pcnt = avbl*0.01 # 주문할 양 잔고의 1%
                avbl_1pcnt_x10 = avbl_1pcnt*10
                amount = avbl_1pcnt_x10 / targetBuyPrice
                buy_count += 1

                print('avbl_1pcnt', avbl_1pcnt)
                print('targetBuyPrice', targetBuyPrice)
                print('amount : ', amount)
                print('amount_precision : ', amount_precision)
                print('adjusted amount : ', round(amount/amount_precision)*amount_precision)
                print('min_cost : ', min_cost)

                 
            open_orders = exchange.fetch_open_orders(symbol)
            pprint(len(open_orders))
            
            # pprint(exchange.markets[symbol])
            



        # except Exception as e:
        #     print(f"error occurered: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()