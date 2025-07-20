import os
import ccxt.pro as ccxt  # 비동기 지원 버전 사용
import pandas as pd
import logging
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from pytz import timezone
from modules.module_rsi import calc_rsi


# 로깅 설정
def timetz(*args):
    return datetime.now(tz).timetuple()


tz = timezone("Asia/Seoul")
logging.Formatter.converter = timetz
logging.basicConfig(
    filename="averaging_down_async.log",
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 바이낸스 객체 생성
load_dotenv()
api_key = os.environ["BINANCE_API_KEY"]
api_secret = os.environ["BINANCE_API_SECRET"]
exchange = ccxt.binance(
    {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future", "adjustForTimeDifference": True},
    }
)


# 메인 함수
async def main():

    # 전역 변수들
    symbol = "CHR/USDT:USDT"
    timeframe = "15m"
    interval = 30  # interval 초마다 반복
    leverage = 5
    df = None

    buy_count = 0
    entry_price = 0
    position_amount = 0
    pending_tp_order_id = None
    pending_sl_order_id = None

    # 거래소 초기화
    await exchange.load_markets()
    await exchange.cancel_all_orders(symbol=symbol)
    await exchange.set_leverage(leverage, symbol)
    await exchange.set_margin_mode("isolated", symbol)

    logging.info("***************  RSI Averaging down ***************")

    while True:
        try:
            balance = await exchange.fetch_balance()
            avbl = balance["USDT"]["free"]

            # OHLCV 데이터 가져오기
            ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=200)
            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

            # 지표 계산
            rsi = calc_rsi(df, 14)
            current_price = df["close"].iloc[-1]

            # 포지션 정보 가져오기
            positions = await exchange.fetch_positions(symbols=[symbol])

            # take profit 체결체크
            if pending_tp_order_id :
                pending_tp_order = await exchange.fetch_order(
                    pending_tp_order_id, symbol
                )

                if pending_tp_order["status"] == "closed" :
                    await exchange.cancel_all_orders(symbol=symbol)
                    buy_count = 0
                    entry_price = 0
                    pending_tp_order_id, pending_sl_order_id = None, None
                    logging.info("---------------   take profit !! ---------------")
                    continue

            # stop loss 체결체크
            if pending_sl_order_id :
                pending_sl_order = await exchange.fetch_order(
                    pending_sl_order_id, symbol
                )

                if pending_sl_order["status"] == "closed":
                    await exchange.cancel_all_orders(symbol=symbol)
                    buy_count = 0
                    entry_price = 0
                    pending_tp_order_id, pending_sl_order_id = None, None
                    logging.info("---------------   stop loss !! ---------------")
                    continue

            # 포지션
            if positions:
                entry_price = positions[0]["entryPrice"]
                position_amount = positions[0]["contracts"]

                # 두번째 매수
                if buy_count == 2 and not pending_tp_order_id:
                    await exchange.cancel_all_orders(symbol=symbol)

                    tp_price = 1.03 * entry_price
                    sl_price = 0.97 * current_price
                    tp_order = await exchange.create_order(
                        symbol,
                        "TAKE_PROFIT_MARKET",
                        "sell",
                        position_amount,
                        None,
                        params={"stopPrice": tp_price},
                    )
                    # sl order
                    sl_order = await exchange.create_order(
                        symbol,
                        "STOP_LOSS_MARKET",
                        "sell",
                        position_amount,
                        None,
                        params={"stopPrice": sl_price},
                    )
                    pending_tp_order_id = tp_order["id"]
                    pending_sl_order_id = sl_order["id"]

            # buy 로직
            if buy_count == 0 and not entry_price and rsi < 30 :
                adjusted_amount = avbl * 0.2 * leverage / current_price
                tp_price = 1.03 * current_price
                
                await exchange.cancel_all_orders(symbol=symbol)

                # buy order
                buy_order = await exchange.create_order(
                    symbol, "market", "buy", adjusted_amount, current_price
                )
                # tp order
                tp_order = await exchange.create_order(
                    symbol,
                    "TAKE_PROFIT_MARKET",
                    "sell",
                    adjusted_amount,
                    None,
                    params={"stopPrice": tp_price},
                )

                pending_tp_order_id = tp_order["id"]
                buy_count = 1

            elif buy_count == 1 and entry_price and (rsi < 20 or ( rsi < 25 and entry_price * 0.93 >= current_price )) :
                await exchange.cancel_all_orders(symbol=symbol)

                adjusted_amount = avbl * 1 * leverage / current_price
                buy_order = await exchange.create_order(
                    symbol, "market", "buy", adjusted_amount, current_price
                )
                pending_tp_order_id = None
                buy_count = 2
                # print("----------loop----------")

            await asyncio.sleep(interval)
        except Exception as e:
            logging.error(f"An error occurred:{type(e).__name__}: {e}")
            await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
