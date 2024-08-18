import ccxt
import os
import comm
import time
from datetime import datetime
from pprint import pprint
from dotenv import load_dotenv
load_dotenv()  # read file from local .env

api_key = os.environ['BINANCE_API_KEY']
api_secret = os.environ['BINANCE_API_SECRET']
# symbol = 'CRV/USDT:USDT'

exchange = ccxt.binance(config = {
    'apiKey' : api_key,
    'secret' : api_secret,
    'enableRateLimit' : True,
    'options' : {
        'defaultType' : 'future',
        'adjustForTimeDifference': True
    }
})


# # 바이낸스 서버 시간 가져오기
# server_time = exchange.fetch_time()

# # 서버 시간을 날짜 형식으로 변환
# server_datetime = datetime.utcfromtimestamp(server_time / 1000)

# # 로컬 현재 시간 가져오기
# local_datetime = datetime.now()

# # 출력
# print(f"Server Time (UTC): {server_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
# print(f"Local Time: {local_datetime.strftime('%Y-%m-%d %H:%M:%S')}")

# 심볼 정보 로드
symbol = 'CRV/USDT:USDT'
exchange.load_markets()

# 심볼의 정보 가져오기
market_info = exchange.markets[symbol]

# price_precision 확인
price_precision = market_info['precision']['price']
print(f"Price Precision for {symbol}: {price_precision}")

def calc_price(percent, price, price_precision) :
    return round(price*percent/price_precision)*price_precision
print(0.1772*1.005)
print(calc_price(1.005, 0.1772, price_precision))

