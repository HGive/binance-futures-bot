import ccxt.pro as ccxtpro
import os
import asyncio
import time
from pprint import pprint
from dotenv import load_dotenv
load_dotenv()  # read file from local .env

api_key = os.environ['BINANCE_API_KEY']
api_secret = os.environ['BINANCE_API_SECRET']

symbol = 'BTCDOM/USDT'
ohlcv = []

async def fetch_ohlcv(exchange):
    global ohlcv
    while True:
        try:
            ohlcv_data = await exchange.watchOHLCV(symbol="BTC/USDT", timeframe='5m')
            if ohlcv_data:
                ohlcv = ohlcv_data[0]
            print('ohlcv : ',ohlcv)
        except Exception as e:
            print(f"Error fetching OHLCV: {e}")
        await asyncio.sleep(3)
        
async def fetch_balance(exchange):
    global ohlcv
    while True:
        balance = await exchange.watchBalance()
        print(balance)
        print(balance['USDT'])
        # print(exchange.connected)
        await asyncio.sleep(1)

async def fetch_positions(exchange):
    global ohlcv
    while True:
        balance = await exchange.watchPositions()
        print(balance)
        print(balance['USDT'])
        # print(exchange.connected)
        # await asyncio.sleep(1)        

async def main():
    exchange = ccxtpro.binance(config = {
    'apiKey' : api_key,
    'secret' : api_secret,
    'enableRateLimit' : True,
    'options' : {
        'defaultType' : 'future'
    }})

    await asyncio.gather(
        fetch_ohlcv(exchange),
        fetch_balance(exchange),
    )

asyncio.run(main())

