"""
Volume + MA Slope 복합 전략 백테스팅
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
타임프레임: 1시간봉

[진입 조건 A] Volume Spike
  - 현재 거래량 > 직전 5봉 평균 × 2배
  - 양봉(close > open) → Long  /  음봉(close < open) → Short
  - SL: 신호 봉의 low (Long) / 신호 봉의 high (Short)
  - 단, SL 거리 > 5% 이면 진입 스킵 (리스크 필터)

[진입 조건 B] MA Triple Slope (조건 A 미해당 시 체크)
  - MA5, MA20, MA99 기울기 모두 + (3봉 기준) → Long
  - MA5, MA20, MA99 기울기 모두 - → Short
  - SL: entry ± ATR × 1.5

[공통 청산]
  - 부분 익절: +5% 도달 시 25% 청산 + 본전 SL + 트레일링 활성
  - 트레일링 스탑: best_price ± ATR × 2.5
  - 타임스탑: 5봉(5시간) 후 수익 없으면 강제 청산
"""
import sys
import math
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.module_ema import calc_ema

# === 타임프레임 ===
TIMEFRAME = "1h"
LEVERAGE = 3
ATR_PERIOD = 14

# === 조건 A: Volume Spike ===
VOLUME_LOOKBACK = 5       # 비교할 직전 봉 수
VOLUME_MULT = 2.0         # 거래량 배수 기준
MAX_SL_PCT = 0.05         # 신호봉 SL 거리 최대 5% (초과 시 스킵)

# === 조건 B: MA Triple Slope ===
MA_SHORT = 5
MA_MID = 20
MA_LONG = 99
SLOPE_PERIOD = 3          # N봉 전 MA와 비교
MA_SL_ATR_MULT = 1.5      # ATR × 1.5 고정 SL

# === 공통 청산 ===
PARTIAL_TP_PCT = 0.05
PARTIAL_TP_RATIO = 0.25
TRAILING_STOP_ATR_MULT = 2.5
TIME_STOP_BARS = 5        # 5봉(5시간) 수익 없으면 강제 청산

# === 포지션 사이징 ===
POSITION_SIZE_PCT = 0.10
MIN_BUY_UNIT = 5
INITIAL_BALANCE = 1000.0
SYMBOL = "BTC/USDT:USDT"


def calc_buy_unit(total_balance: float) -> int:
    base_amount = total_balance * POSITION_SIZE_PCT
    return max(math.floor(base_amount), MIN_BUY_UNIT)


