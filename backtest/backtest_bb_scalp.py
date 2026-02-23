"""
볼린저 밴드 스캘핑 전략 - 극한의 승률 추구
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
핵심 아이디어:
  가격은 어느 구간에서든 결국 일정 % 이상 움직인다.
  볼린저 밴드 이탈 = 과도하게 뻗은 상태 = 회귀 가능성 높음
  → 들어가자마자 +3%만 먹고 빠진다.

진입:
  - 종가 < 하단 밴드 → 롱 (과매도)
  - 종가 > 상단 밴드 → 숏 (과매수)

청산:
  - TP: +3% 고정 익절
  - SL: -6% 고정 손절 (2:1 비율, 67%+ 승률 필요)
  - 한 번에 하나의 포지션만
"""
import sys
import math
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# === 전략 상수 ===
TIMEFRAME = "15m"
LEVERAGE = 3

# 볼린저 밴드 설정
BB_PERIOD = 20
BB_STD = 2.0

# 익절/손절 (가격 기준 %)
TP_PCT = 0.03    # +3% 고정 익절
SL_PCT = 0.06    # -6% 고정 손절

# 포지션 사이징
POSITION_SIZE_PCT = 0.10
MIN_BUY_UNIT = 5
INITIAL_BALANCE = 1000.0
SYMBOL = "BTC/USDT:USDT"


def calc_buy_unit(total_balance: float) -> int:
    base_amount = total_balance * POSITION_SIZE_PCT
    return max(math.floor(base_amount), MIN_BUY_UNIT)


def calc_bb_series(close: pd.Series, period: int = 20, std: float = 2.0):
    """볼린저 밴드 계산 (중앙선, 상단, 하단)"""
    mid = close.rolling(window=period).mean()
    sigma = close.rolling(window=period).std()
    upper = mid + std * sigma
    lower = mid - std * sigma
    return mid, upper, lower


def run_backtest(df: pd.DataFrame, initial_balance: float = INITIAL_BALANCE) -> tuple:
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if "timestamp" not in df.columns and df.index.name != "timestamp":
        df = df.reset_index()

    close = df["close"].astype(float)
    high_s = df["high"].astype(float)
    low_s = df["low"].astype(float)

    bb_mid, bb_upper, bb_lower = calc_bb_series(close, BB_PERIOD, BB_STD)

    balance = initial_balance
    position = None
    trades = []
    equity = [initial_balance]

    min_bars = BB_PERIOD + 1

    for i in range(min_bars, len(df)):
        row = df.iloc[i]
        ts = row.get("timestamp", df.index[i])
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])

        buy_unit = calc_buy_unit(balance)

        # ===== 포지션 관리 =====
        if position is not None:
            side = position["side"]
            entry_price = position["entry_price"]
            size = position["size"]
            tp_price = position["tp_price"]
            sl_price = position["sl_price"]

            if side == "long":
                # TP 체크 (고가가 TP 이상)
                if h >= tp_price:
                    pnl = size * (tp_price - entry_price)
                    balance += pnl
                    trades.append({
                        "timestamp": ts, "side": side, "exit_reason": "TAKE_PROFIT",
                        "entry_price": entry_price, "exit_price": tp_price,
                        "pnl": pnl, "balance_after": balance
                    })
                    position = None
                    equity.append(balance)
                    continue
                # SL 체크 (저가가 SL 이하)
                elif l <= sl_price:
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
            else:  # short
                # TP 체크 (저가가 TP 이하)
                if l <= tp_price:
                    pnl = size * (entry_price - tp_price)
                    balance += pnl
                    trades.append({
                        "timestamp": ts, "side": side, "exit_reason": "TAKE_PROFIT",
                        "entry_price": entry_price, "exit_price": tp_price,
                        "pnl": pnl, "balance_after": balance
                    })
                    position = None
                    equity.append(balance)
                    continue
                # SL 체크 (고가가 SL 이상)
                elif h >= sl_price:
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

            equity.append(balance)
            continue

        # ===== 진입 판단 =====
        if balance < buy_unit:
            equity.append(balance)
            continue

        prev_close = float(df.iloc[i - 1]["close"])
        lower = bb_lower.iloc[i]
        upper = bb_upper.iloc[i]

        # 볼린저 밴드 하단 이탈 → 롱 (과매도 평균 회귀)
        should_long = prev_close < lower
        # 볼린저 밴드 상단 이탈 → 숏 (과매수 평균 회귀)
        should_short = prev_close > upper

        if should_long:
            size = buy_unit * LEVERAGE / c
            position = {
                "side": "long",
                "entry_price": c,
                "size": size,
                "tp_price": c * (1 + TP_PCT),
                "sl_price": c * (1 - SL_PCT),
            }
        elif should_short:
            size = buy_unit * LEVERAGE / c
            position = {
                "side": "short",
                "entry_price": c,
                "size": size,
                "tp_price": c * (1 - TP_PCT),
                "sl_price": c * (1 + SL_PCT),
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
            "pnl": pnl, "balance_after": balance
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
    print("  BB 스캘핑 - 극한의 승률 전략")
    print("=" * 60)
    print(f"  볼린저 밴드    : {BB_PERIOD}기간 / {BB_STD}σ")
    print(f"  고정 TP        : +{TP_PCT*100:.0f}%")
    print(f"  고정 SL        : -{SL_PCT*100:.0f}%")
    print(f"  레버리지       : x{LEVERAGE}")
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
            avg_win = sum(wins) / len(wins)
            avg_loss = abs(sum(losses) / len(losses))
            rr = avg_win / avg_loss if avg_loss > 0 else float("inf")
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
    parser = argparse.ArgumentParser(description="BB 스캘핑 백테스트")
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--limit", type=int, default=1500)
    parser.add_argument("--balance", type=float, default=INITIAL_BALANCE)
    parser.add_argument("--csv", type=str, default="")
    args = parser.parse_args()

    if args.csv:
        df = pd.read_csv(args.csv)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
    else:
        print(f"Fetching {args.limit} x {TIMEFRAME} candles for {args.symbol}...")
        df = fetch_ohlcv(symbol=args.symbol, timeframe=TIMEFRAME, limit=args.limit)

    print(f"Data: {len(df)} rows")
    trades, equity, final_balance = run_backtest(df, initial_balance=args.balance)
    print_summary(trades, equity, args.balance, final_balance)

    if trades:
        print("최근 10건 거래:")
        for t in trades[-10:]:
            print(f"  {t['timestamp']} | {t['side']:5s} | {t['exit_reason']:12s} | PnL: {t['pnl']:+.2f} | Balance: {t['balance_after']:.2f}")


if __name__ == "__main__":
    main()
