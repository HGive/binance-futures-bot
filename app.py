import os
from dotenv import load_dotenv
load_dotenv()  # read file from local .env
# import ccxt.pro as ccxtpro 
import ccxt
from pprint import pprint

#바이낸스 객체 생성
api_key = os.environ['BINANCE_API_KEY']
api_secret = os.environ['BINANCE_API_SECRET']
exchange = ccxt.binance(config = {
    'apiKey' : api_key,
    'secret' : api_secret,
    'enableRateLimit' : True,
    'options' : {
        'defaultType' : 'future'
    }
})

symbol = 'BTCDOM/USDT'

#USDT Avbl balance
balance = exchange.fetch_balance()
avbl = balance['USDT']['free']
pprint(avbl)