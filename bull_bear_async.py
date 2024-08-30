import os
import ccxt.pro as ccxt
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
    filename='bull_bear_async.log',
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

# 메인 함수
async def main():
    # 전역 변수들
    symbol = 'CHR/USDT:USDT'
    timeframe = '15m'
    interval = 15   # interval 초마다 반복
    leverage = 10
    df = None
    entry_price = 0
    position_amount = 0
    pending_order_id = None
    pending_tp_order_id = None
    pending_sl_order_id = None

    # 거래소 초기화
    await exchange.load_markets()
    await exchange.cancel_all_orders(symbol=symbol)
    await exchange.set_leverage(leverage, symbol)
    await exchange.set_margin_mode('isolated', symbol)

    price_precision = exchange.markets[symbol]['precision']['price']
    amount_precision = exchange.markets[symbol]['precision']['amount']
    logging.info("*************** New Trading Strategy has started! ***************")

    while True:
        try:
            balance = await exchange.fetch_balance()
            avbl = balance['USDT']['free']
            
            # OHLCV 데이터 가져오기
            ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=200)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # EMA 계산
            df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
            df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            
            # 현재가 및 지표 계산
            current_price = df['close'].iloc[-1]
            rsi = calc_rsi(df, 14)
            highest_last_30 = df['high'].rolling(window=30).max().iloc[-1]
            lowest_last_30 = df['low'].rolling(window=30).min().iloc[-1]

            # 시장 상태 판단
            is_bull = (df['ema10'].iloc[-1] > df['ema50'].iloc[-1]) and (df['ema20'].iloc[-1] > df['ema50'].iloc[-1])
            is_bear = (df['ema10'].iloc[-1] < df['ema50'].iloc[-1]) and (df['ema20'].iloc[-1] < df['ema50'].iloc[-1])

            # 포지션 정보 가져오기
            positions = await exchange.fetch_positions(symbols=[symbol])
            if positions:
                entry_price = positions[0]['entryPrice']
                position_amount = positions[0]['contracts']
                position_side = positions[0]['side']

            # 주문 상태 확인 및 처리
            if pending_order_id:
                order = await exchange.fetch_order(pending_order_id, symbol)
                if order['status'] == 'closed':
                    logging.info(f"Order executed: {order['side']} {order['amount']} at {order['price']}")
                    pending_order_id = None

            if pending_tp_order_id:
                tp_order = await exchange.fetch_order(pending_tp_order_id, symbol)
                if tp_order['status'] == 'closed':
                    logging.info(f"Take Profit executed at {tp_order['price']}")
                    await exchange.cancel_all_orders(symbol=symbol)
                    pending_order_id = pending_tp_order_id = pending_sl_order_id = None

            if pending_sl_order_id:
                sl_order = await exchange.fetch_order(pending_sl_order_id, symbol)
                if sl_order['status'] == 'closed':
                    logging.info(f"Stop Loss executed at {sl_order['price']}")
                    await exchange.cancel_all_orders(symbol=symbol)
                    pending_order_id = pending_tp_order_id = pending_sl_order_id = None

            # 새로운 주문 로직
            if not pending_order_id and not position_amount:
                if is_bull and current_price <= highest_last_30 * 0.99:
                    # Bull 상태에서 롱 진입
                    amount = avbl * leverage / current_price
                    order = await exchange.create_market_buy_order(symbol, amount)
                    tp_price = current_price * 1.03
                    sl_price = current_price * 0.985
                    tp_order = await exchange.create_limit_sell_order(symbol, amount, tp_price)
                    sl_order = await exchange.create_stop_market_sell_order(symbol, amount, sl_price)
                    pending_order_id = order['id']
                    pending_tp_order_id = tp_order['id']
                    pending_sl_order_id = sl_order['id']
                    logging.info(f"Entered LONG position: {amount} at {current_price}")
                elif is_bear and current_price >= lowest_last_30 * 1.01:
                    # Bear 상태에서 숏 진입
                    amount = avbl * leverage / current_price
                    order = await exchange.create_market_sell_order(symbol, amount)
                    tp_price = current_price * 0.97
                    sl_price = current_price * 1.015
                    tp_order = await exchange.create_limit_buy_order(symbol, amount, tp_price)
                    sl_order = await exchange.create_stop_market_buy_order(symbol, amount, sl_price)
                    pending_order_id = order['id']
                    pending_tp_order_id = tp_order['id']
                    pending_sl_order_id = sl_order['id']
                    logging.info(f"Entered SHORT position: {amount} at {current_price}")
                elif rsi < 35:
                    # RSI 35 미만에서 롱 진입
                    amount = avbl * leverage / current_price
                    order = await exchange.create_market_buy_order(symbol, amount)
                    tp_price = current_price * 1.02
                    sl_price = current_price * 0.99
                    tp_order = await exchange.create_limit_sell_order(symbol, amount, tp_price)
                    sl_order = await exchange.create_stop_market_sell_order(symbol, amount, sl_price)
                    pending_order_id = order['id']
                    pending_tp_order_id = tp_order['id']
                    pending_sl_order_id = sl_order['id']
                    logging.info(f"Entered LONG position (RSI): {amount} at {current_price}")
                elif rsi > 65:
                    # RSI 65 초과에서 숏 진입
                    amount = avbl * leverage / current_price
                    order = await exchange.create_market_sell_order(symbol, amount)
                    tp_price = current_price * 0.98
                    sl_price = current_price * 1.01
                    tp_order = await exchange.create_limit_buy_order(symbol, amount, tp_price)
                    sl_order = await exchange.create_stop_market_buy_order(symbol, amount, sl_price)
                    pending_order_id = order['id']
                    pending_tp_order_id = tp_order['id']
                    pending_sl_order_id = sl_order['id']
                    logging.info(f"Entered SHORT position (RSI): {amount} at {current_price}")

            logging.info(f"Current price: {current_price}, RSI: {rsi}, Bull: {is_bull}, Bear: {is_bear}")
            await asyncio.sleep(interval)

        except Exception as e:
            logging.error(f"An error occurred: {e}")
            await asyncio.sleep(interval)

if __name__ == "__main__":
    asyncio.run(main())