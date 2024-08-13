import os
from dotenv import load_dotenv
import ccxt
import pandas as pd
import numpy as np
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
    filename='bot.log',
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
# logging.info('Timezone: ' + str(tz))


#바이낸스 객체 생성
load_dotenv() 
api_key = os.environ['BINANCE_API_KEY']
api_secret = os.environ['BINANCE_API_SECRET']
print(api_key)
print(api_secret)
exchange = ccxt.binance(config = {
    'apiKey' : api_key,
    'secret' : api_secret,
    'enableRateLimit' : True,
    'options' : {
        'defaultType' : 'future',
        'adjustForTimeDifference': True
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
timeframe = '1m'

#코드 시작하면 먼저 주문 모두 취소
exchange.cancel_all_orders(symbol=symbol)
#해당 타겟의 레버리지 설정
exchange.set_leverage(leverage, symbol)
#해당 타겟 모드 격리로 설정
exchange.set_margin_mode('isolated', symbol)

logging.info("***************   Bot has started!! ***************")

balance = None
avbl = None
positions = None
positionAmt = None
ohlcv = None
currClose = None
df = None
rsi = None
highest_last_40 = None
init_delay_count = 0
is_bull = False

def main() :

    global buy_count, price_precision, amount_precision, pending_buy_order_id, pending_tp_order_id, init_delay_count, balance, avbl, positions, positionAmt, ohlcv, currClose, df, rsi, highest_last_40, init_delay_count, is_bull

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
            df['ma99'] = df['close'].rolling(window = 99).mean()
            last_70 = df.tail(70)
            above_ma99_cnt = np.sum(last_70['close'] > last_70['ma99'] )
            is_bull = above_ma99_cnt > 61
            rsi = calc_rsi(df,14)
            middle_value = (df['high'] + df['close']) / 2
            highest_last_40 = middle_value.rolling(window=40).max().iloc[-1]

            if init_delay_count > 4 :
                exchange.cancel_all_orders(symbol=symbol)
                pending_buy_order_id, pending_tp_order_id = None, None
                init_delay_count = 0
                continue

            #take profit 체결체크
            if pending_tp_order_id != None:  
                
                pending_tp_order = exchange.fetch_order(pending_tp_order_id, symbol)

                if pending_tp_order['status'] == 'closed':
                    logging.info("---------------   take profit !! ---------------")
                    buy_count = 0
                    exchange.cancel_all_orders(symbol=symbol)
                    pending_buy_order_id, pending_tp_order_id = None, None

            #buy_order 체결되었는지 체크
            if pending_buy_order_id != None: 
                try:
                    
                    pending_buy_order = exchange.fetch_order(pending_buy_order_id, symbol)

                    if buy_count == 0 and pending_buy_order['status'] == 'open' :
                        init_delay_count += 1
                        logging.info(f'----- init_delay_count : {init_delay_count} -----')
                        time.sleep(interval)
                        continue
                    
                    #첫 매수 체결
                    if buy_count == 0 and pending_buy_order['status'] == 'closed' :
                        #이후 체결 로그 남기기
                        targetBuyPrice = comm.calc_price(0.99,entryPrice,price_precision)
                        adjusted_amount = comm.calc_amount(avbl, 0.06, leverage, targetBuyPrice, amount_precision)

                        buy_order = comm.custom_limit_order(exchange, symbol, "buy", adjusted_amount, targetBuyPrice)
                        if buy_order == None : 
                            time.sleep(interval)
                            continue

                        init_delay_count = 0
                        buy_count += 1
                        pending_buy_order_id = buy_order['id']

                    #두번째 매수 체결
                    elif buy_count == 1 and pending_buy_order['status'] == 'closed':
                        exchange.cancel_all_orders(symbol=symbol)

                        targetBuyPrice = comm.calc_price(0.96,entryPrice,price_precision)
                        adjusted_amount = comm.calc_amount(avbl, 0.25, leverage, targetBuyPrice, amount_precision)
                        tp_price = comm.calc_price(1.005,entryPrice,price_precision)
                        # tp_stopPrice = comm.calc_price(1.01,entryPrice,price_precision)

                        buy_order = comm.custom_limit_order(exchange, symbol, "buy", adjusted_amount, targetBuyPrice)
                        if buy_order == None : 
                            time.sleep(interval)
                            continue
                        
                        tp_order = comm.custom_tpsl_order(exchange, symbol, "TAKE_PROFIT", "sell", positionAmt, tp_price, tp_price)
                        if tp_order == None : 
                            exchange.cancel_order(buy_order['id'], symbol)
                            time.sleep(interval)
                            continue

                        buy_count += 1
                        pending_buy_order_id = buy_order['id']
                        pending_tp_order_id = tp_order['id']

                    #세번째 매수 체결
                    elif buy_count == 2 and pending_buy_order['status'] == 'closed':
                        exchange.cancel_all_orders(symbol=symbol)

                        targetBuyPrice = comm.calc_price(0.94,entryPrice,price_precision)
                        adjusted_amount = comm.calc_amount(avbl, 0.9, leverage, targetBuyPrice, amount_precision)
                        tp_price = comm.calc_price(1.005,entryPrice,price_precision)
                        # tp_stopPrice = comm.calc_price(1.01,entryPrice,price_precision)

                        buy_order = comm.custom_limit_order(exchange, symbol, "buy", adjusted_amount, targetBuyPrice)
                        if buy_order == None : 
                            time.sleep(interval)
                            continue
                        
                        tp_order = comm.custom_tpsl_order(exchange, symbol, "TAKE_PROFIT", "sell", positionAmt, tp_price, tp_price)
                        if tp_order == None : 
                            exchange.cancel_order(buy_order['id'], symbol)
                            time.sleep(interval)
                            continue

                        buy_count += 1
                        pending_buy_order_id = buy_order['id']
                        pending_tp_order_id = tp_order['id']

                    #네번째 매수 체결
                    elif buy_count == 3 and pending_buy_order['status'] == 'closed':
                        exchange.cancel_all_orders(symbol=symbol)

                        sl_price = comm.calc_price(0.975 ,entryPrice, price_precision)
                        # sl_stopPrice = comm.calc_price(0.99 ,entryPrice, price_precision)
                        tp_price = comm.calc_price(1.004,entryPrice,price_precision)
                        # tp_stopPrice = comm.calc_price(1.004,entryPrice,price_precision)

                        sl_order = comm.custom_tpsl_order(exchange, symbol, "STOP", "sell", positionAmt, sl_price, sl_price)
                        if sl_order == None :
                            time.sleep(interval)
                            continue
                        
                        tp_order = comm.custom_tpsl_order(exchange, symbol, "TAKE_PROFIT", "sell", positionAmt, tp_price, tp_price)
                        if tp_order == None:
                            exchange.cancel_order(sl_order['id'], symbol)
                            time.sleep(interval)
                            continue
                        
                        buy_count += 1
                        pending_buy_order_id = sl_order['id']
                        pending_tp_order_id = tp_order['id']

                    elif buy_count == 4 and pending_buy_order['status'] == 'closed' :
                        logging.info("---------------   stop loss !! ---------------")
                        buy_count = 0
                        exchange.cancel_all_orders(symbol=symbol)
                        pending_buy_order_id, pending_tp_order_id = None, None
                        
                except Exception as e:
                    logging.error(f"Error creating additional order buy_cnt = {buy_count} : {e}")

            # #조건판별 후 buy
            init_cond = ( buy_count == 0 and entryPrice == None and pending_buy_order_id == None and
                        pending_tp_order_id == None ) 
            
            market_cond = (highest_last_40*0.994 >= currClose ) if is_bull else (highest_last_40*0.992 >= currClose or rsi <= 36) 
                 
            # 최초 매수     
            if init_cond and market_cond : 
                try:
                    adjusted_amount = comm.calc_amount(avbl, percent = 0.02, leverage = leverage, targetBuyPrice = currClose, amount_precision = amount_precision)
                    tp_price = comm.calc_price(1.006, currClose, price_precision)
                    # tp_stopPrice = comm.calc_price(1.005, currClose, price_precision)

                    exchange.cancel_all_orders(symbol=symbol)

                    # buy order
                    buy_order = comm.custom_limit_order(exchange, symbol, "buy", adjusted_amount, currClose)
                    if buy_order == None : 
                        time.sleep(interval)
                        continue

                    # tp order
                    tp_order = comm.custom_tpsl_order(exchange, symbol,"TAKE_PROFIT", "sell", adjusted_amount, tp_price, tp_price)
                    if tp_order == None : 
                        #원자성을 위해서 buy_order
                        exchange.cancel_order(buy_order['id'], symbol)
                        time.sleep(interval)
                        continue
                    
                    pending_buy_order_id = buy_order['id']
                    pending_tp_order_id = tp_order['id']
                except Exception as e:
                    logging.error(f"Error creating init order: {e}")

            print("init_cond : ", init_cond)
            # print("market_cond : ", market_cond)
            # print("is_bull : ", is_bull)
            # print("init_cond and market_cond: ", (init_cond and market_cond))
            time.sleep(interval)
        except Exception as e:
            logging.error(f"error occurered: {e}")
            time.sleep(interval)
        
if __name__ == "__main__":
    main()