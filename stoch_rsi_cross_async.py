import os
import ccxt.async_support as ccxt  # 비동기 지원 버전 사용
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
        'defaultType': 'future'
    }
})

async def setup_exchange():
    await exchange.load_markets()
setup_exchange()

# 전역 변수들
symbol = 'CRV/USDC:USDC'
timeframe = '30m'
interval = 15   # interval 초마다 반복
leverage = 10
price_precision = exchange.markets[symbol]['precision']['price']
amount_precision = exchange.markets[symbol]['precision']['amount']


# 초기 설정: 주문 취소, 레버리지 및 마진 모드 설정
async def init_exchange():
    await exchange.cancel_all_orders(symbol=symbol)
    await exchange.set_leverage(leverage, symbol)
    await exchange.set_margin_mode('isolated', symbol)
    logging.info("***************  Stoch RSI Cross Strategy has started!! ***************")
init_exchange()

# OHLCV 데이터 가져오기
async def get_ohlcv(symbol, timeframe='1m', limit=100):
    return await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

# 메인 함수
async def main():
    position = None  # 현재 포지션 상태

    # 거래소 초기화
    await init_exchange()

    while True:
        # OHLCV 데이터 가져오기
        ohlcv = await get_ohlcv(symbol, timeframe)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        # Stochastic RSI 계산
        stoch_rsi_df = calc_stoch_rsi(df['close'])

        # D, K 값 가져오기
        K = stoch_rsi_df['K'].iloc[-1]
        D = stoch_rsi_df['D'].iloc[-1]

        # 롱 포지션 조건: D, K가 20 아래에서 K가 D를 상향 돌파
        if K > D and K < 20 and D < 20:
            if position != "long":
                # 모든 포지션 정리하고 롱 진입
                await exchange.cancel_all_orders(symbol=symbol)
                await exchange.create_market_buy_order(symbol, amount)
                position = "long"
                logging.info(f"Entered LONG position at price: {df['close'].iloc[-1]}")

        # 숏 포지션 조건: D, K가 80 위에서 K가 D를 하향 돌파
        elif K < D and K > 80 and D > 80:
            if position != "short":
                # 모든 포지션 정리하고 숏 진입
                await exchange.cancel_all_orders(symbol=symbol)
                await exchange.create_market_sell_order(symbol, amount)
                position = "short"
                logging.info(f"Entered SHORT position at price: {df['close'].iloc[-1]}")

        # 잠시 대기
        await asyncio.sleep(interval)

if __name__ == "__main__":
    asyncio.run(main())
