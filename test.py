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
pending_buy_order_ids = []
pending_tp_order_ids = []

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
            highest_last_40 = df['high'].rolling(window=40).max().iloc[-1]

            #buy_order 체결되었는지 체크
            for order_id in pending_buy_order_ids[:]:  # 리스트를 복사하여 반복
                try:
                    order = exchange.fetch_order(order_id, symbol)
                    if order['status'] == 'closed':
                        print(f"Order {order_id} has been filled.")
                        #이후 체결 로그 남기기
                        buy_count += 1
                        pending_buy_order_ids.remove(order_id)
                    elif order['status'] == 'canceled':
                        print(f"Order {order_id} has been canceled.")
                        pending_buy_order_ids.remove(order_id)
                except Exception as e:
                    print(f"Error checking order {order_id}: {e}")

            # #조건판별 후 buy
            init_cond = ( buy_count == 0 and entryPrice == None and highest_last_150*0.97 >= currClose and 
                         highest_last_40*0.985 >= currClose and rsi < 35 and currClose < ema_99 and len(pending_order_ids) == 0 ) 
            second_cond = ( buy_count == 1 and entryPrice != None and currClose <= entryPrice*0.96 )
            third_cond = ()
                 
            # 최초 매수     
            if init_cond : 
                targetBuyPrice = currClose - 1*price_precision
                adjusted_amount = calculate_amount(avbl, percent = 0.01, leverage= 10, targetBuyPrice = targetBuyPrice, amount_precision = amount_precision)

                exchange.cancel_all_orders(symbol=symbol)
                 
                order = exchange.create_order( symbol = symbol, type = "LIMIT", side = "buy", amount = adjusted_amount, price = targetBuyPrice )
                pending_buy_order_ids.append(order['id'])

                #  # take profit
                exchange.create_order( symbol = symbol, type = "TAKE_PROFIT", side = "sell", amount = adjusted_amount,
                                        price = targetBuyPrice*1.03, params = {'stopPrice': targetBuyPrice*1.02} )
                #  stop loss 
                #  exchange.create_order( symbol = symbol, type = "STOP", side = "sell", amount = 0.001, price = None, params={'stopPrice': 19200} )
                

            # 첫 번째 물타기
            if second_cond :
                exchange.cancel_all_orders(symbol=symbol)
                exchange.create_order( symbol = symbol, type = "LIMIT", side = "buy", amount = adjusted_amount, price = targetBuyPrice )
                
                 
            # open_orders = exchange.fetch_open_orders(symbol)
            # pprint(open_orders)
            
            # # pprint(exchange.markets[symbol])
            # print('pending_order_ids : ', pending_buy_order_ids)

            



        # except Exception as e:
        #     print(f"error occurered: {e}")
            time.sleep(3)

def calculate_amount(avbl,percent, leverage, targetBuyPrice, amount_precision):
    avbl_pcnt = avbl*percent # 주문할 양 잔고의 percent%
    avbl_pcnt_xlev = avbl_pcnt*leverage
    amount = avbl_pcnt_xlev / targetBuyPrice
    return round(amount/amount_precision)*amount_precision
    
        
# avbl_1pcnt = avbl*0.01 # 주문할 양 잔고의 1%
#                 avbl_1pcnt_x10 = avbl_1pcnt*10
#                 amount = avbl_1pcnt_x10 / targetBuyPrice
#                 adjusted_amount = round(amount/amount_precision)*amount_precision

if __name__ == "__main__":
    main()