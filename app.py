import os
import time
from dotenv import load_dotenv
import ccxt
import pandas as pd
import logging
import comm
from pprint import pprint
from module_rsi import calculate_rsi
from module_ema import calculate_ema

logging.basicConfig(filename='bot.log', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')


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
symbol = 'CHR/USDT:USDT'

#가격 소숫점 자릿수 제한 설정
exchange.load_markets()
price_precision = exchange.markets[symbol]['precision']['price']
amount_precision = exchange.markets[symbol]['precision']['amount']
# min_cost = exchange.markets[symbol]['limits']['cost']['min']
pending_buy_order_id = None
pending_tp_order_id = None
interval = 15   # interval 초마다 반복
leverage = 10
init_delay_count = 0
buy_count = 0
timeframe = '5m'

#코드 시작하면 먼저 주문 모두 취소
exchange.cancel_all_orders(symbol=symbol)
#해당 타겟의 레버리지 설정
exchange.set_leverage(leverage, symbol)
#해당 타겟 모드 격리로 설정
exchange.set_margin_mode('isolated', symbol)

def main() :

    global buy_count, price_precision, amount_precision, pending_buy_order_id, pending_tp_order_id, init_delay_count

    while True:
        try:
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
            # ema_99 = calculate_ema(df['close'],window=99)
            # ema_150 = calculate_ema(df['close'],window=150)
            # highest_last_150 = df['high'].rolling(window=150).max().iloc[-1]
            highest_last_40 = df['high'].rolling(window=40).max().iloc[-1]

            if init_delay_count > 8 :
                exchange.cancel_all_orders(symbol=symbol)
                comm.clear_pending()
                init_delay_count = 0
                continue

            #take profit 체결체크
            if pending_tp_order_id != None:  
                
                order = comm.fetch_order_with_retry(exchange, pending_tp_order_id, symbol, interval) 
                if order == None : continue

                if order['status'] == 'closed':
                        buy_count = 0
                        exchange.cancel_all_orders(symbol=symbol)
                        comm.clear_pending()

            #buy_order 체결되었는지 체크
            if pending_buy_order_id != None: 
                try:

                    order = comm.fetch_order_with_retry(exchange, pending_buy_order_id, symbol, interval)

                    try:
                        order = exchange.fetch_order(pending_buy_order_id, symbol)
                    except:
                        logging.error(f"Error checking buy order {pending_buy_order_id}: {e}")
                        time.sleep(interval)
                        continue

                    if buy_count == 0 and order['status'] == 'open' :
                        init_delay_count += 1
                        time.sleep(interval)
                        continue
                    
                    #첫 매수 체결
                    if buy_count == 0 and order['status'] == 'closed' :
                        #이후 체결 로그 남기기
                        targetBuyPrice = round(entryPrice*0.985/price_precision)*price_precision
                        new_order = exchange.create_order( symbol = symbol, type = "LIMIT", side = "buy",
                                                       amount = comm.calculate_amount(avbl, 0.15, leverage, targetBuyPrice, amount_precision),
                                                        price = targetBuyPrice )
                        init_delay_count = 0
                        buy_count += 1
                        pending_buy_order_id = new_order['id']

                    #두번째 매수 체결
                    elif buy_count == 1 and order['status'] == 'closed':
                        exchange.cancel_all_orders(symbol=symbol)

                        targetBuyPrice = round(entryPrice*0.97/price_precision)*price_precision
                        
                        new_order = exchange.create_order( symbol = symbol, type = "LIMIT", side = "buy",
                                                       amount = comm.calculate_amount(avbl, 0.6, leverage, targetBuyPrice, amount_precision),
                                                        price = targetBuyPrice )
                        
                        new_tp_order = exchange.create_order( symbol = symbol, type = "TAKE_PROFIT", side = "sell", amount = positionAmt,
                                        price = round(entryPrice*1.01/price_precision)*price_precision ,
                                        params = {'stopPrice': round(entryPrice*1.005/price_precision)*price_precision } )

                        buy_count += 1
                        pending_buy_order_id = new_order['id']
                        pending_tp_order_id = new_tp_order['id']

                    #세번째 매수 체결
                    elif buy_count == 2 and order['status'] == 'closed':
                        exchange.cancel_all_orders(symbol=symbol)

                        new_tp_order = exchange.create_order( symbol = symbol, type = "TAKE_PROFIT", side = "sell", amount = positionAmt,
                                        price = round(entryPrice*1.008/price_precision)*price_precision ,
                                        params = {'stopPrice': round(entryPrice*1.004/price_precision)*price_precision} )
                        
                        sl_order = exchange.create_order( symbol = symbol, type = "STOP", side = "sell", amount = positionAmt,
                                        price = round(entryPrice*0.973/price_precision)*price_precision ,
                                        params = {'stopPrice': round(entryPrice*0.99/price_precision)*price_precision} ) 

                        buy_count += 1
                        pending_buy_order_id = sl_order['id']
                        pending_tp_order_id = new_tp_order['id']

                    elif buy_count == 3 and order['status'] == 'closed' :
                        buy_count = 0
                        exchange.cancel_all_orders(symbol=symbol)
                        comm.clear_pending()
                        
                except Exception as e:
                    print(f"Error creating additional order : {e}")

            # #조건판별 후 buy
            init_cond = ( buy_count == 0 and entryPrice == None and pending_buy_order_id == None and
                        pending_buy_order_id == None and highest_last_40*0.985 >= currClose and rsi <= 36  ) 
                 
            # 최초 매수     
            if init_cond : 
                try:
                    targetBuyPrice = currClose - 1*price_precision
                    adjusted_amount = comm.calculate_amount(avbl, percent = 0.05, leverage = leverage, targetBuyPrice = targetBuyPrice, amount_precision = amount_precision)
                    tp_price = round(targetBuyPrice*1.01/price_precision)*price_precision
                    tp_stopPrice = round(targetBuyPrice*1.003/price_precision)*price_precision

                    exchange.cancel_all_orders(symbol=symbol)

                    # buy order
                    buy_order = comm.create_limit_order(exchange, symbol, "buy", adjusted_amount, tp_price, tp_stopPrice)
                    if buy_order == None : continue

                    # tp order
                    tp_order = comm.create_take_profit_order(exchange, symbol, "sell", adjusted_amount, tp_price, tp_stopPrice)
                    if tp_order == None : 
                        exchange.cancel_order(buy_order['id'], symbol)
                        continue
                    
                    pending_buy_order_id = buy_order['id']
                    pending_tp_order_id = tp_order['id']
                except:
                    logging.error(f"Error creating init order: {e}")
            time.sleep(interval)
        except Exception as e:
            print(f"error occurered: {e}")
            time.sleep(interval)
        


if __name__ == "__main__":
    main()