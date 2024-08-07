import ccxt
import os
import comm
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
        'defaultType' : 'future'
    }
})

#타겟 심볼
symbol = 'CHR/USDT:USDT'

#가격 소숫점 자릿수 제한 설정
exchange.load_markets()
price_precision = exchange.markets[symbol]['precision']['price']
amount_precision = exchange.markets[symbol]['precision']['amount']
ohlcv = exchange.fetch_ohlcv(symbol, "5m", limit=200)
currClose = ohlcv[-1][4]

#USDT Avbl balance
balance = exchange.fetch_balance()
avbl = balance['USDT']['free']

positions = exchange.fetch_positions(symbols=[symbol])
entryPrice = positions[0]['entryPrice'] if len(positions) > 0 else None
positionAmt = positions[0]['contracts'] if len(positions) > 0 else None 

targetBuyPrice = currClose - 1*price_precision
adjusted_amount = comm.calculate_amount(avbl, percent = 0.05, leverage = 10, targetBuyPrice = targetBuyPrice, amount_precision = amount_precision)

new_tp_order = exchange.create_order( symbol = symbol, type = "TAKE_PROFIT", side = "sell", amount = adjusted_amount,
                                        price = round(targetBuyPrice*1.01/price_precision)*price_precision ,
                                        params = {'stopPrice': round(targetBuyPrice*1.005/price_precision)*price_precision , 'reduceOnly': True} )

print("price_precision : ", price_precision)
print("amount_precision : ", amount_precision)
print("currClose : ", currClose)
print("targetBuyPrice : ", targetBuyPrice)
print("adjusted_amount : ", adjusted_amount)
print("buy : ", adjusted_amount * targetBuyPrice)
print("order : ", new_tp_order)



# markets = exchange.load_markets()
# tickers = exchange.fetch_tickers()
# symbols = tickers.keys()
# usdt_symbols = [x for x in symbols if x.endswith('USDT')]
# print(usdt_symbols)
# print(len(usdt_symbols))
# price_precision = exchange.markets[symbol]['precision']['price']
# pprint(exchange.markets['ZRX/USDT']['precision']['price'])
# print(exchange.markets[symbol]['precision'])
# print(price_precision)
# tao_symbols = [sym for sym in exchange.markets.keys() if sym.startswith('TAO')]

# for symbol in tao_symbols:
#     market = exchange.markets[symbol]
#     print(f"\nSymbol: {symbol}")
#     print(f"Type: {market['type']}")
#     print(f"Base/Quote: {market['base']}/{market['quote']}")
#     print(f"Active: {market['active']}")
#     print(f"Precision: {market['precision']}")
#     print(f"Limits: {market['limits']}")

# markets = exchange.load_markets()
# usdt_pairs = [symbol.replace('/','') for symbol in markets if symbol.endswith('/USDT')]
# print(usdt_pairs)

# 4. USDT 마켓 필터링
# def set_leverage(symbol, leverage):
#     try:
#         # 바이낸스에서 레버리지 설정 엔드포인트 호출
#         exchange.fapiPrivatePostLeverage({
#             'symbol': symbol,
#             'leverage': leverage
#         })
#         print(f"레버리지 {leverage}x가 {symbol}에 설정되었습니다.")
#     except ccxt.BaseError as e:
#         print(f"레버리지 설정 중 오류 발생: {str(e)}")

# 전체 USDT 거래 쌍에 대해 레버리지 설정 (예: BTC/USDT, ETH/USDT 등)

# leverage = 10

# tickers = exchange.fetch_tickers()
# symbols = [tickers[symbol]['info']['symbol'] for symbol in tickers]
# for pair in symbols:
#     set_leverage(pair, leverage)
# set_leverage('SAGAUSDT', 15)

# tickers = exchange.fetch_tickers()
# symbols = [tickers[symbol]['info']['symbol'] for symbol in tickers]

# # 결과 출력
# print(symbols)


#소숫점 자릿수 제한
# exchange.load_markets()
# print(exchange.markets['CHR/USDT']['precision'])
# print(exchange.markets['CHR/USDT']['limits'])

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
# print(balance)

# assets_with_positions = [
#     asset for asset in balance['info']['assets']
#     if float(asset['walletBalance']) != 0 or float(asset['unrealizedProfit']) != 0 or float(asset['marginBalance']) != 0
# ]

# # 포지션 보유 자산 출력
# pprint(assets_with_positions)

# balance = exchange.fetch_balance()
# usdt_balance = balance['USDT']
# pprint(usdt_balance)

# 포지션 정보 
# positions = exchange.fetch_positions(symbols=['TAO/USDT'])
# pprint(positions)


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


# usdt_symbols = [symbol for symbol in exchange.symbols if symbol.endswith('/USDT')]
# pprint(usdt_symbols)
# markets = exchange.load_markets()
# usdt_symbols = [symbol for symbol in markets if symbol.endswith('/USDT')]

# responses = []
# for symbol in usdt_symbols:
#     try:
#         response = exchange.set_margin_mode(marginMode='isolated', symbol=symbol)
#         responses.append(response)
#     except Exception as e:
#         print(f"Error changing margin mode for {symbol}: {e}")

# # 결과 확인
# pprint(responses)

# pprint(exchange.set_margin_mode(marginMode='isolated', symbol='MYROUSDT'))

