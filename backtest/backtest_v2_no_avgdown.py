"""
min15_3p_strategy 백테스팅 v2 - 물타기 없음
- 물타기 완전 제거
- 손익비 개선 (익절 늦춤, 손절 타이트)
- 진입 필터 강화
"""
import sys
import math
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.module_ema import calc_ema

# === 전략 상수 ===
TIMEFRAME = "15m"
LEVERAGE = 3
EMA_MEDIUM = 20
EMA_SLOW = 120
SLOPE_PERIOD = 3
RSI_PERIOD = 14

# === 개선된 진입 필터 ===
RSI_LONG_THRESHOLD = 55       # 60 → 55 (더 엄격)
RSI_SHORT_THRESHOLD = 45     # 40 → 45 (더 엄격)

# === 개선된 익절/손절 ===
PARTIAL_TP_PCT = 0.05         # 3% → 5% (더 늦게 익절)
TRAILING_STOP_ATR_MULT = 2.5  # 2.0 → 2.5 (추세 더 타기)
ATR_PERIOD = 14
INITIAL_SL_ATR_MULT = 2.0     # 2.5 → 2.0 (손절 타이트)

# === 물타기 비활성화 ===
ENABLE_AVG_DOWN = False

# === 포지션 사이징 ===
POSITION_SIZE_PCT = 0.10
MIN_BUY_UNIT = 5

INITIAL_BALANCE = 1000.0
SYMBOL = "BTC/USDT:USDT"


def calc_buy_unit(total_balance: float) -> int:
    base_amount = total_balance * POSITION_SIZE_PCT
    return max(math.floor(base_amount), MIN_BUY_UNIT)


def calc_rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    _gain = gains.ewm(com=(period - 1), min_periods=period).mean()
    _loss = losses.ewm(com=(period - 1), min_periods=period).mean()
    rs = _gain / _loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr


def detect_trend(price: float, ema20: pd.Series, ema120: pd.Series, idx: int) -> str:
    if idx < SLOPE_PERIOD:
        return "NONE"
    ema20_now = ema20.iloc[idx]
    ema20_prev = ema20.iloc[idx - SLOPE_PERIOD]
    ema120_now = ema120.iloc[idx]
    ema120_prev = ema120.iloc[idx - SLOPE_PERIOD]
    slope_20 = (ema20_now - ema20_prev) / ema20_prev * 100 if ema20_prev else 0
    slope_120 = (ema120_now - ema120_prev) / ema120_prev * 100 if ema120_prev else 0

    if price > ema20_now and price > ema120_now and slope_20 > 0 and slope_120 > 0:
        return "UPTREND"
    if price < ema20_now and price < ema120_now and slope_20 < 0 and slope_120 < 0:
        return "DOWNTREND"
    return "NONE"


