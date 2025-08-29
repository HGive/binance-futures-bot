# strategies/hour_3p_strategy.py
import pandas as pd
import logging
import random
import math
import time
from modules.module_rsi import calc_rsi

class Hour3PStrategy:
    def __init__(self, exchange, symbol, leverage=3, timeframe="1h"):
        self.exchange = exchange
        self.symbol = symbol
        self.leverage = leverage
        self.timeframe = timeframe
        self.pending_tp_order_id = None
        self.buy_unit = 0
        self.current_price = 0
        self.liquidation_count = 0
        self.trading_suspended_until = None

    async def setup(self):
        await self.exchange.cancel_all_orders(symbol=self.symbol)
        await self.exchange.set_leverage(self.leverage, self.symbol)
        await self.exchange.set_margin_mode("isolated", self.symbol)

    async def run_once(self):
        try:
            # 거래 중단 체크
            if self.trading_suspended_until and time.time() * 1000 < self.trading_suspended_until:
                logging.info(f"[{self.symbol}] Trading suspended until {self.trading_suspended_until}")
                return
            
            # 거래 중단 시간이 지났으면 초기화
            if self.trading_suspended_until and time.time() * 1000 >= self.trading_suspended_until:
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                self.liquidation_count = 0
                self.trading_suspended_until = None
                self.pending_tp_order_id = None
                logging.info(f"[{self.symbol}] Trading suspension lifted")

            # logging.info(f"symbol: {self.symbol}. run_once start")
            balance = await self.exchange.fetch_balance()
            total_balance = balance["USDT"]["total"]
            avbl = balance["USDT"]["free"]
            self.buy_unit : int = self.calc_buy_unit(total_balance)
            if avbl < self.buy_unit:
                logging.info(f"not enough minerals.")
                return

            ohlcv = await self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=200)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            rsi = calc_rsi(df, 14)
            self.current_price = df["close"].iloc[-1]
            positions = await self.exchange.fetch_positions(symbols=[self.symbol])
            current_time = int(time.time() * 1000)  # 현재 시간 (ms)
            one_hour_ago = current_time - (61 * 60 * 1000)  # 1시간 1분 전
            liquidations = await self.exchange.fetch_my_liquidations(symbol=self.symbol, limit=1, since=one_hour_ago)

            #liquidation check
            if len(liquidations) > 0:
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                await self.re_order(liquidations=liquidations)

            # take profit check
            if self.pending_tp_order_id:
                pending_tp_order = await self.exchange.fetch_order(self.pending_tp_order_id, self.symbol)
                if pending_tp_order["status"] == "closed":
                    await self.exchange.cancel_all_orders(symbol=self.symbol)
                    self.pending_tp_order_id = None
                    logging.info(f"[{self.symbol}] >>> TAKE PROFIT!")
                    return
                
            #tp order check
            if positions and len(positions) > 0 and positions[0]["contracts"] > 0 :
                open_orders = await self.exchange.fetch_open_orders(self.symbol)
                if len(open_orders) < 1:
                    position_info = positions[0]
                    position_side = position_info['side']
                    amount = abs(float(position_info['contracts']))
                    entry_price = float(position_info['entryPrice'])
                    if position_side == 'long':
                        tp_price = entry_price * 1.03
                        tp_side = 'sell'
                    else:  # short
                        tp_price = entry_price * 0.97
                        tp_side = 'buy'
                    await self.custom_tp_order(self.symbol, "TAKE_PROFIT_MARKET", tp_side, amount, tp_price)
                    return
                return
            
            # 롱/숏 진입 조건 설정
            should_long = False
            should_short = False
            
            # 롱 진입 조건 (RSI 낮을 때)
            if rsi < 30:
                should_long = random.random() < 0.8
            elif rsi < 35:
                should_long = random.random() < 0.15
            
            # 숏 진입 조건 (RSI 높을 때)
            if rsi > 72:
                should_short = random.random() < 0.8
            elif rsi > 65:
                should_short = random.random() < 0.15

            # 롱 포지션 진입
            if should_long:
                adjusted_amount = self.buy_unit * self.leverage / self.current_price
                tp_price = self.current_price * 1.03
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                done_entry = await self.custom_entry_order(self.symbol, "market", "buy", adjusted_amount, self.current_price)
                done_tp = await self.custom_tp_order(self.symbol, "TAKE_PROFIT_MARKET", "sell", adjusted_amount, tp_price)

            # 숏 포지션 진입
            elif should_short:
                adjusted_amount = self.buy_unit * self.leverage / self.current_price
                tp_price = self.current_price * 0.97
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                done_entry = await self.custom_entry_order(self.symbol, "market", "sell", adjusted_amount, self.current_price)
                done_tp = await self.custom_tp_order(self.symbol, "TAKE_PROFIT_MARKET", "buy", adjusted_amount, tp_price)

        except Exception as e:
            await self.exchange.cancel_all_orders(symbol=self.symbol)
            logging.error(f"[{self.symbol}] Error: {type(e).__name__}: {e}")


    def calc_buy_unit(self, total_balance) -> int:
        base_amount = total_balance / 10
        buy_unit = math.floor(base_amount / 5) * 5
        return max(buy_unit, 5)  # 최소 5 USDT 보장
    
    async def custom_entry_order(self,symbol,order_type,side,amount,price):
        try:
            buy_order = await self.exchange.create_order(symbol, order_type, side, amount, price)
            if buy_order['status'] != 'open' and buy_order['status'] != 'closed':
                logging.error(f"[{symbol}] REJECT {side} ORDER  - STATUS: {buy_order['status']}")
                raise
            logging.info(f"[{symbol}] {side} ORDER SUCCESS")
        except Exception as e:
            logging.error(f"[{symbol}] {side} ORDER REQUEST ERROR: {type(e).__name__}: {e}")
            raise
        
    async def custom_tp_order(self,symbol,order_type,side,amount,tp_price,):
        try:
            tp_order = await self.exchange.create_order(symbol, order_type, side, amount, None, params={"stopPrice": tp_price})
            if tp_order['status'] != 'open' and tp_order['status'] != 'closed':
                logging.error(f"[{symbol}] REJECT TP ORDER  - STATUS: {tp_order['status']}")
                raise
            logging.info(f"[{symbol}] {side} TP ORDER SUCCESS")
            self.pending_tp_order_id = tp_order["id"]
        except Exception as e:
            logging.error(f"[{symbol}] sell TP REQUEST ERROR: {type(e).__name__}: {e}")
            raise

    async def re_order(self, liquidations):
        liquid_price = liquidations[0]['price']
        liquid_side = liquidations[0]['info']['side']
        tp_ratio = 1.03 if liquid_side == 'SELL' else 0.97
        entry_side = 'buy' if liquid_side == 'SELL' else 'sell'
        tp_side = 'sell' if liquid_side == 'SELL' else 'buy'
        
        self.liquidation_count += 1
        logging.info(f"[{self.symbol}] Liquidation #{self.liquidation_count} detected at {liquid_price}, side: {liquid_side}")
        
        # 첫 번째 청산 후 재진입
        if self.liquidation_count == 1:
            if liquid_side == 'SELL':
                if self.current_price > liquid_price: return
            else:
                if self.current_price < liquid_price: return
            
            adjusted_amount = self.buy_unit * self.leverage / self.current_price
            tp_price = self.current_price * tp_ratio
            await self.exchange.cancel_all_orders(symbol=self.symbol)
            done_entry = await self.custom_entry_order(self.symbol, "market", entry_side, adjusted_amount, self.current_price)
            done_tp = await self.custom_tp_order(self.symbol, "TAKE_PROFIT_MARKET", tp_side, adjusted_amount, tp_price)
            logging.info(f"[{self.symbol}] Re-entry after first liquidation at {self.current_price} with TP at {tp_price}")
        
        # 두 번째 청산 후 거래 중단
        elif self.liquidation_count >= 2:
            # 하루(24시간) 거래 중단
            self.trading_suspended_until = int(time.time() * 1000) + (24 * 60 * 60 * 1000)
            logging.warning(f"[{self.symbol}] Second liquidation detected! Trading suspended for 24 hours until {self.trading_suspended_until}")
            await self.exchange.cancel_all_orders(symbol=self.symbol)