def calc_atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def run_backtest(df: pd.DataFrame, initial_balance: float = INITIAL_BALANCE) -> tuple:
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if "timestamp" not in df.columns and df.index.name != "timestamp":
        df = df.reset_index()

    close = df["close"].astype(float)
    high_s = df["high"].astype(float)
    low_s = df["low"].astype(float)
    open_s = df["open"].astype(float)
    volume_s = df["volume"].astype(float)

    # 지표 계산
    atr_series = calc_atr_series(high_s, low_s, close, ATR_PERIOD)
    ma5 = calc_ema(close, MA_SHORT)
    ma20 = calc_ema(close, MA_MID)
    ma99 = calc_ema(close, MA_LONG)
    # 거래량: 현재 봉 제외한 직전 5봉 평균
    vol_avg = volume_s.shift(1).rolling(VOLUME_LOOKBACK).mean()

    balance = initial_balance
    position = None
    trades = []
    equity = [initial_balance]

    min_bars = max(MA_LONG + SLOPE_PERIOD, ATR_PERIOD, VOLUME_LOOKBACK + 1)

    for i in range(min_bars, len(df)):
        row = df.iloc[i]
        ts = row.get("timestamp", df.index[i])
        o = open_s.iloc[i]
        h = high_s.iloc[i]
        l = low_s.iloc[i]
        c = close.iloc[i]
        vol = volume_s.iloc[i]

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

            # 봉 카운트
            position["bars_open"] += 1

            # 타임스탑: 5봉 경과 + 수익 없으면 강제 청산
            if not position["trailing_active"] and position["bars_open"] >= TIME_STOP_BARS:
                if (side == "long" and c <= entry_price) or \
                   (side == "short" and c >= entry_price):
                    pnl = size * (c - entry_price) if side == "long" else size * (entry_price - c)
                    balance += pnl
                    trades.append({
                        "timestamp": ts, "side": side, "exit_reason": "TIME_STOP",
                        "entry_price": entry_price, "exit_price": c,
                        "pnl": pnl, "balance_after": balance,
                        "entry_type": position["entry_type"],
                    })
                    position = None
                    equity.append(balance)
                    continue

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
                    "pnl": pnl, "balance_after": balance,
                    "entry_type": position["entry_type"],
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
                    "pnl": pnl, "balance_after": balance,
                    "entry_type": position["entry_type"],
                })
                position = None
                equity.append(balance)
                continue

            # 부분 익절 (+5%, 25% 청산)
            if not position["partial_taken"]:
                if side == "long":
                    partial_tp = entry_price * (1 + PARTIAL_TP_PCT)
                    if h >= partial_tp:
                        partial_size = size * PARTIAL_TP_RATIO
                        pnl = partial_size * (partial_tp - entry_price)
                        balance += pnl
                        trades.append({
                            "timestamp": ts, "side": side, "exit_reason": "PARTIAL_TP",
                            "entry_price": entry_price, "exit_price": partial_tp,
                            "pnl": pnl, "balance_after": balance,
                            "entry_type": position["entry_type"],
                        })
                        position["size"] = size - partial_size
                        position["partial_taken"] = True
                        position["trailing_active"] = True
                        position["best_price"] = h
                        position["sl_price"] = entry_price  # 본전 보장
                else:
                    partial_tp = entry_price * (1 - PARTIAL_TP_PCT)
                    if l <= partial_tp:
                        partial_size = size * PARTIAL_TP_RATIO
                        pnl = partial_size * (entry_price - partial_tp)
                        balance += pnl
                        trades.append({
                            "timestamp": ts, "side": side, "exit_reason": "PARTIAL_TP",
                            "entry_price": entry_price, "exit_price": partial_tp,
                            "pnl": pnl, "balance_after": balance,
                            "entry_type": position["entry_type"],
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
                            "pnl": pnl, "balance_after": balance,
                            "entry_type": position["entry_type"],
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
                            "pnl": pnl, "balance_after": balance,
                            "entry_type": position["entry_type"],
                        })
                        position = None
                        equity.append(balance)
                        continue

            equity.append(balance)
            continue

        # ===== 진입 판단 =====
        if balance < buy_unit:
            equity.append(balance)
            continue

        entered = False

        # --- 조건 A: Volume Spike ---
        v_avg = vol_avg.iloc[i]
        if not pd.isna(v_avg) and v_avg > 0 and vol > v_avg * VOLUME_MULT:
            is_bullish = c > o
            is_bearish = c < o
            size = buy_unit * LEVERAGE / c

            if is_bullish:
                sl_price = l  # 신호 봉 low
                sl_dist = (c - sl_price) / c
                if sl_dist <= MAX_SL_PCT:
                    position = {
                        "side": "long", "entry_price": c, "size": size,
                        "sl_price": sl_price, "partial_taken": False,
                        "trailing_active": False, "best_price": c,
                        "bars_open": 0, "entry_type": "VOLUME",
                    }
                    entered = True
            elif is_bearish:
                sl_price = h  # 신호 봉 high
                sl_dist = (sl_price - c) / c
                if sl_dist <= MAX_SL_PCT:
                    position = {
                        "side": "short", "entry_price": c, "size": size,
                        "sl_price": sl_price, "partial_taken": False,
                        "trailing_active": False, "best_price": c,
                        "bars_open": 0, "entry_type": "VOLUME",
                    }
                    entered = True

        # --- 조건 B: MA Triple Slope (조건 A 미진입 시) ---
        if not entered and i >= SLOPE_PERIOD:
            ma5_now = ma5.iloc[i];    ma5_prev = ma5.iloc[i - SLOPE_PERIOD]
            ma20_now = ma20.iloc[i];  ma20_prev = ma20.iloc[i - SLOPE_PERIOD]
            ma99_now = ma99.iloc[i];  ma99_prev = ma99.iloc[i - SLOPE_PERIOD]

            slope_up = (ma5_now > ma5_prev) and (ma20_now > ma20_prev) and (ma99_now > ma99_prev)
            slope_dn = (ma5_now < ma5_prev) and (ma20_now < ma20_prev) and (ma99_now < ma99_prev)

            size = buy_unit * LEVERAGE / c

            if slope_up:
                sl_price = c - atr * MA_SL_ATR_MULT
                position = {
                    "side": "long", "entry_price": c, "size": size,
                    "sl_price": sl_price, "partial_taken": False,
                    "trailing_active": False, "best_price": c,
                    "bars_open": 0, "entry_type": "MA_SLOPE",
                }
            elif slope_dn:
                sl_price = c + atr * MA_SL_ATR_MULT
                position = {
                    "side": "short", "entry_price": c, "size": size,
                    "sl_price": sl_price, "partial_taken": False,
                    "trailing_active": False, "best_price": c,
                    "bars_open": 0, "entry_type": "MA_SLOPE",
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
            "entry_price": entry_price, "exit_price": c,
            "pnl": pnl, "balance_after": balance,
            "entry_type": position["entry_type"],
        })

    return trades, pd.Series(equity), balance


