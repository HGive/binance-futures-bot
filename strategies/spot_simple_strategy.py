# strategies/spot_simple_strategy.py
import pandas as pd
import logging
import random
import math
import time
from modules.module_rsi import calc_rsi
from modules.module_ma import get_ma_signals

class SpotSimpleStrategy:
    def __init__(self, exchange, symbol, timeframe="1h"):
        self.exchange = exchange
        self.symbol = symbol
        self.timeframe = timeframe

    async def setup(self):
        """초기 설정"""
        await self.exchange.cancel_all_orders(symbol=self.symbol)

    async def run_once(self):
        """메인 실행 로직"""
        try:
            # 1. 잔고 확인
            balance = await self.exchange.fetch_balance()
            total_balance = balance["USDT"]["total"]
            available = balance["USDT"]["free"]
            
            if available < 5:  # 최소 10 USDT 필요
                return

            # 2. OHLCV 데이터 가져오기
            ohlcv = await self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=200)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            rsi = calc_rsi(df, 14)
            ma_signals = get_ma_signals(df)
            current_price = df["close"].iloc[-1]

            # 3. 스팟 잔고 정보 가져오기
            base_currency = self.symbol.split('/')[0]  # 예: CHR
            base_balance = balance[base_currency]["free"] if base_currency in balance else 0

            # 4. 코인을 보유하고 있으면 기존 포지션 처리
            if base_balance > 0:
                await self._handle_existing_position(base_balance, current_price)
                return

            # 5. 코인을 보유하지 않으면 새 포지션 진입 로직
            await self._handle_new_position(total_balance, rsi, current_price, ma_signals)

        except Exception as e:
            logging.error(f"[{self.symbol}] Error: {type(e).__name__}: {e}")

    async def _handle_existing_position(self, position, current_price):
        """기존 포지션 처리"""
        contracts = float(position["contracts"])
        percentage = float(position["percentage"])
        
        # -50%일 때 한번만 추가 매수
        if percentage <= -50:
            buy_unit = self._calc_buy_unit(1000)  # 고정 금액으로 추가 매수
            amount = buy_unit / current_price
            
            await self.exchange.create_order(
                self.symbol, "market", "buy", amount
            )
            logging.info(f"[{self.symbol}] Additional buy at {current_price}")

    async def _handle_new_position(self, total_balance, rsi, current_price, ma_signals):
        """새 포지션 진입 (MA + RSI 기반)"""
        # 매수 단위 계산 (무조건 1 단위)
        buy_unit = self._calc_buy_unit(total_balance)
        amount = buy_unit * 3 / current_price  # 3배 레버리지

        # MA + RSI 기반 진입 조건
        should_long = False
        should_short = False

        # 롱 진입 조건
        if (ma_signals['ma40_slope_positive'] and 
            ma_signals['ma120_slope_positive'] and 
            ma_signals['price_above_ma40'] and
            rsi < 50):
            should_long = True

        # 숏 진입 조건  
        elif (not ma_signals['ma40_slope_positive'] and 
              not ma_signals['ma120_slope_positive'] and 
              not ma_signals['price_above_ma40'] and
              rsi > 50):
            should_short = True

        # 포지션 진입
        if should_long:
            await self.exchange.create_order(
                self.symbol, "market", "buy", amount
            )
            logging.info(f"[{self.symbol}] LONG opened at {current_price} (MA40: {ma_signals['ma40']:.2f}, MA120: {ma_signals['ma120']:.2f})")

        elif should_short:
            await self.exchange.create_order(
                self.symbol, "market", "sell", amount
            )
            logging.info(f"[{self.symbol}] SHORT opened at {current_price} (MA40: {ma_signals['ma40']:.2f}, MA120: {ma_signals['ma120']:.2f})")

    def _calc_buy_unit(self, total_balance) -> int:
        """매수 단위 계산"""
        base_amount = total_balance / 10
        buy_unit = math.floor(base_amount / 5) * 5
        return max(buy_unit, 5)  # 최소 5 USDT