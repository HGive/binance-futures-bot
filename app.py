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

api_key = os.environ['BINANCE_API_KEY']
api_secret = os.environ['BINANCE_API_SECRET']
client = Client(api_key, api_secret, testnet=True)

# client = Client(api_key, api_secret, tld='us')

tickers = client.get_all_tickers()
df = pd.DataFrame(tickers)
df.head()

url = 'https://api1.binance.com'
api_call = '/api/v3/ticker/price'
headers = {'content-type': 'application/json','X-MBX-APIKEY': api_key}

response = requests.get(url + api_call, headers=headers)
response = json.loads(response.text)
df = pd.DataFrame.from_records(response)
print(df)

# print(client.ping())  # {} empty response means no errors
res= client.get_server_time()
# print(res)
ts = res['serverTime'] / 1000
your_dt = datetime.datetime.fromtimestamp(ts)
your_dt.strftime("%Y-%m-%d %H:%M:%S")
# print(your_dt)
# help(client.get_all_tickers)