def run_backtest(df: pd.DataFrame, initial_balance: float = INITIAL_BALANCE) -> tuple:
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if "timestamp" not in df.columns and df.index.name != "timestamp":
        df = df.reset_index()

    close = df["close"].astype(float)
    high_s = df["high"].astype(float)
    low_s = df["low"].astype(float)
    ema20 = calc_ema(close, EMA_MEDIUM)
    ema120 = calc_ema(close, EMA_SLOW)
    rsi_series = calc_rsi_series(close, RSI_PERIOD)
    atr_series = calc_atr_series(high_s, low_s, close, ATR_PERIOD)

    balance = initial_balance
    position = None
    trades = []
    equity = [initial_balance]
    min_bars = max(EMA_SLOW + SLOPE_PERIOD, RSI_PERIOD, ATR_PERIOD)

    for i in range(min_bars, len(df)):
        row = df.iloc[i]
        ts = row.get("timestamp", df.index[i])
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        rsi = rsi_series.iloc[i]
        if pd.isna(rsi):
            rsi = 50.0
        atr = atr_series.iloc[i]
        if pd.isna(atr) or atr <= 0:
            atr = c * 0.01

        buy_unit = calc_buy_unit(balance)

        # ===== 포지션 관리 =====
        if position is not None:
            side = position["side"]
            entry_price = position["entry_price"]
            size = position["size"]
            sl_price = position["sl_price"]

            # 트레일링 최고/최저 갱신
            if position["trailing_active"]:
                if side == "long":
                    position["best_price"] = max(position["best_price"], h)
                else:
                    position["best_price"] = min(position["best_price"], l)

            # 손절 체크
            if side == "long" and l <= sl_price:
                pnl = size * (sl_price - entry_price)
                balance += pnl
                trades.append({
                    "timestamp": ts, "side": side, "exit_reason": "STOP_LOSS",
                    "entry_price": entry_price, "exit_price": sl_price,
                    "pnl": pnl, "balance_after": balance
                })
                position = None
                equity.append(balance)
                continue
            elif side == "short" and h >= sl_price:
                pnl = size * (entry_price - sl_price)
                balance += pnl
                trades.append({
                    "timestamp": ts, "side": side, "exit_reason": "STOP_LOSS",
                    "entry_price": entry_price, "exit_price": sl_price,
                    "pnl": pnl, "balance_after": balance
                })
                position = None
                equity.append(balance)
                continue

            # 부분 익절 (+5%)
            if not position["partial_taken"]:
                if side == "long":
                    partial_tp = entry_price * (1 + PARTIAL_TP_PCT)
                    if h >= partial_tp:
                        partial_size = size * 0.5
                        pnl = partial_size * (partial_tp - entry_price)
                        balance += pnl
                        trades.append({
                            "timestamp": ts, "side": side, "exit_reason": "PARTIAL_TP",
                            "entry_price": entry_price, "exit_price": partial_tp,
                            "pnl": pnl, "balance_after": balance
                        })
                        position["size"] = size - partial_size
                        position["partial_taken"] = True
                        position["trailing_active"] = True
                        position["best_price"] = h
                        position["sl_price"] = entry_price  # 본전 보장
                else:
                    partial_tp = entry_price * (1 - PARTIAL_TP_PCT)
                    if l <= partial_tp:
                        partial_size = size * 0.5
                        pnl = partial_size * (entry_price - partial_tp)
                        balance += pnl
                        trades.append({
                            "timestamp": ts, "side": side, "exit_reason": "PARTIAL_TP",
                            "entry_price": entry_price, "exit_price": partial_tp,
                            "pnl": pnl, "balance_after": balance
                        })
                        position["size"] = size - partial_size
                        position["partial_taken"] = True
                        position["trailing_active"] = True
                        position["best_price"] = l
                        position["sl_price"] = entry_price

            # 트레일링 스탑
            if position is not None and position["trailing_active"]:
                trail_atr = atr * TRAILING_STOP_ATR_MULT
                remaining_size = position["size"]
                if side == "long":
                    trail_sl = position["best_price"] - trail_atr
                    if l <= trail_sl:
                        pnl = remaining_size * (trail_sl - entry_price)
                        balance += pnl
                        trades.append({
                            "timestamp": ts, "side": side, "exit_reason": "TRAILING_STOP",
                            "entry_price": entry_price, "exit_price": trail_sl,
                            "pnl": pnl, "balance_after": balance
                        })
                        position = None
                        equity.append(balance)
                        continue
                else:
                    trail_sl = position["best_price"] + trail_atr
                    if h >= trail_sl:
                        pnl = remaining_size * (entry_price - trail_sl)
                        balance += pnl
                        trades.append({
                            "timestamp": ts, "side": side, "exit_reason": "TRAILING_STOP",
                            "entry_price": entry_price, "exit_price": trail_sl,
                            "pnl": pnl, "balance_after": balance
                        })
                        position = None
                        equity.append(balance)
                        continue

            equity.append(balance)
            continue

        # ===== 진입 판단 =====
        trend = detect_trend(c, ema20, ema120, i)
        if trend == "NONE" or balance < buy_unit:
            equity.append(balance)
            continue

        should_long = trend == "UPTREND" and rsi < RSI_LONG_THRESHOLD
        should_short = trend == "DOWNTREND" and rsi > RSI_SHORT_THRESHOLD

        if should_long:
            size = buy_unit * LEVERAGE / c
            sl_price = c - atr * INITIAL_SL_ATR_MULT
            position = {
                "side": "long", "entry_price": c, "size": size,
                "partial_taken": False, "trailing_active": False,
                "best_price": c, "sl_price": sl_price,
            }
        elif should_short:
            size = buy_unit * LEVERAGE / c
            sl_price = c + atr * INITIAL_SL_ATR_MULT
            position = {
                "side": "short", "entry_price": c, "size": size,
                "partial_taken": False, "trailing_active": False,
                "best_price": c, "sl_price": sl_price,
            }
        equity.append(balance)

    # 마지막 포지션 청산
    if position is not None:
        last = df.iloc[-1]
        c = float(last["close"])
        ts = last.get("timestamp", df.index[-1])
        side = position["side"]
        entry_price = position["entry_price"]
        size = position["size"]
        pnl = size * (c - entry_price) if side == "long" else size * (entry_price - c)
        balance += pnl
        trades.append({
            "timestamp": ts, "side": side, "exit_reason": "END_OF_DATA",
            "entry_price": entry_price, "exit_price": c, "pnl": pnl, "balance_after": balance
        })

    return trades, pd.Series(equity), balance


