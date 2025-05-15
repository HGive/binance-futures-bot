import os
import ccxt.pro as ccxt
import pandas as pd
import asyncio
import logging
import random
from dotenv import load_dotenv
from datetime import datetime
from pytz import timezone
from math import ceil

# ----- 설정 -----
SYMBOLS = [
    "CHR/USDT:USDT", "SOL/USDT:USDT", "AVAX/USDT:USDT", "NEAR/USDT:USDT", "INJ/USDT:USDT",
    "AR/USDT:USDT", "LINA/USDT:USDT", "WAVES/USDT:USDT", "MATIC/USDT:USDT", "GALA/USDT:USDT",
    "MASK/USDT:USDT", "STMX/USDT:USDT", "SUSHI/USDT:USDT", "MANA/USDT:USDT", "BLZ/USDT:USDT",
    "XEM/USDT:USDT", "DASH/USDT:USDT", "MTL/USDT:USDT", "RLC/USDT:USDT", "BAND/USDT:USDT"
]
LEVERAGE = 3
INTERVAL = 1800  # 30분
BUY_PROBABILITY = 0.02
TP_RATE = 1.03
BUY_PRICE_DISCOUNT = 0.995

# ----- 로깅 설정 -----
tz = timezone("Asia/Seoul")
logging.Formatter.converter = lambda *args: datetime.now(tz).timetuple()
logging.basicConfig(
    filename="random_tp_strategy.log",
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ----- API 설정 -----
load_dotenv()
api_key = os.environ["BINANCE_API_KEY"]
api_secret = os.environ["BINANCE_API_SECRET"]

exchange = ccxt.binance({
    "apiKey": api_key,
    "secret": api_secret,
    "enableRateLimit": True,
    "options": {
        "defaultType": "future",
        "adjustForTimeDifference": True
    }
})


async def main():

    logging.info("===== Random tp Started =====")
    await exchange.load_markets()

    # 레버리지 및 마진 설정
    for symbol in SYMBOLS:
        await exchange.set_leverage(LEVERAGE, symbol)
        await exchange.set_margin_mode("isolated", symbol)

    while True:
        try:
            
            # 잔고 조회
            balance = await exchange.fetch_balance()
            total_usdt = balance["total"]["USDT"]
            buy_unit = 2 * ceil(total_usdt / 100)

            # 보유 중인 포지션 확인
            all_positions = await exchange.fetch_positions()
            open_symbols = [pos["symbol"] for pos in all_positions if float(pos["contracts"]) > 0]

            for symbol in SYMBOLS:
                market = exchange.market(symbol)
                base = market['base']
                if market['id'] in open_symbols:
                    continue  # 이미 포지션 보유 중이면 스킵

                # 주문 초기화
                await exchange.cancel_all_orders(symbol=symbol)

                # 매수 확률 체크
                if random.random() < BUY_PROBABILITY:
                    # 현재가 조회
                    ticker = await exchange.fetch_ticker(symbol)
                    current_price = ticker["last"]
                    buy_price = round(current_price * BUY_PRICE_DISCOUNT, 6)

                    # 수량 계산
                    amount = round((buy_unit * LEVERAGE) / buy_price, 4)
                    min_amount = market["limits"]["amount"]["min"]
                    if amount < min_amount:
                        logging.warning(f"Skip {symbol}: amount {amount} below min {min_amount}")
                        continue

                    # 지정가 매수
                    order = await exchange.create_limit_buy_order(symbol, amount, buy_price)
                    logging.info(f"[BUY] {symbol} @ {buy_price} / Amount: {amount}")

                    # TP 주문 (시장가 익절)
                    tp_price = round(buy_price * TP_RATE, 6)
                    tp_order = await exchange.create_order(
                        symbol=symbol,
                        type="TAKE_PROFIT_MARKET",
                        side="sell",
                        amount=amount,
                        price=None,
                        params={"stopPrice": tp_price}
                    )
                    logging.info(f"[TP]  {symbol} @ {tp_price} (market TP)")

            await asyncio.sleep(INTERVAL)

        except Exception as e:
            logging.error(f"Error: {type(e).__name__} - {e}")
            await asyncio.sleep(60)  # 에러시 1분 대기 후 재시도


if __name__ == "__main__":
    asyncio.run(main())
