import os
from dotenv import load_dotenv
import ccxt
import pandas as pd
import logging
from pytz import timezone
from datetime import datetime
import time
import comm
from module_rsi import calc_rsi
# from module_ema import calc_ema

#로깅 설정
def timetz(*args):
    return datetime.now(tz).timetuple()
tz = timezone('Asia/Seoul') # UTC, Asia/Shanghai, Europe/Berlin
logging.Formatter.converter = timetz
logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
# logging.info('Timezone: ' + str(tz))


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
interval = 20   # interval 초마다 반복
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
            rsi = calc_rsi(df,14)
            # ema_99 = calc_ema(df['close'],window=99)
            # ema_150 = calc_ema(df['close'],window=150)
            # highest_last_150 = df['high'].rolling(window=150).max().iloc[-1]
            highest_last_40 = df['high'].rolling(window=40).max().iloc[-1]

            if init_delay_count > 8 :
                exchange.cancel_all_orders(symbol=symbol)
                comm.clear_pending()
                init_delay_count = 0
                continue

            #take profit 체결체크
            if pending_tp_order_id != None:  
                
                order = order = exchange.fetch_order(pending_tp_order_id, symbol)

                if order['status'] == 'closed':
                    buy_count = 0
                    exchange.cancel_all_orders(symbol=symbol)
                    comm.clear_pending()

            #buy_order 체결되었는지 체크
            if pending_buy_order_id != None: 
                try:
                    
                    order = exchange.fetch_order(pending_buy_order_id, symbol)

                    if buy_count == 0 and order['status'] == 'open' :
                        init_delay_count += 1
                        time.sleep(interval)
                        continue
                    
                    #첫 매수 체결
                    if buy_count == 0 and order['status'] == 'closed' :
                        #이후 체결 로그 남기기
                        targetBuyPrice = comm.calc_price(0.985,entryPrice,price_precision)
                        adjusted_amount = comm.calc_amount(avbl, 0.15, leverage, targetBuyPrice, amount_precision)

                        new_order = comm.custom_limit_order(exchange, symbol, "buy", adjusted_amount, targetBuyPrice)
                        if new_order == None : 
                            time.sleep(interval)
                            continue

                        init_delay_count = 0
                        buy_count += 1
                        pending_buy_order_id = new_order['id']

                    #두번째 매수 체결
                    elif buy_count == 1 and order['status'] == 'closed':
                        exchange.cancel_all_orders(symbol=symbol)

                        targetBuyPrice = comm.calc_price(0.97,entryPrice,price_precision)
                        adjusted_amount = comm.calc_amount(avbl, 0.7, leverage, targetBuyPrice, amount_precision)
                        tp_price = comm.calc_price(1.08,entryPrice,price_precision)
                        tp_stopPrice = comm.calc_price(1.04,entryPrice,price_precision)

                        new_order = comm.custom_limit_order(exchange, symbol, "buy", adjusted_amount, targetBuyPrice)
                        if new_order == None : 
                            time.sleep(interval)
                            continue
                        
                        new_tp_order = comm.custom_tpsl_order(exchange, symbol, "TAKE_PROFIT", "sell", positionAmt, tp_price, tp_stopPrice)
                        if new_tp_order == None : 
                            exchange.cancel_order(new_order['id'], symbol)
                            time.sleep(interval)
                            continue

                        buy_count += 1
                        pending_buy_order_id = new_order['id']
                        pending_tp_order_id = new_tp_order['id']

                    #세번째 매수 체결
                    elif buy_count == 2 and order['status'] == 'closed':
                        exchange.cancel_all_orders(symbol=symbol)

                        sl_price = comm.calc_price(0.973 ,entryPrice, price_precision)
                        sl_stopPrice = comm.calc_price(0.99 ,entryPrice, price_precision)
                        tp_price = comm.calc_price(1.06,entryPrice,price_precision)
                        tp_stopPrice = comm.calc_price(1.02,entryPrice,price_precision)

                        sl_order = comm.custom_tpsl_order(exchange, symbol, "STOP", "sell", positionAmt, sl_price, sl_stopPrice)
                        if sl_order == None :
                            time.sleep(interval)
                            continue
                        
                        new_tp_order = comm.custom_tpsl_order(exchange, symbol, "TAKE_PROFIT", "sell", positionAmt, tp_price, tp_stopPrice)
                        if new_tp_order == None:
                            exchange.cancel_order(sl_order['id'], symbol)
                            time.sleep(interval)
                            continue
                        

                        buy_count += 1
                        pending_buy_order_id = sl_order['id']
                        pending_tp_order_id = new_tp_order['id']

                    elif buy_count == 3 and order['status'] == 'closed' :
                        buy_count = 0
                        exchange.cancel_all_orders(symbol=symbol)
                        comm.clear_pending()
                        
                except Exception as e:
                    logging.error(f"Error creating additional order : {e}")

            # #조건판별 후 buy
            init_cond = ( buy_count == 0 and entryPrice == None and pending_buy_order_id == None and
                        pending_buy_order_id == None and highest_last_40*0.988 >= currClose and rsi <= 40  ) 
                 
            # 최초 매수     
            if init_cond : 
                try:
                    targetBuyPrice = currClose - 1*price_precision
                    adjusted_amount = comm.calc_amount(avbl, percent = 0.05, leverage = leverage, targetBuyPrice = targetBuyPrice, amount_precision = amount_precision)
                    tp_price = comm.calc_price(1.08, targetBuyPrice, price_precision)
                    tp_stopPrice = comm.calc_price(1.004, targetBuyPrice, price_precision)

                    exchange.cancel_all_orders(symbol=symbol)

                    # buy order
                    buy_order = comm.custom_limit_order(exchange, symbol, "buy", adjusted_amount, tp_price, tp_stopPrice)
                    if buy_order == None : 
                        time.sleep(interval)
                        continue

                    # tp order
                    tp_order = comm.custom_tpsl_order(exchange, symbol,"TAKE_PROFIT", "sell", adjusted_amount, tp_price, tp_stopPrice)
                    if tp_order == None : 
                        #원자성을 위해서 buy_order
                        exchange.cancel_order(buy_order['id'], symbol)
                        time.sleep(interval)
                        continue
                    
                    pending_buy_order_id = buy_order['id']
                    pending_tp_order_id = tp_order['id']
                except Exception as e:
                    logging.error(f"Error creating init order: {e}")
            time.sleep(interval)
        except Exception as e:
            logging.error(f"error occurered: {e}")
            time.sleep(interval)
        


if __name__ == "__main__":
    main()