def fetch_ohlcv(symbol: str = SYMBOL, timeframe: str = TIMEFRAME, limit: int = 1000) -> pd.DataFrame:
    import ccxt
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def print_summary(trades: list, equity: pd.Series, initial: float, final: float):
    print("\n" + "=" * 60)
    print("  v2 NO AVGDOWN - 물타기 없음")
    print("=" * 60)
    print(f"  기간 봉 수     : {len(equity)}")
    print(f"  초기 잔고     : {initial:,.2f} USDT")
    print(f"  최종 잔고     : {final:,.2f} USDT")
    ret_pct = (final - initial) / initial * 100
    print(f"  수익률        : {ret_pct:+.2f}%")
    print(f"  총 거래 횟수  : {len(trades)}")
    if trades:
        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        print(f"  승리 횟수     : {len(wins)}")
        print(f"  손실 횟수     : {len(losses)}")
        win_rate = len(wins) / len(pnls) * 100 if pnls else 0
        print(f"  승률          : {win_rate:.1f}%")
        if wins:
            print(f"  평균 익절    : {sum(wins)/len(wins):+.2f} USDT")
        if losses:
            print(f"  평균 손절    : {sum(losses)/len(losses):+.2f} USDT")
        if wins and losses:
            avg_win = sum(wins) / len(wins)
            avg_loss = abs(sum(losses) / len(losses))
            rr = avg_win / avg_loss if avg_loss > 0 else float('inf')
            print(f"  손익비 (R:R)  : {rr:.2f}")
        eq = pd.Series(equity)
        peak = eq.cummax()
        dd = (eq - peak) / peak * 100
        mdd = dd.min()
        print(f"  최대 낙폭(MDD): {mdd:.2f}%")
        by_reason = {}
        for t in trades:
            r = t["exit_reason"]
            by_reason[r] = by_reason.get(r, 0) + 1
        print("  청산 사유     :", by_reason)
    print("=" * 60 + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="v2 NO AVGDOWN backtest")
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--balance", type=float, default=INITIAL_BALANCE)
    args = parser.parse_args()

    print(f"Fetching {args.limit} x {TIMEFRAME} candles for {args.symbol}...")
    df = fetch_ohlcv(symbol=args.symbol, timeframe=TIMEFRAME, limit=args.limit)
    print(f"Data: {len(df)} rows")

    trades, equity, final_balance = run_backtest(df, initial_balance=args.balance)
    print_summary(trades, equity, args.balance, final_balance)

    if trades:
        print("최근 10건 거래:")
        for t in trades[-10:]:
            print(f"  {t['timestamp']} | {t['side']:5s} | {t['exit_reason']:15s} | PnL: {t['pnl']:+.2f}")


if __name__ == "__main__":
    main()
