import os
import ccxt.pro as ccxt  # 비동기 지원 버전 사용
import pandas as pd
import numpy as np
import logging
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from pytz import timezone
from module_rsi import calc_rsi

# 로깅 설정
def timetz(*args):
    return datetime.now(tz).timetuple()

tz = timezone('Asia/Seoul')
logging.Formatter.converter = timetz
logging.basicConfig(
    filename='averaging_down_async.log',
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 바이낸스 객체 생성
load_dotenv()
api_key = os.environ['BINANCE_API_KEY']
api_secret = os.environ['BINANCE_API_SECRET']
exchange = ccxt.binance({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',
        'adjustForTimeDifference': True
    }
})

# # 초기 설정: 주문 취소, 레버리지 및 마진 모드 설정
# async def init_exchange(symbol,leverage):
    

# 메인 함수
async def main():

    # 전역 변수들
    symbol = 'CHR/USDT:USDT'
    timeframe = '5m'
    interval = 15   # interval 초마다 반복
    leverage = 10
    df = None
    entry_price = 0
    position_amount = 0
    pending_buy_order_id = None
    pending_tp_order_id = None
    init_delay_count = 0
    buy_count = 0
    buy_percent = [0.02, 0.06, 0.25, 1]  # 각 매수 퍼센트 (1차, 2차, 3차, 4차 매수)
    price_diffs = [1, 0.97, 0.96, 0.94]  # 각 매수 시점에서의 가격 변동 비율

    
    # 거래소 초기화
    await exchange.load_markets()
    await exchange.cancel_all_orders(symbol=symbol)
    await exchange.set_leverage(leverage, symbol)
    await exchange.set_margin_mode('isolated', symbol)

    price_precision = exchange.markets[symbol]['precision']['price']
    amount_precision = exchange.markets[symbol]['precision']['amount']
    logging.info("***************  Averaging Down sync Strategy has started!! ***************")

    while True:
        try:
            balance = await exchange.fetch_balance()
            avbl = balance['USDT']['free']
            
            # OHLCV 데이터 가져오기
            ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=200)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['ma99'] = df['close'].rolling(window = 99).mean()
            last_70 = df.tail(70)
            above_ma99_cnt = np.sum(last_70['close'] > last_70['ma99'] )
            is_bull = above_ma99_cnt > 61 
            middle_value = (df['high'] + df['close']) / 2
            highest_last_40 = middle_value.rolling(window=40).max().iloc[-1]
            
            # 지표 계산
            rsi = calc_rsi(df, 14)
            current_price = df['close'].iloc[-1]

            # 포지션 정보 가져오기
            positions = await exchange.fetch_positions(symbols=[symbol])
            if positions :
                entry_price = positions[0]['entryPrice']
                position_amount = positions[0]['contracts']

            if init_delay_count > 5 :
                await exchange.cancel_all_orders(symbol=symbol)
                pending_buy_order_id, pending_tp_order_id = None, None
                init_delay_count = 0
                continue

            #take profit 체결체크
            if pending_tp_order_id != None:        
                pending_tp_order = await exchange.fetch_order(pending_tp_order_id, symbol)

                if pending_tp_order['status'] == 'closed':
                    buy_count = 0
                    await exchange.cancel_all_orders(symbol=symbol)
                    pending_buy_order_id, pending_tp_order_id = None, None
                    logging.info("---------------   take profit !! ---------------")

            #buy_order 체결 체크
            if pending_buy_order_id != None: 
  
                pending_buy_order = await exchange.fetch_order(pending_buy_order_id, symbol)

                if buy_count == 0 and pending_buy_order['status'] == 'open' :
                    init_delay_count += 1
                    logging.info(f'----- init_delay_count : {init_delay_count} -----')
                    await asyncio.sleep(interval)
                    continue
                
                #첫 매수 체결
                if buy_count == 0 and pending_buy_order['status'] == 'closed' :
                    targetBuyPrice = price_diffs[1]*entry_price
                    adjusted_amount = avbl*buy_percent[1]*leverage/targetBuyPrice

                    buy_order = await exchange.create_order(symbol,"LIMIT","buy",adjusted_amount,targetBuyPrice)

                    init_delay_count = 0
                    buy_count += 1
                    pending_buy_order_id = buy_order['id']

                #두번째 매수 체결
                elif buy_count == 1 and pending_buy_order['status'] == 'closed':
                    await exchange.cancel_all_orders(symbol=symbol)

                    targetBuyPrice = price_diffs[2]*entry_price
                    adjusted_amount = avbl*buy_percent[2]*leverage/targetBuyPrice
                    tp_price = 1.015*entry_price

                    buy_order = await exchange.create_order(symbol,"LIMIT","buy",adjusted_amount,targetBuyPrice)
                    
                    tp_order = await exchange.create_order(symbol,"TAKE_PROFIT","sell",position_amount,tp_price, params={'stopPrice': tp_price})

                    buy_count += 1
                    pending_buy_order_id = buy_order['id']
                    pending_tp_order_id = tp_order['id']

                #세번째 매수 체결
                elif buy_count == 2 and pending_buy_order['status'] == 'closed':
                    await exchange.cancel_all_orders(symbol=symbol)

                    targetBuyPrice = price_diffs[3]*entry_price
                    adjusted_amount = avbl*buy_percent[3]*leverage/targetBuyPrice
                    tp_price = 1.005*entry_price

                    buy_order = await exchange.create_order(symbol,"LIMIT","buy",adjusted_amount,targetBuyPrice)
                    
                    tp_order = await exchange.create_order(symbol,"TAKE_PROFIT","sell",position_amount,tp_price, params={'stopPrice': tp_price})

                    buy_count += 1
                    pending_buy_order_id = buy_order['id']
                    pending_tp_order_id = tp_order['id']

                # 매수 체결
                elif buy_count == 3 and pending_buy_order['status'] == 'closed':
                    await exchange.cancel_all_orders(symbol=symbol)

                    sl_price = 0.97*entry_price
                    tp_price = 1.005*entry_price

                    sl_order = await exchange.create_order(symbol,"stop","sell",position_amount,sl_price)
                    
                    tp_order = await exchange.create_order(symbol,"TAKE_PROFIT","sell",position_amount,tp_price, params={'stopPrice': tp_price})
                    
                    buy_count += 1
                    pending_buy_order_id = sl_order['id']
                    pending_tp_order_id = tp_order['id']

                elif buy_count == 4 and pending_buy_order['status'] == 'closed' :
                    logging.info("---------------   stop loss !! ---------------")
                    buy_count = 0
                    await exchange.cancel_all_orders(symbol=symbol)
                    pending_buy_order_id, pending_tp_order_id = None, None

                # #조건판별 후 buy
                init_cond = ( buy_count == 0 and entry_price == None and pending_buy_order_id == None and
                            pending_tp_order_id == None ) 
                
                market_cond = (highest_last_40*0.99 >= current_price ) if is_bull else (highest_last_40*0.98 >= current_price or rsi <= 33)     

                if init_cond and market_cond :
                    
                    adjusted_amount = avbl*0.04*leverage/current_price if is_bull else avbl*buy_percent[0]*leverage/current_price
                    tp_price = 1.02*current_price if is_bull else 1.01*current_price

                    await exchange.cancel_all_orders(symbol=symbol)

                    # buy order
                    buy_order = await exchange.create_order(symbol,"LIMIT","buy",adjusted_amount,current_price)
                    logging.info("")

                    # tp order
                    tp_order = await exchange.create_order(symbol,"TAKE_PROFIT","sell",adjusted_amount,tp_price, params={'stopPrice': tp_price})
                    
                    pending_buy_order_id = buy_order['id']
                    pending_tp_order_id = tp_order['id']

                print("----------loop----------")
                await asyncio.sleep(interval)        
        except Exception as e:
                logging.error(f"An error occurred: {e}")
                await asyncio.sleep(interval)

        

if __name__ == "__main__":
    asyncio.run(main())
