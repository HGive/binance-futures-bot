import pandas as pd
import os
import requests
import json
import datetime
from dotenv import load_dotenv
load_dotenv()  # read file from local .env
from binance.client import Client
from pprint import pprint

url = 'https://api1.binance.com'

api_key = os.environ['BINANCE_API_KEY_TEST']
api_secret = os.environ['BINANCE_API_SECRET_TEST']
client = Client(api_key, api_secret,testnet=False)

# client = Client(api_key, api_secret, tld='us')

# tickers = client.get_all_tickers()
# df = pd.DataFrame(tickers)
# df.head()

# url = 'https://api1.binance.com'
# api_call = '/api/v3/ticker/price'
# headers = {'content-type': 'application/json','X-MBX-APIKEY': api_key}

# response = requests.get(url + api_call, headers=headers)
# response = json.loads(response.text)
# df = pd.DataFrame.from_records(response)
# print(df)

# print(client.ping())  # {} empty response means no errors
# res= client.get_server_time()
# print(res)
# ts = res['serverTime'] / 1000
# your_dt = datetime.datetime.fromtimestamp(ts)
# your_dt.strftime("%Y-%m-%d %H:%M:%S")
# print(your_dt)
# help(client.get_all_tickers)

# coin_info = client.get_all_tickers()
# df = pd.DataFrame(coin_info)
# pprint(coin_info)
# pprint(df.head())

# exchange_info = client.get_exchange_info()
# print(exchange_info.keys())   #dict_keys(['timezone', 'serverTime', 'rateLimits', 'exchangeFilters', 'symbols'])
# df = pd.DataFrame(exchange_info['symbols'])
# print(df)
# symbol_info = client.get_symbol_info('BTCUSDT')
# pprint(symbol_info)

# market_depth = client.get_order_book(symbol='BTCUSDT')
# bids = pd.DataFrame(market_depth['bids'])
# bids.columns = ['price','bids']
# asks = pd.DataFrame(market_depth['asks'])
# asks.columns = ['price','asks']
# # pprint(bids)
# # pprint(asks)
# df = pd.concat([bids,asks]).fillna('-')
# print(df)

# recent_trades = client.get_recent_trades(symbol='BTCUSDT')
# df = pd.DataFrame(recent_trades)
# # print(df)
# # help(client.get_historical_trades)
# id = df.loc[450,'id']
# print('id:' ,id)
# historical_trades = client.get_historical_trades(symbol='BTCUSDT', limit=1000, fromId=id)
# df = pd.DataFrame(historical_trades)
# print(df)

# avg_price = client.get_avg_price(symbol="BTCUSDT")
# print(avg_price)

# tickers = client.get_ticker()
# df = pd.DataFrame(tickers)
# print(df)

info = client.futures_account(symbol='NTRNUSDT')
pprint(info)

# asset_balance = client.futures_position_information(symbol='NTRNUSDT')
# trades = client.futures_account_trades()
# df = pd.DataFrame(trades)
# df['time'] = pd.to_datetime(df['time'],unit='ms').dt.strftime('%Y-%m-%d %H:%M:%S')
# df=df.rename(columns={'time':'시간'})
# print(df)