def fetch_ohlcv(symbol: str = SYMBOL, timeframe: str = TIMEFRAME, limit: int = 500) -> pd.DataFrame:
    import ccxt
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def print_summary(trades: list, equity: pd.Series, initial: float, final: float):
    print("\n" + "=" * 60)
    print("  Volume + MA Slope 복합 전략")
    print("=" * 60)
    print(f"  타임프레임     : {TIMEFRAME}")
    print(f"  [A] Volume     : 직전 {VOLUME_LOOKBACK}봉 평균 × {VOLUME_MULT}배 / 최대SL {MAX_SL_PCT*100:.0f}%")
    print(f"  [B] MA Slope   : MA{MA_SHORT}/{MA_MID}/{MA_LONG} 3선 동방향 ({SLOPE_PERIOD}봉 기준) / SL ATR×{MA_SL_ATR_MULT}")
    print(f"  부분 익절      : +{PARTIAL_TP_PCT*100:.0f}% / {PARTIAL_TP_RATIO*100:.0f}% 청산")
    print(f"  트레일링 ATR   : ×{TRAILING_STOP_ATR_MULT}")
    print(f"  타임스탑       : {TIME_STOP_BARS}봉 수익 없으면 청산")
    print("-" * 60)
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
            rr = (sum(wins)/len(wins)) / abs(sum(losses)/len(losses))
            print(f"  손익비 (R:R)  : {rr:.2f}")
        eq = pd.Series(equity)
        mdd = ((eq - eq.cummax()) / eq.cummax() * 100).min()
        print(f"  최대 낙폭(MDD): {mdd:.2f}%")

        # 청산 사유별 집계
        by_reason = {}
        for t in trades:
            r = t["exit_reason"]
            by_reason[r] = by_reason.get(r, 0) + 1
        print("  청산 사유     :", by_reason)

        # 진입 조건별 집계
        by_entry = {}
        for t in trades:
            e = t.get("entry_type", "?")
            by_entry[e] = by_entry.get(e, 0) + 1
        print("  진입 조건     :", by_entry)

    print("=" * 60 + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Volume Trailing 백테스트")
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--balance", type=float, default=INITIAL_BALANCE)
    parser.add_argument("--csv", type=str, default="")
    args = parser.parse_args()

    if args.csv:
        df = pd.read_csv(args.csv)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
    else:
        print(f"Fetching {args.limit} × {TIMEFRAME} candles for {args.symbol}...")
        df = fetch_ohlcv(symbol=args.symbol, timeframe=TIMEFRAME, limit=args.limit)

    print(f"Data: {len(df)} rows")
    trades, equity, final_balance = run_backtest(df, initial_balance=args.balance)
    print_summary(trades, equity, args.balance, final_balance)

    if trades:
        print("최근 10건 거래:")
        for t in trades[-10:]:
            print(f"  {t['timestamp']} | {t['side']:5s} | {t['entry_type']:8s} | "
                  f"{t['exit_reason']:15s} | PnL: {t['pnl']:+.2f} | Bal: {t['balance_after']:.2f}")


if __name__ == "__main__":
    main()
