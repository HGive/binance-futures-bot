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
    position = None  # 현재 포지션 상태
    symbol = 'CRV/USDC:USDC'
    timeframe = '30m'
    interval = 5   # interval 초마다 반복
    leverage = 10
    df = None
    buy_count = 0
    # 거래소 초기화
    await exchange.load_markets()
    await exchange.cancel_all_orders(symbol=symbol)
    await exchange.set_leverage(10, symbol)
    await exchange.set_margin_mode('isolated', symbol)
    price_precision = exchange.markets[symbol]['precision']['price']
    amount_precision = exchange.markets[symbol]['precision']['amount']
    logging.info("***************  Stoch RSI Cross Strategy has started!! ***************")


    while True :

        balance = await exchange.fetch_balance()
        avbl = balance['USDC']['free']
        # OHLCV 데이터 가져오기
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=200)
        currClose = ohlcv[-1][4]
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        positions = await exchange.fetch_positions(symbols=[symbol])
        entryPrice = positions[0]['entryPrice'] if len(positions) > 0 else None
        positionAmt = positions[0]['contracts'] if len(positions) > 0 else None  
        rsi = calc_rsi(df,14)
        stoch_rsi_df = calc_stoch_rsi(df['close'])
        K = round(stoch_rsi_df['K'].iloc[-1],2)
        D = round(stoch_rsi_df['D'].iloc[-1],2)

        # # 롱 포지션 조건: D, K가 20 아래에서 K가 D를 상향 돌파
        # if buy_count == 0 and K < 20 and D < 20 and  K > D:
        #     if position != "long":
        #         # 모든 포지션 정리하고 롱 진입
        #         await exchange.cancel_all_orders(symbol=symbol)
        #         await exchange.create_market_buy_order(symbol, amount)
        #         position = "long"
        #         logging.info(f"Entered LONG position at price: {df['close'].iloc[-1]}")

        # # 숏 포지션 조건: D, K가 80 위에서 K가 D를 하향 돌파
        # elif K < D and K > 80 and D > 80:
        #     if position != "short":
        #         # 모든 포지션 정리하고 숏 진입
        #         await exchange.cancel_all_orders(symbol=symbol)
        #         await exchange.create_market_sell_order(symbol, amount)
        #         position = "short"
        #         logging.info(f"Entered SHORT position at price: {df['close'].iloc[-1]}")

        print(df)
        print("positions:", positions)
        print("entryPrice:", entryPrice)
        print("positionAmt:", positionAmt)
        print("currClose:", currClose)
        print("rsi:", rsi)
        print("avbl:", avbl)
        print("K : ", K, ", D : ", D)

        # 잠시 대기
        await asyncio.sleep(interval)

if __name__ == "__main__":
    asyncio.run(main())
