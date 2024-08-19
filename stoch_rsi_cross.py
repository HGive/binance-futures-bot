import os
from dotenv import load_dotenv
import ccxt
import pandas as pd
import numpy as np
import logging
from pytz import timezone
from datetime import datetime
import time
from module_stochrsi import calc_stoch_rsi
from module_rsi import calc_rsi
import comm

# 로깅 설정
def timetz(*args):
    return datetime.now(tz).timetuple()
tz = timezone('Asia/Seoul') 
logging.Formatter.converter = timetz
logging.basicConfig(
    filename='stoch_rsi_cross.log',
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

# 타겟 심볼
symbol = 'CHR/USDT:USDT'

# 가격 소숫점 자릿수 제한 설정
exchange.load_markets()
price_precision = exchange.markets[symbol]['precision']['price']
amount_precision = exchange.markets[symbol]['precision']['amount']

# 주문 관리 변수 초기화
pending_buy_order_id = None
pending_tp_order_id = None
stop_loss_activated = False
interval = 15   # interval 초마다 반복
leverage = 10
timeframe = '1m'

# 레버리지 및 마진 모드 설정
exchange.cancel_all_orders(symbol=symbol)
exchange.set_leverage(leverage, symbol)
exchange.set_margin_mode('isolated', symbol)

logging.info("***************   Bot has started!! ***************")

def main():
    global pending_buy_order_id, pending_tp_order_id, stop_loss_activated

    while True:
        try:
            balance = exchange.fetch_balance()
            avbl = balance['USDT']['free']
            positions = exchange.fetch_positions(symbols=[symbol])
            entryPrice = positions[0]['entryPrice'] if len(positions) > 0 else None
            positionAmt = positions[0]['contracts'] if len(positions) > 0 else None  
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=200)
            currClose = ohlcv[-1][4]
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # Stochastic RSI 계산
            df['stoch_k'], df['stoch_d'] = calc_stoch_rsi(df, 14, 3, 3)
            rsi = calc_rsi(df, 14)

            last_k = df['stoch_k'].iloc[-1]
            last_d = df['stoch_d'].iloc[-1]
            
            # 롱 포지션 진입 조건
            if pending_buy_order_id is None and pending_tp_order_id is None and entryPrice is None:
                if last_k < 20 and last_d < 20 and last_k > last_d:
                    adjusted_amount = comm.calc_amount(avbl, percent=0.3, leverage=leverage, targetBuyPrice=currClose, amount_precision=amount_precision)
                    buy_order = comm.custom_limit_order(exchange, symbol, "buy", adjusted_amount, currClose)
                    if buy_order:
                        pending_buy_order_id = buy_order['id']
            
            # 물타기 조건
            elif entryPrice and (currClose / entryPrice) < 0.98 and not stop_loss_activated:
                adjusted_amount = comm.calc_amount(avbl, percent=1.0, leverage=leverage, targetBuyPrice=currClose, amount_precision=amount_precision)
                buy_order = comm.custom_limit_order(exchange, symbol, "buy", adjusted_amount, currClose)
                if buy_order:
                    pending_buy_order_id = buy_order['id']
                    stop_loss_activated = True
            
            # 손절 조건
            elif stop_loss_activated and (currClose / entryPrice) < 0.98:
                sl_price = comm.calc_price(0.98, entryPrice, price_precision)
                sl_order = comm.custom_tpsl_order(exchange, symbol, "STOP", "sell", positionAmt, sl_price, sl_price)
                if sl_order:
                    pending_buy_order_id = sl_order['id']
                    pending_tp_order_id = None
                    stop_loss_activated = False
            
            # 숏 포지션 진입 조건
            elif stop_loss_activated and last_k < 20 and last_d < 20 and rsi < 20:
                if entryPrice is None or (currClose / entryPrice) < 1.02:
                    adjusted_amount = comm.calc_amount(avbl, percent=0.2, leverage=leverage, targetBuyPrice=currClose, amount_precision=amount_precision)
                    sell_order = comm.custom_limit_order(exchange, symbol, "sell", adjusted_amount, currClose)
                    if sell_order:
                        pending_buy_order_id = sell_order['id']
            
            # 익절 조건
            elif entryPrice and last_k > 80 and last_d > 80 and last_k < last_d:
                exchange.cancel_all_orders(symbol=symbol)
                sell_order = comm.custom_limit_order(exchange, symbol, "sell", positionAmt, currClose)
                if sell_order:
                    pending_buy_order_id = sell_order['id']
                    pending_tp_order_id = None
            
            time.sleep(interval)
        
        except Exception as e:
            logging.error(f"error occurred: {e}")
            time.sleep(interval)

if __name__ == "__main__":
    main()
