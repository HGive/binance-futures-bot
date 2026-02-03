# strategies/min15_3p_strategy.py
"""
15분봉 3% 추세추종 단타 전략
- 추세 방향으로만 진입 (눌림목/되돌림 활용)
- 3% 고정 익절
- 1회 추가매수 후 손절
"""
import pandas as pd
import logging
from modules.module_rsi import calc_rsi
from modules.module_ema import calc_ema
from modules.module_common import calc_buy_unit

# === 전략 상수 ===
TIMEFRAME = "15m"
LEVERAGE = 3
EMA_MEDIUM = 20
EMA_SLOW = 120
SLOPE_PERIOD = 3
RSI_PERIOD = 14
RSI_THRESHOLD = 50
TAKE_PROFIT_PCT = 0.03      # +3%
AVG_DOWN_TRIGGER_PCT = 0.05  # -5%에서 추가매수
STOP_LOSS_PCT = -7.0         # 추가매수 후 -7%에서 손절
AVG_DOWN_MULTIPLIER = 2


class Min15Strategy3p:
    def __init__(self, exchange, symbol, leverage=LEVERAGE, timeframe=TIMEFRAME):
        self.exchange = exchange
        self.symbol = symbol
        self.leverage = leverage
        self.timeframe = timeframe
        self.avg_down_count = 0  # 추가매수 횟수 추적
        self.first_entry_price = None  # 최초 진입가 (추가매수 트리거용)

    async def setup(self):
        """초기화: 레버리지, 마진모드 설정"""
        await self.exchange.cancel_all_orders(symbol=self.symbol)
        await self.exchange.set_leverage(self.leverage, self.symbol)
        await self.exchange.set_margin_mode("isolated", self.symbol)
        logging.info(f"[{self.symbol}] Setup complete - Leverage: {self.leverage}x, Timeframe: {self.timeframe}")

    async def run_once(self):
        """메인 루프 - 1회 실행"""
        try:
            # 1. 데이터 수집
            balance = await self.exchange.fetch_balance()
            total_balance = balance["USDT"]["total"]
            avbl = balance["USDT"]["free"]
            buy_unit = calc_buy_unit(total_balance)

            ohlcv = await self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=200)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            
            current_price = df["close"].iloc[-1]
            rsi = calc_rsi(df, RSI_PERIOD)
            ema20 = calc_ema(df["close"], EMA_MEDIUM)
            ema120 = calc_ema(df["close"], EMA_SLOW)

            positions = await self.exchange.fetch_positions(symbols=[self.symbol])
            position = positions[0] if positions and positions[0]["contracts"] > 0 else None

            # 2. 포지션이 있으면 → 관리 모드
            if position:
                await self._manage_position(position, avbl, buy_unit, current_price)
                return

            # 3. 포지션이 없으면 → 진입 판단
            self._reset_state()  # 상태 초기화
            
            if avbl < buy_unit:
                logging.debug(f"[{self.symbol}] Insufficient balance: {avbl:.2f} < {buy_unit}")
                return

            trend = self._detect_trend(current_price, ema20, ema120)
            if trend == "NONE":
                return

            # 진입 조건 체크
            should_long = trend == "UPTREND" and rsi < RSI_THRESHOLD
            should_short = trend == "DOWNTREND" and rsi > RSI_THRESHOLD

            if should_long:
                await self._enter_position("buy", buy_unit, current_price)
            elif should_short:
                await self._enter_position("sell", buy_unit, current_price)

        except Exception as e:
            logging.error(f"[{self.symbol}] Error: {type(e).__name__}: {e}")

    def _detect_trend(self, price, ema20, ema120) -> str:
        """추세 판단: 가격 위치 + EMA 기울기"""
        ema20_now, ema20_prev = ema20.iloc[-1], ema20.iloc[-SLOPE_PERIOD]
        ema120_now, ema120_prev = ema120.iloc[-1], ema120.iloc[-SLOPE_PERIOD]

        slope_20 = (ema20_now - ema20_prev) / ema20_prev * 100
        slope_120 = (ema120_now - ema120_prev) / ema120_prev * 100

        # 상승 추세
        if price > ema20_now and price > ema120_now and slope_20 > 0 and slope_120 > 0:
            return "UPTREND"
        # 하락 추세
        if price < ema20_now and price < ema120_now and slope_20 < 0 and slope_120 < 0:
            return "DOWNTREND"
        return "NONE"

    async def _enter_position(self, side: str, buy_unit: int, price: float):
        """시장가 진입 + TP 주문"""
        amount = buy_unit * self.leverage / price
        tp_price = price * (1 + TAKE_PROFIT_PCT) if side == "buy" else price * (1 - TAKE_PROFIT_PCT)
        tp_side = "sell" if side == "buy" else "buy"

        try:
            await self.exchange.create_order(self.symbol, "market", side, amount)
            await self.exchange.create_order(
                self.symbol, "TAKE_PROFIT_MARKET", tp_side, amount, None,
                params={"stopPrice": tp_price}
            )
            self.first_entry_price = price
            logging.info(f"[{self.symbol}] ENTRY {side.upper()} @ {price:.4f} | TP @ {tp_price:.4f}")
        except Exception as e:
            logging.error(f"[{self.symbol}] Entry failed: {e}")

    async def _manage_position(self, position, avbl: float, buy_unit: int, current_price: float):
        """포지션 관리: 익절/추가매수/손절"""
        side = position["side"]
        entry_price = float(position["entryPrice"])
        contracts = float(position["contracts"])
        pnl_pct = float(position["percentage"])  # unrealizedPnl %

        # 봇 재시작 시 추매 완료 여부 추론 (포지션 크기로 판단)
        if self.avg_down_count == 0:
            expected_single = buy_unit * self.leverage / entry_price
            if contracts > expected_single * 1.5:
                self.avg_down_count = 1
                logging.info(f"[{self.symbol}] Detected existing avg_down position (contracts={contracts:.4f})")

        # 익절 주문 존재 여부 확인
        open_orders = await self.exchange.fetch_open_orders(self.symbol)
        order_types = [o.get("type", "") for o in open_orders]
        logging.debug(f"[{self.symbol}] Open orders types: {order_types}")
        has_tp = any(
            o.get("type", "") in ("TAKE_PROFIT_MARKET", "take_profit_market", "TAKE_PROFIT")
            for o in open_orders
        )

        # TP 주문이 없으면 재설정
        if not has_tp:
            tp_price = entry_price * (1 + TAKE_PROFIT_PCT) if side == "long" else entry_price * (1 - TAKE_PROFIT_PCT)
            tp_side = "sell" if side == "long" else "buy"
            await self.exchange.create_order(
                self.symbol, "TAKE_PROFIT_MARKET", tp_side, contracts, None,
                params={"stopPrice": tp_price}
            )
            logging.info(f"[{self.symbol}] TP order reset @ {tp_price:.4f}")

        # 최초 진입가 설정 (재시작 대비)
        if self.first_entry_price is None:
            self.first_entry_price = entry_price

        # === 손절 조건 체크 ===
        # Case 1: 추가매수 후 -5% 이하
        if self.avg_down_count >= 1 and pnl_pct <= STOP_LOSS_PCT:
            await self._close_position(side, contracts, "STOP_LOSS_AFTER_AVG_DOWN")
            return

        # === 추가매수 조건 체크 ===
        if self.avg_down_count < 1:
            price_change_pct = (current_price - self.first_entry_price) / self.first_entry_price
            
            # 롱: -5% 하락 시 추가매수
            # 숏: +5% 상승 시 추가매수
            trigger_hit = (side == "long" and price_change_pct <= -AVG_DOWN_TRIGGER_PCT) or \
                          (side == "short" and price_change_pct >= AVG_DOWN_TRIGGER_PCT)

            if trigger_hit:
                required_margin = buy_unit * AVG_DOWN_MULTIPLIER
                
                # Case 2: 잔고 부족 → 즉시 손절
                if avbl < required_margin:
                    await self._close_position(side, contracts, "NO_MARGIN_FOR_AVG_DOWN")
                    return
                
                # 추가매수 실행
                await self._average_down(side, buy_unit, current_price, contracts)

    async def _average_down(self, side: str, buy_unit: int, price: float, current_contracts: float):
        """추가매수 실행 (2배 물량)"""
        amount = buy_unit * AVG_DOWN_MULTIPLIER * self.leverage / price
        order_side = "buy" if side == "long" else "sell"

        try:
            # 기존 TP 취소
            await self.exchange.cancel_all_orders(symbol=self.symbol)
            
            # 추가매수
            await self.exchange.create_order(self.symbol, "market", order_side, amount)
            self.avg_down_count += 1
            
            # 새 TP 설정 (평균가 기준으로 exchange에서 자동 계산됨)
            positions = await self.exchange.fetch_positions(symbols=[self.symbol])
            new_entry = float(positions[0]["entryPrice"])
            new_contracts = float(positions[0]["contracts"])
            
            tp_price = new_entry * (1 + TAKE_PROFIT_PCT) if side == "long" else new_entry * (1 - TAKE_PROFIT_PCT)
            tp_side = "sell" if side == "long" else "buy"
            
            await self.exchange.create_order(
                self.symbol, "TAKE_PROFIT_MARKET", tp_side, new_contracts, None,
                params={"stopPrice": tp_price}
            )
            logging.info(f"[{self.symbol}] AVG DOWN #{self.avg_down_count} @ {price:.4f} | New Entry: {new_entry:.4f} | TP: {tp_price:.4f}")
        except Exception as e:
            logging.error(f"[{self.symbol}] Average down failed: {e}")

    async def _close_position(self, side: str, contracts: float, reason: str):
        """포지션 청산 (시장가)"""
        close_side = "sell" if side == "long" else "buy"
        try:
            await self.exchange.cancel_all_orders(symbol=self.symbol)
            await self.exchange.create_order(self.symbol, "market", close_side, contracts)
            logging.warning(f"[{self.symbol}] CLOSED - Reason: {reason}")
            self._reset_state()
        except Exception as e:
            logging.error(f"[{self.symbol}] Close failed: {e}")

    def _reset_state(self):
        """상태 초기화"""
        self.avg_down_count = 0
        self.first_entry_price = None
