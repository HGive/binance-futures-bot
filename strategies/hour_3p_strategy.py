# strategies/hour_3p_strategy.py
import pandas as pd
import logging
import random
import math
from modules.module_rsi import calc_rsi

class Hour3PStrategy:
    def __init__(self, exchange, symbol, leverage=3, timeframe="1h"):
        self.exchange = exchange
        self.symbol = symbol
        self.leverage = leverage
        self.timeframe = timeframe
        self.pending_tp_order_id = None

    async def setup(self):
        await self.exchange.cancel_all_orders(symbol=self.symbol)
        await self.exchange.set_leverage(self.leverage, self.symbol)
        await self.exchange.set_margin_mode("isolated", self.symbol)

    async def run_once(self):
        try:
            logging.info(f"symbol: {self.symbol}. run_once start")
            balance = await self.exchange.fetch_balance()
            total_balance = balance["USDT"]["total"]
            avbl = balance["USDT"]["free"]
            buy_unit = self.calc_buy_unit(total_balance)
            if avbl < buy_unit:
                logging.info(f"not enough minerals.")
                return

            ohlcv = await self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=200)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            rsi = calc_rsi(df, 14)
            current_price = df["close"].iloc[-1]
            positions = await self.exchange.fetch_positions(symbols=[self.symbol])

            # take profit check
            if self.pending_tp_order_id:
                pending_tp_order = await self.exchange.fetch_order(
                    self.pending_tp_order_id, self.symbol
                )
                if pending_tp_order["status"] == "closed":
                    await self.exchange.cancel_all_orders(symbol=self.symbol)
                    self.pending_tp_order_id = None
                    logging.info(f"[{self.symbol}] >>> TAKE PROFIT!")
                    return

            if positions and len(positions) > 0 and positions[0]["contracts"] > 0:
                print(positions)
                return

            # 롱/숏 진입 조건 설정
            should_long = False
            should_short = False
            
            # 롱 진입 조건 (RSI 낮을 때)
            if rsi < 30:
                should_long = random.random() < 0.9
            elif rsi < 40:
                should_long = random.random() < 0.3
            
            # 숏 진입 조건 (RSI 높을 때)
            if rsi > 80:
                should_short = random.random() < 0.9
            elif rsi > 75:
                should_short = random.random() < 0.3

            # 롱 포지션 진입
            if should_long:
                adjusted_amount = buy_unit * self.leverage / current_price
                tp_price = current_price * 1.03
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                try:
                    buy_order = await self.exchange.create_order(self.symbol, "market", "buy", adjusted_amount, current_price)
                    if buy_order['status'] != 'open' and buy_order['status'] != 'closed':
                        logging.error(f"[{self.symbol}] buy 주문 실패 - 상태: {buy_order['status']}")
                        return
                    logging.info(f"[{self.symbol}] buy 주문 성공")
                except Exception as e:
                    logging.error(f"[{self.symbol}] buy 주문 에러: {type(e).__name__}: {e}")
                    return
                try:
                    tp_order = await self.exchange.create_order(
                        self.symbol, "TAKE_PROFIT_MARKET", "sell",
                        adjusted_amount, None, params={"stopPrice": tp_price}
                    )
                    if tp_order['status'] != 'open' and tp_order['status'] != 'closed':
                        logging.error(f"[{self.symbol}] sell TP 주문 실패 - 상태: {tp_order['status']}")
                        return
                    logging.info(f"[{self.symbol}] sell TP 주문 성공")
                    self.pending_tp_order_id = tp_order["id"]
                except Exception as e:
                    logging.error(f"[{self.symbol}] sell TP 주문 에러: {type(e).__name__}: {e}")
                    return

            # 숏 포지션 진입
            elif should_short:
                adjusted_amount = buy_unit * self.leverage / current_price
                tp_price = current_price * 0.97
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                try:
                    sell_order = await self.exchange.create_order(self.symbol, "market", "sell", adjusted_amount, current_price)
                    if sell_order['status'] != 'open' and sell_order['status'] != 'closed':
                        logging.error(f"[{self.symbol}] sell 주문 실패 - 상태: {sell_order['status']}")
                        return
                    logging.info(f"[{self.symbol}] sell 주문 성공")
                except Exception as e:
                    logging.error(f"[{self.symbol}] sell 주문 에러: {type(e).__name__}: {e}")
                    return
                try:
                    tp_order = await self.exchange.create_order(
                        self.symbol, "TAKE_PROFIT_MARKET", "buy",
                        adjusted_amount, None, params={"stopPrice": tp_price}
                    )
                    if tp_order['status'] != 'open' and tp_order['status'] != 'closed':
                        logging.error(f"[{self.symbol}] buy TP 주문 실패 - 상태: {tp_order['status']}")
                        return
                    logging.info(f"[{self.symbol}] buy TP 주문 성공")
                    self.pending_tp_order_id = tp_order["id"]
                except Exception as e:
                    logging.error(f"[{self.symbol}] buy TP 주문 에러: {type(e).__name__}: {e}")
                    return

        except Exception as e:
            await self.exchange.cancel_all_orders(symbol=self.symbol)
            logging.error(f"[{self.symbol}] Error: {type(e).__name__}: {e}")


    def calc_buy_unit(self, total_balance):
        base_amount = total_balance / 10
        buy_unit = math.floor(base_amount / 5) * 5
        return buy_unit