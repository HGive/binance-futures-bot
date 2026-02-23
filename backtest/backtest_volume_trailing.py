"""
돌파 진입 추세 추종 전략 백테스팅 (Breakout + ADX)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
타임프레임: 1시간봉

[진입 조건]
  - 종가가 직전 N봉 최고가 돌파 → Long
  - 종가가 직전 N봉 최저가 돌파 → Short
  - ADX > 20 (추세 존재 확인 - 횡보장 진입 방지)
  - SL 거리 > 3% 이면 진입 스킵 (리스크 필터)

[청산]
  - SL: entry ± ATR × 1.5 (진입 직후 고정)
  - 부분 익절: +5% 도달 시 25% 청산 + 본전 SL + 트레일링 활성
  - 트레일링 스탑: best_price ± ATR × 4.0
  - 타임스탑: 24봉(24시간) 후 수익 없으면 강제 청산

[수수료]
  - 바이낸스 테이커 기준 편도 0.04% → 왕복 0.08% 반영
"""
import sys
import math
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# === 타임프레임 ===
TIMEFRAME = "1h"
LEVERAGE = 3
ATR_PERIOD = 14

# === ADX 필터 ===
ADX_PERIOD = 14
ADX_MIN = 20              # ADX > 20 일 때만 진입

# === 돌파 진입 설정 ===
BREAKOUT_PERIOD = 20      # 직전 N봉 최고/최저가 돌파 기준
INITIAL_SL_ATR_MULT = 1.5 # 진입 후 SL = entry ± ATR × 1.5
MAX_SL_PCT = 0.03         # SL 거리 최대 3% (초과 시 스킵)

# === 공통 청산 ===
PARTIAL_TP_PCT = 0.05
PARTIAL_TP_RATIO = 0.25
TRAILING_STOP_ATR_MULT = 4.0
TIME_STOP_BARS = 24       # 24봉(24시간) 수익 없으면 강제 청산

# === 수수료 ===
COMMISSION_RATE = 0.0004  # 편도 0.04% (바이낸스 테이커)

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


