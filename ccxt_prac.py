import ccxt
import os
from pprint import pprint
from dotenv import load_dotenv
load_dotenv()  # read file from local .env

api_key = os.environ['BINANCE_API_KEY']
api_secret = os.environ['BINANCE_API_SECRET']
symbol = 'BTCDOM/USDT'

exchange = ccxt.binance(config = {
    'apiKey' : api_key,
    'secret' : api_secret,
    'enableRateLimit' : True,
    'options' : {
        'defaultType' : 'future'
    }
})

#usdt markets
# markets = exchange.load_markets()
# usdt_markets = {}
# for symbol, market in markets.items():
#     if 'USDT' in symbol:
#         usdt_markets[symbol] = market
# print( len(usdt_markets) )

#tickers
# tickers = exchange.fetch_tickers()
# pprint(tickers)


# ticker = exchange.fetch_ticker(symbol)

# # 특정 티커 정보 출력
# pprint(ticker)

# balance = exchange.fetch_balance(params={"type":"future"})
# # print(balance)

# assets_with_positions = [
#     asset for asset in balance['info']['assets']
#     if float(asset['walletBalance']) != 0 or float(asset['unrealizedProfit']) != 0 or float(asset['marginBalance']) != 0
# ]

# # 포지션 보유 자산 출력
# pprint(assets_with_positions)

# 포지션 정보 
positions = exchange.fetch_positions(symbols=[symbol])
pprint(positions)


# orders = [None] * 3
# price = 2410

# # limit price
# orders[0] = exchange.create_order(
#     symbol="BTCDOM/USDT",
#     type="LIMIT",
#     side="buy",
#     amount=0.001,
#     price=price
# )

# # take profit
# orders[1] = exchange.create_order(
#     symbol="BTCDOM/USDT",
#     type="TAKE_PROFIT",
#     side="sell",
#     amount=0.001,
#     price=price,
#     params={'stopPrice': 19600}
# )

# # stop loss
# orders[2] = exchange.create_order(
#     symbol="BTCDOM/USDT",
#     type="STOP",
#     side="sell",
#     amount=0.001,
#     price=price,
#     params={'stopPrice': 19200}
# )

# for order in orders:
#     pprint(order)

# symbol = "BTCDOM/USDT"
# open_orders = exchange.fetch_open_orders(symbol)

# 열려 있는 모든 주문 출력
# for order in open_orders:
#     pprint(order)

# resp = exchange.cancel_all_orders(symbol=symbol)
# pprint(resp)    