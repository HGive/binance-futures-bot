# strategies/hour_3p_strategy.py

import pandas as pd
import logging
import asyncio
from modules.module_rsi import calc_rsi

class Hour3PStrategy:
    def __init__(self, exchange, symbol, leverage=3, timeframe="1h"):
        self.exchange = exchange
        self.symbol = symbol
        self.leverage = leverage
        self.timeframe = timeframe
        self.buy_count = 0
        self.entry_price = 0
        self.position_amount = 0
        self.pending_tp_order_id = None
        self.pending_sl_order_id = None

    async def setup(self):
        # 심볼별 최초 1회 실행 (main에서 루프 전 호출)
        await self.exchange.cancel_all_orders(symbol=self.symbol)
        await self.exchange.set_leverage(self.leverage, self.symbol)
        await self.exchange.set_margin_mode("isolated", self.symbol)

    async def run_once(self):
        try:
            balance = await self.exchange.fetch_balance()
            avbl = balance["USDT"]["free"]

            ohlcv = await self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=200)
            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

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
                    self.buy_count = 0
                    self.entry_price = 0
                    self.pending_tp_order_id, self.pending_sl_order_id = None, None
                    logging.info(f"[{self.symbol}] >>> TAKE PROFIT!")
                    return

            # stop loss check
            if self.pending_sl_order_id:
                pending_sl_order = await self.exchange.fetch_order(
                    self.pending_sl_order_id, self.symbol
                )
                if pending_sl_order["status"] == "closed":
                    await self.exchange.cancel_all_orders(symbol=self.symbol)
                    self.buy_count = 0
                    self.entry_price = 0
                    self.pending_tp_order_id, self.pending_sl_order_id = None, None
                    logging.info(f"[{self.symbol}] >>> STOP LOSS!")
                    return

            # 포지션 상태 동기화
            if positions and positions[0]["entryPrice"]:
                self.entry_price = positions[0]["entryPrice"]
                self.position_amount = positions[0]["contracts"]

                # 두번째 매수 진입 & 익절/손절 주문 설정
                if self.buy_count == 2 and not self.pending_tp_order_id:
                    await self.exchange.cancel_all_orders(symbol=self.symbol)
                    tp_price = 1.03 * self.entry_price
                    sl_price = 0.97 * current_price
                    tp_order = await self.exchange.create_order(
                        self.symbol, "TAKE_PROFIT_MARKET", "sell",
                        self.position_amount, None, params={"stopPrice": tp_price}
                    )
                    sl_order = await self.exchange.create_order(
                        self.symbol, "STOP_LOSS_MARKET", "sell",
                        self.position_amount, None, params={"stopPrice": sl_price}
                    )
                    self.pending_tp_order_id = tp_order["id"]
                    self.pending_sl_order_id = sl_order["id"]

            # 첫번째 매수
            if self.buy_count == 0 and not self.entry_price and rsi < 30:
                adjusted_amount = avbl * 0.2 * self.leverage / current_price
                tp_price = 1.03 * current_price
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                buy_order = await self.exchange.create_order(
                    self.symbol, "market", "buy", adjusted_amount, current_price
                )
                tp_order = await self.exchange.create_order(
                    self.symbol, "TAKE_PROFIT_MARKET", "sell",
                    adjusted_amount, None, params={"stopPrice": tp_price}
                )
                self.pending_tp_order_id = tp_order["id"]
                self.buy_count = 1

            # 두번째 매수
            elif self.buy_count == 1 and self.entry_price and (
                rsi < 20 or (rsi < 25 and self.entry_price * 0.93 >= current_price)
            ):
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                adjusted_amount = avbl * 1 * self.leverage / current_price
                buy_order = await self.exchange.create_order(
                    self.symbol, "market", "    buy", adjusted_amount, current_price
                )
                self.pending_tp_order_id = None
                self.buy_count = 2

        except Exception as e:
            await self.exchange.cancel_all_orders(symbol=self.symbol)
            logging.error(f"[{self.symbol}] Error: {type(e).__name__}: {e}")

    async def test(self):
        if self.symbol == 'CHR/USDT:USDT' : self.buy_count += 1
        print('symbol: ', self.symbol, 'buy_count : ', self.buy_count)
        print(self.symbol, "done" )