def calc_adx_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ADX (Average Directional Index) 계산"""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    up_move = high - prev_high
    down_move = prev_low - low
    dm_plus = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    dm_minus = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    dm_plus_s = pd.Series(dm_plus, index=close.index)
    dm_minus_s = pd.Series(dm_minus, index=close.index)

    atr_s = tr.ewm(span=period, adjust=False).mean()
    di_plus = 100 * dm_plus_s.ewm(span=period, adjust=False).mean() / atr_s
    di_minus = 100 * dm_minus_s.ewm(span=period, adjust=False).mean() / atr_s

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx


def run_backtest(df: pd.DataFrame, initial_balance: float = INITIAL_BALANCE) -> tuple:
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if "timestamp" not in df.columns and df.index.name != "timestamp":
        df = df.reset_index()

    close = df["close"].astype(float)
    high_s = df["high"].astype(float)
    low_s = df["low"].astype(float)
    open_s = df["open"].astype(float)

    atr_series = calc_atr_series(high_s, low_s, close, ATR_PERIOD)
    adx_series = calc_adx_series(high_s, low_s, close, ADX_PERIOD)

    # 직전 N봉 최고/최저 (현재 봉 제외: shift(1) 후 rolling)
    breakout_high = high_s.shift(1).rolling(BREAKOUT_PERIOD).max()
    breakout_low = low_s.shift(1).rolling(BREAKOUT_PERIOD).min()

    balance = initial_balance
    position = None
    trades = []
    equity = [initial_balance]

    min_bars = max(ATR_PERIOD * 2, BREAKOUT_PERIOD + 1)

    for i in range(min_bars, len(df)):
        row = df.iloc[i]
        ts = row.get("timestamp", df.index[i])
        h = high_s.iloc[i]
        l = low_s.iloc[i]
        c = close.iloc[i]

        atr = atr_series.iloc[i]
        if pd.isna(atr) or atr <= 0:
            atr = c * 0.01

        adx = adx_series.iloc[i]
        bo_high = breakout_high.iloc[i]
        bo_low = breakout_low.iloc[i]

        buy_unit = calc_buy_unit(balance)

        # ===== 포지션 관리 =====
        if position is not None:
            side = position["side"]
            entry_price = position["entry_price"]
            size = position["size"]
            sl_price = position["sl_price"]

            position["bars_open"] += 1

            # 트레일링 best_price 갱신
            if position["trailing_active"]:
                if side == "long":
                    position["best_price"] = max(position["best_price"], h)
                else:
                    position["best_price"] = min(position["best_price"], l)

            # 타임스탑: 24봉 경과 + 수익 없으면 강제 청산
            if not position["trailing_active"] and position["bars_open"] >= TIME_STOP_BARS:
                if (side == "long" and c <= entry_price) or \
                   (side == "short" and c >= entry_price):
                    pnl = size * (c - entry_price) if side == "long" else size * (entry_price - c)
                    pnl -= size * (entry_price + c) * COMMISSION_RATE
                    balance += pnl
                    trades.append({
                        "timestamp": ts, "side": side, "exit_reason": "TIME_STOP",
                        "entry_price": entry_price, "exit_price": c,
                        "pnl": pnl, "balance_after": balance,
                    })
                    position = None
                    equity.append(balance)
                    continue

            # 손절 체크
            if side == "long" and l <= sl_price:
                pnl = size * (sl_price - entry_price)
                pnl -= size * (entry_price + sl_price) * COMMISSION_RATE
                balance += pnl
                trades.append({
                    "timestamp": ts, "side": side, "exit_reason": "STOP_LOSS",
                    "entry_price": entry_price, "exit_price": sl_price,
                    "pnl": pnl, "balance_after": balance,
                })
                position = None
                equity.append(balance)
                continue
            elif side == "short" and h >= sl_price:
                pnl = size * (entry_price - sl_price)
                pnl -= size * (entry_price + sl_price) * COMMISSION_RATE
                balance += pnl
                trades.append({
                    "timestamp": ts, "side": side, "exit_reason": "STOP_LOSS",
                    "entry_price": entry_price, "exit_price": sl_price,
                    "pnl": pnl, "balance_after": balance,
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
                        pnl -= partial_size * (entry_price + partial_tp) * COMMISSION_RATE
                        balance += pnl
                        trades.append({
                            "timestamp": ts, "side": side, "exit_reason": "PARTIAL_TP",
                            "entry_price": entry_price, "exit_price": partial_tp,
                            "pnl": pnl, "balance_after": balance,
                        })
                        position["size"] = size - partial_size
                        position["partial_taken"] = True
                        position["trailing_active"] = True
                        position["best_price"] = h
                        position["sl_price"] = entry_price
                else:
                    partial_tp = entry_price * (1 - PARTIAL_TP_PCT)
                    if l <= partial_tp:
                        partial_size = size * PARTIAL_TP_RATIO
                        pnl = partial_size * (entry_price - partial_tp)
                        pnl -= partial_size * (entry_price + partial_tp) * COMMISSION_RATE
                        balance += pnl
                        trades.append({
                            "timestamp": ts, "side": side, "exit_reason": "PARTIAL_TP",
                            "entry_price": entry_price, "exit_price": partial_tp,
                            "pnl": pnl, "balance_after": balance,
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
                        pnl -= remaining_size * (entry_price + trail_sl) * COMMISSION_RATE
                        balance += pnl
                        trades.append({
                            "timestamp": ts, "side": side, "exit_reason": "TRAILING_STOP",
                            "entry_price": entry_price, "exit_price": trail_sl,
                            "pnl": pnl, "balance_after": balance,
                        })
                        position = None
                        equity.append(balance)
                        continue
                else:
                    trail_sl = position["best_price"] + trail_atr
                    if h >= trail_sl:
                        pnl = remaining_size * (entry_price - trail_sl)
                        pnl -= remaining_size * (entry_price + trail_sl) * COMMISSION_RATE
                        balance += pnl
                        trades.append({
                            "timestamp": ts, "side": side, "exit_reason": "TRAILING_STOP",
                            "entry_price": entry_price, "exit_price": trail_sl,
                            "pnl": pnl, "balance_after": balance,
                        })
                        position = None
                        equity.append(balance)
                        continue

            equity.append(balance)
            continue

        # ===== 진입 판단: 돌파 + ADX 필터 =====
        if balance < buy_unit:
            equity.append(balance)
            continue

        if pd.isna(adx) or adx < ADX_MIN:
            equity.append(balance)
            continue

        if pd.isna(bo_high) or pd.isna(bo_low):
            equity.append(balance)
            continue

        size = buy_unit * LEVERAGE / c

        if c > bo_high:
            # 상단 돌파 → Long
            sl_price = c - atr * INITIAL_SL_ATR_MULT
            if (c - sl_price) / c <= MAX_SL_PCT:
                position = {
                    "side": "long", "entry_price": c, "size": size,
                    "sl_price": sl_price, "partial_taken": False,
                    "trailing_active": False, "best_price": c, "bars_open": 0,
                }
        elif c < bo_low:
            # 하단 돌파 → Short
            sl_price = c + atr * INITIAL_SL_ATR_MULT
            if (sl_price - c) / c <= MAX_SL_PCT:
                position = {
                    "side": "short", "entry_price": c, "size": size,
                    "sl_price": sl_price, "partial_taken": False,
                    "trailing_active": False, "best_price": c, "bars_open": 0,
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
        pnl -= size * (entry_price + c) * COMMISSION_RATE
        balance += pnl
        trades.append({
            "timestamp": ts, "side": side, "exit_reason": "END_OF_DATA",
            "entry_price": entry_price, "exit_price": c,
            "pnl": pnl, "balance_after": balance,
        })

    return trades, pd.Series(equity), balance


def fetch_ohlcv(symbol: str = SYMBOL, timeframe: str = TIMEFRAME, limit: int = 500) -> pd.DataFrame:
    import ccxt
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def print_summary(trades: list, equity: pd.Series, initial: float, final: float, tf: str = TIMEFRAME):
    print("\n" + "=" * 60)
    print("  Breakout Trailing - 돌파 진입 추세 추종 전략")
    print("=" * 60)
    print(f"  타임프레임     : {tf}")
    print(f"  돌파 기준      : 직전 {BREAKOUT_PERIOD}봉 최고/최저가")
    print(f"  ADX 필터       : ADX > {ADX_MIN}")
    print(f"  초기 SL        : entry ± ATR × {INITIAL_SL_ATR_MULT} / 최대 {MAX_SL_PCT*100:.0f}%")
    print(f"  부분 익절      : +{PARTIAL_TP_PCT*100:.0f}% / {PARTIAL_TP_RATIO*100:.0f}% 청산")
    print(f"  트레일링 ATR   : ×{TRAILING_STOP_ATR_MULT}")
    print(f"  타임스탑       : {TIME_STOP_BARS}봉 수익 없으면 청산")
    print(f"  수수료         : 왕복 {COMMISSION_RATE*2*100:.2f}% 반영")
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
        by_reason = {}
        for t in trades:
            r = t["exit_reason"]
            by_reason[r] = by_reason.get(r, 0) + 1
        print("  청산 사유     :", by_reason)

    print("=" * 60 + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Breakout Trailing 백테스트")
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--timeframe", type=str, default=TIMEFRAME)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--balance", type=float, default=INITIAL_BALANCE)
    parser.add_argument("--csv", type=str, default="")
    args = parser.parse_args()

    tf = args.timeframe

    if args.csv:
        df = pd.read_csv(args.csv)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
    else:
        print(f"Fetching {args.limit} × {tf} candles for {args.symbol}...")
        df = fetch_ohlcv(symbol=args.symbol, timeframe=tf, limit=args.limit)

    print(f"Data: {len(df)} rows")
    trades, equity, final_balance = run_backtest(df, initial_balance=args.balance)
    print_summary(trades, equity, args.balance, final_balance, tf=tf)

    if trades:
        print("최근 10건 거래:")
        for t in trades[-10:]:
            print(f"  {t['timestamp']} | {t['side']:5s} | {t['exit_reason']:15s} | "
                  f"PnL: {t['pnl']:+.2f} | Bal: {t['balance_after']:.2f}")


if __name__ == "__main__":
    main()
