import os
import ccxt.pro as ccxt  # 비동기 지원 버전 사용
import pandas as pd
import logging
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from pytz import timezone
from module_rsi import calc_rsi
from module_stochrsi import calc_stoch_rsi
from module_ema import calc_ema

# 로깅 설정
def timetz(*args):
    return datetime.now(tz).timetuple()

tz = timezone('Asia/Seoul')
logging.Formatter.converter = timetz
logging.basicConfig(
    filename='stoch_rsi_cross_async.log',
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
    position_status = "none"  # 현재 포지션 상태 (none, long, full_long, short, full_short)
    symbol = 'CRV/USDC:USDC'
    timeframe = '1h'
    interval = 40   # interval 초마다 반복
    leverage = 10
    df = None
    entry_price = 0
    position_amount = 0
    avbl_25per = 0.25
    
    # 거래소 초기화
    await exchange.load_markets()
    await exchange.cancel_all_orders(symbol=symbol)
    await exchange.set_leverage(leverage, symbol)
    await exchange.set_margin_mode('isolated', symbol)
    price_precision = exchange.markets[symbol]['precision']['price']
    amount_precision = exchange.markets[symbol]['precision']['amount']
    logging.info("***************  Stoch RSI Cross Strategy has started!! ***************")

    while True:
        try:
            balance = await exchange.fetch_balance()
            avbl = balance['USDC']['free']
            
            # OHLCV 데이터 가져오기
            ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=200)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # 지표 계산
            rsi = calc_rsi(df, 14)
            stoch_rsi_df = calc_stoch_rsi(df['close'])
            K = round(stoch_rsi_df['K'].iloc[-2], 2)
            D = round(stoch_rsi_df['D'].iloc[-2], 2)
            current_price = df['close'].iloc[-1]

            # 포지션 정보 가져오기
            positions = await exchange.fetch_positions(symbols=[symbol])
            if positions :
                entry_price = positions[0]['entryPrice']
                position_amount = positions[0]['contracts']

            # 전략 로직
            if position_status == "none":
                if K < 20 and D < 20 and K > D and rsi <= 45 :
                    # 롱 진입 (잔고의 30%)
                    amount = (avbl * avbl_25per * leverage) / current_price
                    await exchange.create_market_buy_order(symbol, amount)
                    position_status = "long_1"
                    logging.info(f"Entered LONG_1 position with 30% at price: {current_price}")
                elif K > 80 and D > 80 and K < D and rsi >= 55 :
                    # 숏 진입 (잔고의 30%)
                    amount = (avbl * avbl_25per * leverage) / current_price
                    await exchange.create_market_sell_order(symbol, amount)
                    position_status = "short_1"
                    logging.info(f"Entered SHORT_1 position with 30% at price: {current_price}")

            elif position_status == "long_1":
                if K > 80 and D > 80 and K < D :
                    # 롱 포지션 청산 및 숏 전환
                    await exchange.create_market_sell_order(symbol, position_amount)
                    await asyncio.sleep(2)
                    amount = (avbl * avbl_25per * leverage) / current_price
                    await exchange.create_market_sell_order(symbol, amount)
                    position_status = "short_1"
                    logging.info(f"Switched from LONG_1 to SHORT_1 at price: {current_price}")
                elif current_price <= entry_price * 0.97:
                    # 2차 롱 진입 (잔고 전량)
                    amount = (avbl * leverage) / current_price
                    await exchange.create_market_buy_order(symbol, amount)
                    position_status = "long_2"
                    logging.info(f"Added to LONG_1 and switched to LONG_2 position at price: {current_price}")

            elif position_status == "short_1":
                if K < 20 and D < 20 and K > D:
                    # 숏 포지션 청산 및 롱 전환
                    await exchange.create_market_buy_order(symbol, position_amount)
                    await asyncio.sleep(2)
                    amount = (avbl * avbl_25per * leverage) / current_price
                    await exchange.create_market_buy_order(symbol, amount)
                    position_status = "long_1"
                    logging.info(f"Switched from SHORT to FULL LONG at price: {current_price}")
                elif current_price >= entry_price * 1.03:
                    # 2차 숏 진입 (잔고 전량)
                    amount = (avbl * leverage) / current_price
                    await exchange.create_market_sell_order(symbol, amount)
                    position_status = "full_short"
                    logging.info(f"Added to SHORT position (full) at price: {current_price}")

            # elif position_status in ["full_long", "full_short"]:
            #     if position_status == "full_long" and current_price <= entry_price * 0.98:
            #         if rsi <= 20 and K < 20 and D < 20:
            #             # 롱 포지션 청산 후 숏 전환 (잔고의 30%)
            #             await exchange.create_market_sell_order(symbol, position_amount)
            #             amount = (avbl * 0.3 * leverage) / current_price
            #             await exchange.create_market_sell_order(symbol, amount)
            #             position_status = "short"
            #             logging.info(f"Closed FULL LONG and entered SHORT with 30% at price: {current_price}")
            #         else:
            #             # 롱 포지션 청산
            #             await exchange.create_market_sell_order(symbol, position_amount)
            #             position_status = "none"
            #             logging.info(f"Closed FULL LONG position at price: {current_price}")

            #     elif position_status == "full_short" and current_price >= entry_price * 1.02:
            #         if rsi >= 80 and K > 80 and D > 80:
            #             # 숏 포지션 청산 후 롱 전환 (잔고의 30%)
            #             await exchange.create_market_buy_order(symbol, position_amount)
            #             amount = (avbl * 0.3 * leverage) / current_price
            #             await exchange.create_market_buy_order(symbol, amount)
            #             position_status = "long"
            #             logging.info(f"Closed FULL SHORT and entered LONG with 30% at price: {current_price}")
            #         else:
            #             # 숏 포지션 청산
            #             await exchange.create_market_buy_order(symbol, position_amount)
            #             position_status = "none"
            #             logging.info(f"Closed FULL SHORT position at price: {current_price}")


        except Exception as e:
            logging.error(f"An error occurred: {str(e)}")

        # 잠시 대기
        await asyncio.sleep(interval)

if __name__ == "__main__":
    asyncio.run(main())
