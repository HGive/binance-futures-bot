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
pending_order_ids = []

timeframe = '5m'
buy_count = 0

def main() :

    global buy_count, price_precision, amount_precision, min_cost, pending_order_ids

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

            # check_order_status()

            # #조건판별 후 buy
            init_cond = buy_count == 0 and entryPrice == None and highest_last_150*0.95 >= currClose and highest_last_35*0.980 >= currClose and rsi < 35
                 
            # 최초 매수     
            if buy_count == 0 : 
                targetBuyPrice = currClose - 2*price_precision
                avbl_1pcnt = avbl*0.01 # 주문할 양 잔고의 1%
                avbl_1pcnt_x10 = avbl_1pcnt*10
                amount = avbl_1pcnt_x10 / targetBuyPrice
                adjusted_amount = round(amount/amount_precision)*amount_precision

                exchange.cancel_all_orders(symbol=symbol)
                 
                order = exchange.create_order( symbol = symbol, type = "LIMIT", side = "buy", amount = adjusted_amount, price = targetBuyPrice )
                pending_order_ids.append(order['id'])

                #  # take profit
                exchange.create_order( symbol = symbol, type = "TAKE_PROFIT", side = "sell", amount = adjusted_amount,
                                        price = targetBuyPrice*1.03, params = {'stopPrice': targetBuyPrice*1.02} )
                #  stop loss 
                #  exchange.create_order( symbol = symbol, type = "STOP", side = "sell", amount = 0.001, price = None, params={'stopPrice': 19200} )

                buy_count += 1

                print('avbl_1pcnt', avbl_1pcnt)
                print('targetBuyPrice', targetBuyPrice)
                print('amount : ', amount)
                print('amount_precision : ', amount_precision)
                print('adjusted amount : ', round(amount/amount_precision)*amount_precision)
                print('min_cost : ', min_cost)
                print('pending_order_ids : ', pending_order_ids)
                

            # 첫 번째 물타기
            if buy_count == 1 and entryPrice != None and currClose <= entryPrice*0.95 and rsi < 35:
                exchange.cancel_all_orders(symbol=symbol)
                exchange.create_order( symbol = symbol, type = "LIMIT", side = "buy", amount = adjusted_amount, price = targetBuyPrice )
                
                 
            open_orders = exchange.fetch_open_orders(symbol)
            pprint(len(open_orders))
            
            # pprint(exchange.markets[symbol])
            



        # except Exception as e:
        #     print(f"error occurered: {e}")
            time.sleep(3)

def check_order_status():
    global buy_count, pending_order_ids
    
    for order_id in pending_order_ids[:]:  # 리스트를 복사하여 반복
        try:
            order = exchange.fetch_order(order_id, symbol)
            if order['status'] == 'closed':
                print(f"Order {order_id} has been filled.")
                buy_count += 1
                pending_order_ids.remove(order_id)
            elif order['status'] == 'canceled':
                print(f"Order {order_id} has been canceled.")
                pending_order_ids.remove(order_id)
        except Exception as e:
            print(f"Error checking order {order_id}: {e}")

        

if __name__ == "__main__":
    main()