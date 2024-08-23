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
    filename='multi_factor_trade.log',
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
            df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
            df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            
            # 지표 계산
            stoch_rsi_df = calc_stoch_rsi(df['close'])
            rsi = calc_rsi(df, 14)
            K = round(stoch_rsi_df['K'].iloc[-1], 2)
            D = round(stoch_rsi_df['D'].iloc[-1], 2)

            current_price = df['close'].iloc[-1]

            # 포지션 정보 가져오기
            positions = await exchange.fetch_positions(symbols=[symbol])
            if positions :
                entry_price = positions[0]['entryPrice']
                position_amount = positions[0]['contracts']


        except Exception as e:
            logging.error(f"An error occurred: {str(e)}")

        # 잠시 대기
        await asyncio.sleep(interval)

if __name__ == "__main__":
    asyncio.run(main())
