import ccxt
import ccxt.pro as ccxtpro
import os
import asyncio
from pprint import pprint
from dotenv import load_dotenv
load_dotenv()  # read file from local .env

api_key = os.environ['BINANCE_API_KEY']
api_secret = os.environ['BINANCE_API_SECRET']
symbol = 'BTCDOM/USDT'

exchange = ccxtpro.binance(config = {
    'apiKey' : api_key,
    'secret' : api_secret,
    'enableRateLimit' : True,
    'options' : {
        'defaultType' : 'future'
    }
})

async def main():

    while True:
        # ticker = await exchange.watch_ticker(symbol="BTC/USDT")
        # pprint(ticker)
        ohlcv = await exchange.watch_ohlcv(symbol="BTC/USDT", timeframe='5m')
        pprint(ohlcv)

asyncio.run(main())
