# strategies/min15_3p_strategy.py
"""
15분봉 추세추종 전략 (backtest_min15_3p.py 로직 1:1 이식)
- 부분 익절: +3% 도달 시 50% 청산, SL → 본전 이동
- 트레일링 스탑: 나머지 50%는 최고점 대비 ATR*2
- ATR 기반 동적 손절: 진입가 대비 ATR*2.5
- 추가매수: -5% 시 2배 물량, 이후 평균가 -7% 손절
- 포지션 사이징: 잔고의 10% (멀티 코인 대응)
"""
import math
import pandas as pd
import logging
from modules.module_rsi import calc_rsi
from modules.module_ema import calc_ema
from modules.module_atr import calc_atr

# === 전략 상수 (백테스트와 동일) ===
TIMEFRAME = "15m"
LEVERAGE = 3
EMA_MEDIUM = 20
EMA_SLOW = 120
SLOPE_PERIOD = 3
RSI_PERIOD = 14
RSI_LONG_THRESHOLD = 60       # UPTREND + RSI < 60
RSI_SHORT_THRESHOLD = 40      # DOWNTREND + RSI > 40

# === 익절/손절 상수 (백테스트와 동일) ===
PARTIAL_TP_PCT = 0.03         # +3% 도달 시 50% 부분 익절
TRAILING_STOP_ATR_MULT = 2.0  # 트레일링 스탑: 최고점 대비 ATR * 2
ATR_PERIOD = 14
INITIAL_SL_ATR_MULT = 2.5     # 초기 손절: 진입가 대비 ATR * 2.5

# === 추가매수 상수 (백테스트와 동일) ===
AVG_DOWN_TRIGGER_PCT = 0.05   # -5%에서 추가매수
AVG_DOWN_MULTIPLIER = 2       # 추가매수 물량 배수
STOP_LOSS_AFTER_AVG_PCT = -0.07  # 추가매수 후 평균가 대비 -7% 손절

# === 포지션 사이징 (멀티 코인용 10%) ===
POSITION_SIZE_PCT = 0.10
MIN_BUY_UNIT = 5


def _calc_buy_unit(total_balance: float) -> int:
    return max(math.floor(total_balance * POSITION_SIZE_PCT), MIN_BUY_UNIT)


