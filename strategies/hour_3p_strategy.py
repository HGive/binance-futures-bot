# strategies/hour_3p_strategy.py
import pandas as pd
import logging
import random
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
            balance = await self.exchange.fetch_balance()
            # print(balance)
            avbl = balance["USDT"]["free"]

            ohlcv = await self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=200)
            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

            rsi = calc_rsi(df, 14)
            logging.info(f"symbol: {self.symbol}, rsi: {rsi}. run_once start")
            print(f"symbol: {self.symbol}, rsi: {rsi}. run_once start")
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
                adjusted_amount = avbl * 0.1 * self.leverage / current_price
                tp_price = current_price * 1.03  # 3% 익절
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                
                buy_order = await self.exchange.create_order(
                    self.symbol, "market", "buy", adjusted_amount, current_price
                )
                
                tp_order = await self.exchange.create_order(
                    self.symbol, "TAKE_PROFIT_MARKET", "sell",
                    adjusted_amount, None, params={"stopPrice": tp_price}
                )
                
                self.pending_tp_order_id = tp_order["id"]
                logging.info(f"[{self.symbol}] 롱 매수 완료 - RSI: {rsi:.2f}, 가격: {current_price}, 수량: {adjusted_amount}")

            # 숏 포지션 진입
            elif should_short:
                adjusted_amount = avbl * 0.1 * self.leverage / current_price
                tp_price = current_price * 0.97  # 3% 익절 (가격이 3% 하락)
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                
                sell_order = await self.exchange.create_order(
                    self.symbol, "market", "sell", adjusted_amount, current_price
                )
                
                tp_order = await self.exchange.create_order(
                    self.symbol, "TAKE_PROFIT_MARKET", "buy",
                    adjusted_amount, None, params={"stopPrice": tp_price}
                )
                
                self.pending_tp_order_id = tp_order["id"]
                logging.info(f"[{self.symbol}] 숏 매도 완료 - RSI: {rsi:.2f}, 가격: {current_price}, 수량: {adjusted_amount}")

        except Exception as e:
            await self.exchange.cancel_all_orders(symbol=self.symbol)
            logging.error(f"[{self.symbol}] Error: {type(e).__name__}: {e}")