class Min15Strategy3p:
    def __init__(self, exchange, symbol, leverage=LEVERAGE, timeframe=TIMEFRAME):
        self.exchange = exchange
        self.symbol = symbol
        self.leverage = leverage
        self.timeframe = timeframe
        self._reset_state()

    async def setup(self):
        await self.exchange.cancel_all_orders(symbol=self.symbol)
        await self.exchange.set_leverage(self.leverage, self.symbol)
        await self.exchange.set_margin_mode("isolated", self.symbol)
        logging.info(f"[{self.symbol}] Setup complete - Leverage: {self.leverage}x, Timeframe: {self.timeframe}")

    # =========================================================
    #  run_once  (백테스트 for-loop 1회 반복과 동일)
    # =========================================================
    async def run_once(self):
        try:
            # --- 데이터 수집 ---
            bal = await self.exchange.fetch_balance()
            total_balance = bal["USDT"]["total"]
            avbl = bal["USDT"]["free"]
            buy_unit = _calc_buy_unit(total_balance)

            ohlcv = await self.exchange.fetch_ohlcv(
                self.symbol, timeframe=self.timeframe, limit=200
            )
            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            c = float(df["close"].iloc[-1])
            h = float(df["high"].iloc[-1])
            l = float(df["low"].iloc[-1])
            rsi = calc_rsi(df, RSI_PERIOD)
            ema20 = calc_ema(df["close"], EMA_MEDIUM)
            ema120 = calc_ema(df["close"], EMA_SLOW)
            atr = calc_atr(df, ATR_PERIOD)
            if atr <= 0:
                atr = c * 0.01

            positions = await self.exchange.fetch_positions(symbols=[self.symbol])
            pos = positions[0] if positions and positions[0]["contracts"] > 0 else None

            # --- 포지션 있으면 → 관리 (백테스트 순서 그대로) ---
            if pos:
                await self._manage_position(pos, avbl, buy_unit, c, h, l, atr)
                return

            # --- 포지션 없으면 → 잔여 주문 정리 후 진입 판단 ---
            if self.first_entry_price is not None:
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                logging.info(f"[{self.symbol}] Position gone → cleaned up stale orders")
            self._reset_state()

            if avbl < buy_unit:
                logging.debug(f"[{self.symbol}] Insufficient balance: {avbl:.2f} < {buy_unit}")
                return

            trend = self._detect_trend(c, ema20, ema120)
            if trend == "NONE":
                return

            should_long = trend == "UPTREND" and rsi < RSI_LONG_THRESHOLD
            should_short = trend == "DOWNTREND" and rsi > RSI_SHORT_THRESHOLD

            if should_long:
                await self._enter("buy", buy_unit, c, atr)
            elif should_short:
                await self._enter("sell", buy_unit, c, atr)

        except Exception as e:
            logging.error(f"[{self.symbol}] Error: {type(e).__name__}: {e}")

    # =========================================================
    #  추세 판단 (백테스트 detect_trend 동일)
    # =========================================================
    def _detect_trend(self, price, ema20, ema120) -> str:
        ema20_now = float(ema20.iloc[-1])
        ema20_prev = float(ema20.iloc[-1 - SLOPE_PERIOD])
        ema120_now = float(ema120.iloc[-1])
        ema120_prev = float(ema120.iloc[-1 - SLOPE_PERIOD])
        slope_20 = (ema20_now - ema20_prev) / ema20_prev * 100 if ema20_prev else 0
        slope_120 = (ema120_now - ema120_prev) / ema120_prev * 100 if ema120_prev else 0

        if price > ema20_now and price > ema120_now and slope_20 > 0 and slope_120 > 0:
            return "UPTREND"
        if price < ema20_now and price < ema120_now and slope_20 < 0 and slope_120 < 0:
            return "DOWNTREND"
        return "NONE"

    # =========================================================
    #  진입 (백테스트 진입 로직 동일)
    # =========================================================
    async def _enter(self, side: str, buy_unit: int, price: float, atr: float):
        amount = buy_unit * self.leverage / price
        tp_side = "sell" if side == "buy" else "buy"

        # ATR 기반 초기 SL (백테스트: c - atr * 2.5 / c + atr * 2.5)
        if side == "buy":
            sl_price = price - atr * INITIAL_SL_ATR_MULT
        else:
            sl_price = price + atr * INITIAL_SL_ATR_MULT

        # 1) 혹시 남아있는 잔여 주문 정리 후 Market 진입
        await self.exchange.cancel_all_orders(symbol=self.symbol)
        try:
            await self.exchange.create_order(self.symbol, "market", side, amount)
        except Exception as e:
            logging.error(f"[{self.symbol}] Entry failed: {e}")
            return

        # 2) 상태 갱신 (Market 성공 후 즉시 — SL 실패와 무관하게 추적)
        self.first_entry_price = price
        self.entry_price = price
        self.size = amount
        self.sl_price = sl_price
        self.partial_taken = False
        self.trailing_active = False
        self.best_price = price
        self.avg_down_count = 0

        logging.info(
            f"[{self.symbol}] ENTRY {side.upper()} @ {price:.4f} | "
            f"SL @ {sl_price:.4f} (ATR={atr:.6f})"
        )

        # 3) SL 주문 (실패해도 코드 기반 SL이 관리)
        await self._place_sl_order(tp_side, amount, sl_price)

    # =========================================================
    #  포지션 관리 (백테스트 순서 1→2→3→4→5 그대로)
    # =========================================================
    async def _manage_position(self, pos, avbl: float, buy_unit: int,
                                c: float, h: float, l: float, atr: float):
        side = pos["side"]           # "long" | "short"
        entry_price = float(pos["entryPrice"])
        contracts = float(pos["contracts"])

        # --- 봇 재시작 시 상태 복원 ---
        if self.first_entry_price is None:
            self.first_entry_price = entry_price
            self.entry_price = entry_price
            self.size = contracts
            # 추가매수 여부 추론
            expected = buy_unit * self.leverage / entry_price
            if contracts > expected * 1.5:
                self.avg_down_count = 1
                logging.info(f"[{self.symbol}] Restored: avg_down detected (contracts={contracts:.4f})")
            # 부분 익절 여부 추론
            if contracts < expected * 0.7:
                self.partial_taken = True
                self.trailing_active = True
                self.best_price = h if side == "long" else l
                logging.info(f"[{self.symbol}] Restored: partial_taken detected → trailing mode")
            # SL 복원
            if self.sl_price is None:
                if self.avg_down_count >= 1:
                    if side == "long":
                        self.sl_price = entry_price * (1 + STOP_LOSS_AFTER_AVG_PCT)
                    else:
                        self.sl_price = entry_price * (1 - STOP_LOSS_AFTER_AVG_PCT)
                elif self.partial_taken:
                    self.sl_price = entry_price  # 본전
                else:
                    if side == "long":
                        self.sl_price = entry_price - atr * INITIAL_SL_ATR_MULT
                    else:
                        self.sl_price = entry_price + atr * INITIAL_SL_ATR_MULT

        # 교환소 entryPrice(평균가) 를 진실 소스로 사용
        self.entry_price = entry_price
        self.size = contracts

        # --- 1. 트레일링 최고/최저 갱신 (백테스트 동일) ---
        if self.trailing_active:
            if side == "long":
                self.best_price = max(self.best_price or h, h)
            else:
                self.best_price = min(self.best_price or l, l)

        # --- 2. 추가매수 체크 (백테스트 동일) ---
        if self.avg_down_count < 1 and not self.partial_taken:
            first = self.first_entry_price
            if side == "long":
                price_change = (l - first) / first
                trigger_hit = price_change <= -AVG_DOWN_TRIGGER_PCT
            else:
                price_change = (h - first) / first
                trigger_hit = price_change >= AVG_DOWN_TRIGGER_PCT

            if trigger_hit:
                avg_margin = buy_unit * AVG_DOWN_MULTIPLIER
                if avbl >= avg_margin:
                    await self._average_down(side, buy_unit, c)
                    return  # 다음 루프에서 갱신된 포지션으로 재진입
                else:
                    await self._close(side, contracts, "NO_MARGIN_FOR_AVG_DOWN")
                    return

        # --- 3. 손절 체크 (백테스트 동일) ---
        sl = self.sl_price
        if sl is not None:
            if side == "long" and l <= sl:
                await self._close(side, contracts, "STOP_LOSS_AFTER_AVG" if self.avg_down_count > 0 else "STOP_LOSS")
                return
            if side == "short" and h >= sl:
                await self._close(side, contracts, "STOP_LOSS_AFTER_AVG" if self.avg_down_count > 0 else "STOP_LOSS")
                return

        # --- 4. 부분 익절 체크 (+3% 50%) (백테스트 동일) ---
        if not self.partial_taken:
            if side == "long":
                partial_tp = entry_price * (1 + PARTIAL_TP_PCT)
                if h >= partial_tp:
                    await self._partial_close(side, contracts, entry_price)
                    return
            else:
                partial_tp = entry_price * (1 - PARTIAL_TP_PCT)
                if l <= partial_tp:
                    await self._partial_close(side, contracts, entry_price)
                    return

        # --- 5. 트레일링 스탑 체크 (백테스트 동일) ---
        if self.trailing_active:
            trail_atr = atr * TRAILING_STOP_ATR_MULT
            if side == "long":
                trail_sl = self.best_price - trail_atr
                if l <= trail_sl:
                    await self._close(side, contracts, "TRAILING_STOP")
                    return
            else:
                trail_sl = self.best_price + trail_atr
                if h >= trail_sl:
                    await self._close(side, contracts, "TRAILING_STOP")
                    return
            logging.debug(
                f"[{self.symbol}] Trailing: best={self.best_price:.4f} "
                f"trail_sl={trail_sl:.4f} price={c:.4f}"
            )

        # --- SL 주문 존재 확인 & 복구 (안전장치) ---
        await self._ensure_sl_order(side, contracts)

    # =========================================================
    #  부분 익절 실행 (50% 청산 → 트레일링 모드 전환)
    # =========================================================
    async def _partial_close(self, side: str, contracts: float,
                              entry_price: float):
        close_side = "sell" if side == "long" else "buy"
        partial_size = contracts * 0.5

        try:
            await self.exchange.cancel_all_orders(symbol=self.symbol)
            self.sl_order_placed = False
            await self.exchange.create_order(
                self.symbol, "market", close_side, partial_size
            )
            self.partial_taken = True
            self.trailing_active = True
            self.best_price = None  # 다음 run_once에서 갱신
            # SL을 본전으로 이동 (백테스트: position["sl_price"] = entry_price)
            self.sl_price = entry_price
            logging.info(
                f"[{self.symbol}] PARTIAL TP 50% closed | "
                f"Remaining ~{contracts - partial_size:.4f} | SL → breakeven @ {entry_price:.4f}"
            )
        except Exception as e:
            logging.error(f"[{self.symbol}] Partial close failed: {e}")

    # =========================================================
    #  추가매수 (백테스트 로직 동일)
    # =========================================================
    async def _average_down(self, side: str, buy_unit: int, price: float):
        amount = buy_unit * AVG_DOWN_MULTIPLIER * self.leverage / price
        order_side = "buy" if side == "long" else "sell"

        try:
            await self.exchange.cancel_all_orders(symbol=self.symbol)
            self.sl_order_placed = False
            await self.exchange.create_order(self.symbol, "market", order_side, amount)
            self.avg_down_count = 1

            # 갱신된 포지션 조회
            positions = await self.exchange.fetch_positions(symbols=[self.symbol])
            new_entry = float(positions[0]["entryPrice"])
            new_contracts = float(positions[0]["contracts"])

            # 추가매수 후 SL → 평균가 -7% (백테스트 동일)
            if side == "long":
                self.sl_price = new_entry * (1 + STOP_LOSS_AFTER_AVG_PCT)
            else:
                self.sl_price = new_entry * (1 - STOP_LOSS_AFTER_AVG_PCT)

            # SL 주문 재설정
            sl_side = "sell" if side == "long" else "buy"
            await self._place_sl_order(sl_side, new_contracts, self.sl_price)

            self.entry_price = new_entry
            self.size = new_contracts
            self.partial_taken = False

            logging.info(
                f"[{self.symbol}] AVG DOWN @ {price:.4f} | "
                f"New Entry: {new_entry:.4f} | SL: {self.sl_price:.4f}"
            )
        except Exception as e:
            logging.error(f"[{self.symbol}] Average down failed: {e}")

    # =========================================================
    #  전량 청산
    # =========================================================
    async def _close(self, side: str, contracts: float, reason: str):
        close_side = "sell" if side == "long" else "buy"
        try:
            await self.exchange.cancel_all_orders(symbol=self.symbol)
            self.sl_order_placed = False
            await self.exchange.create_order(self.symbol, "market", close_side, contracts)
            logging.warning(f"[{self.symbol}] CLOSED - Reason: {reason}")
            self._reset_state()
        except Exception as e:
            logging.error(f"[{self.symbol}] Close failed: {e}")

    # =========================================================
    #  SL 주문 배치 (STOP_MARKET 미지원 시 코드 SL로 폴백)
    # =========================================================
    async def _place_sl_order(self, side: str, amount: float, sl_price: float):
        if not self.sl_order_supported:
            return
        try:
            await self.exchange.create_order(
                self.symbol, "STOP_MARKET", side, amount, None,
                params={"stopPrice": sl_price, "reduceOnly": True}
            )
            self.sl_order_placed = True
        except Exception as e:
            if "-4120" in str(e):
                self.sl_order_supported = False
                logging.warning(
                    f"[{self.symbol}] STOP_MARKET not supported → code-based SL only"
                )
            else:
                logging.warning(f"[{self.symbol}] SL order failed: {e}")

    # =========================================================
    #  SL 주문 복구 안전장치
    # =========================================================
    async def _ensure_sl_order(self, side: str, contracts: float):
        if self.trailing_active:
            return  # 트레일링 모드에서는 코드로 SL 관리
        if not self.sl_order_supported:
            return  # 이 심볼은 STOP_MARKET 미지원
        if self.sl_order_placed:
            return  # 이미 SL 주문이 배치됨
        if self.sl_price is not None:
            sl_side = "sell" if side == "long" else "buy"
            await self._place_sl_order(sl_side, contracts, self.sl_price)
            logging.info(f"[{self.symbol}] SL order restored @ {self.sl_price:.4f}")

    # =========================================================
    #  상태 초기화
    # =========================================================
    def _reset_state(self):
        self.avg_down_count = 0
        self.first_entry_price = None
        self.entry_price = None
        self.size = None
        self.partial_taken = False
        self.trailing_active = False
        self.best_price = None
        self.sl_price = None
        self.sl_order_supported = True   # STOP_MARKET 지원 여부
        self.sl_order_placed = False     # SL 주문 배치 여부
