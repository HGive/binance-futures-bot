"""
Volume Contrarian 전략 백테스팅 v2 (볼륨 스파이크 역추세 개선)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
아이디어:
  볼륨 스파이크 직후 방향이 fake-out인 경우가 더 많다
  → 반대로 들어가되, 추세 방향과 일치하는 contrarian만 진입

[진입 조건]
  - 현재 거래량 > 직전 5봉 평균 × 2배
  - DI 방향 필터:
      상승추세(DI+ > DI-): 음봉 스파이크 → Long만 허용
      하락추세(DI- > DI+): 양봉 스파이크 → Short만 허용
      횡보(|DI+−DI-| ≤ 10): 양방향 모두 허용
  - TP 거리 > 5% 이면 진입 스킵

[청산]
  - 부분 익절 (50%): 신호봉 시가(open) → SL 본전 이동
  - 전체 익절 (나머지 50%): 신호봉 저가(Short) / 고가(Long)
  - SL: entry ± ATR × 2.5 (추세 지속 확인 시 손절)
  - 타임스탑: 8봉(8시간) 후 수익 없으면 강제 청산

[수수료]
  - 바이낸스 테이커 기준 편도 0.04% → 왕복 0.08% 반영

타임프레임: 1h (기본)
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

# === ADX / DI 필터 ===
ADX_PERIOD = 14
DI_DIFF_THRESHOLD = 10    # |DI+−DI-| > 10 이면 방향성 있다고 판단

# === Volume Spike 설정 ===
VOLUME_LOOKBACK = 5
VOLUME_MULT = 2.0
MAX_TP_PCT = 0.05          # 전체 TP 거리 > 5% 이면 스킵

# === 청산 ===
SL_ATR_MULT = 2.5
PARTIAL_TP_RATIO = 0.50    # 부분 익절 비율 (50%)
TIME_STOP_BARS = 8         # 8봉 후 수익 없으면 강제 청산

# === 수수료 ===
COMMISSION_RATE = 0.0004   # 편도 0.04% (바이낸스 테이커)

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
    tr = pd.concat([high - low,
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calc_di_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    """DI+, DI- 반환 (ADX 구성 요소) — 추세 방향 필터용"""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat([high - low,
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)

    up_move = high - prev_high
    down_move = prev_low - low
    dm_plus = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=close.index)
    dm_minus = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=close.index)

    atr_s = tr.ewm(span=period, adjust=False).mean()
    di_plus = 100 * dm_plus.ewm(span=period, adjust=False).mean() / atr_s
    di_minus = 100 * dm_minus.ewm(span=period, adjust=False).mean() / atr_s
    return di_plus, di_minus


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

    atr_series = calc_atr_series(high_s, low_s, close, ATR_PERIOD)
    di_plus_s, di_minus_s = calc_di_series(high_s, low_s, close, ADX_PERIOD)
    vol_avg = volume_s.shift(1).rolling(VOLUME_LOOKBACK).mean()

    balance = initial_balance
    position = None
    trades = []
    equity = [initial_balance]

    min_bars = max(ATR_PERIOD * 2, VOLUME_LOOKBACK + 1)

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

        di_plus = di_plus_s.iloc[i]
        di_minus = di_minus_s.iloc[i]

        buy_unit = calc_buy_unit(balance)

        # ===== 포지션 관리 =====
        if position is not None:
            side = position["side"]
            entry_price = position["entry_price"]
            size = position["size"]
            sl_price = position["sl_price"]
            full_tp = position["full_tp"]
            partial_tp = position["partial_tp"]

            position["bars_open"] += 1

            # ── 타임스탑 ──────────────────────────────────────────
            if position["bars_open"] >= TIME_STOP_BARS:
                if (side == "short" and c >= entry_price) or \
                   (side == "long" and c <= entry_price):
                    pnl = size * (entry_price - c) if side == "short" else size * (c - entry_price)
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

            if side == "short":
                # 부분 익절 (캔들 시가까지 되돌림 - 빠른 50%)
                if not position["partial_taken"] and l <= partial_tp:
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
                    position["sl_price"] = entry_price  # SL 본전 이동
                    # size 갱신 후 바로 Full TP / SL 체크
                    size = position["size"]
                    sl_price = position["sl_price"]

                # 전체 익절 (캔들 저가까지)
                if l <= full_tp:
                    pnl = size * (entry_price - full_tp)
                    pnl -= size * (entry_price + full_tp) * COMMISSION_RATE
                    balance += pnl
                    trades.append({
                        "timestamp": ts, "side": side, "exit_reason": "TAKE_PROFIT",
                        "entry_price": entry_price, "exit_price": full_tp,
                        "pnl": pnl, "balance_after": balance,
                    })
                    position = None
                    equity.append(balance)
                    continue
                # SL
                elif h >= sl_price:
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

            else:  # long
                # 부분 익절 (캔들 시가까지 되돌림)
                if not position["partial_taken"] and h >= partial_tp:
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
                    position["sl_price"] = entry_price  # SL 본전 이동
                    size = position["size"]
                    sl_price = position["sl_price"]

                # 전체 익절 (캔들 고가까지)
                if h >= full_tp:
                    pnl = size * (full_tp - entry_price)
                    pnl -= size * (entry_price + full_tp) * COMMISSION_RATE
                    balance += pnl
                    trades.append({
                        "timestamp": ts, "side": side, "exit_reason": "TAKE_PROFIT",
                        "entry_price": entry_price, "exit_price": full_tp,
                        "pnl": pnl, "balance_after": balance,
                    })
                    position = None
                    equity.append(balance)
                    continue
                # SL
                elif l <= sl_price:
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

            equity.append(balance)
            continue

        # ===== 진입 판단 =====
        if balance < buy_unit:
            equity.append(balance)
            continue

        v_avg = vol_avg.iloc[i]
        if pd.isna(v_avg) or v_avg <= 0 or vol <= v_avg * VOLUME_MULT:
            equity.append(balance)
            continue

        if pd.isna(di_plus) or pd.isna(di_minus):
            equity.append(balance)
            continue

        is_bullish = c > o
        is_bearish = c < o
        size = buy_unit * LEVERAGE / c

        # DI 방향 판단
        di_diff = di_plus - di_minus
        uptrend = di_diff > DI_DIFF_THRESHOLD    # DI+ 우세 → 상승 추세
        downtrend = di_diff < -DI_DIFF_THRESHOLD  # DI- 우세 → 하락 추세

        if is_bullish:
            # 양봉 스파이크 → Short
            # 상승추세에선 추세 방향 스파이크 → 진입 스킵
            if uptrend:
                equity.append(balance)
                continue
            full_tp = l       # 전체 TP: 캔들 저가
            partial_tp = o    # 부분 TP: 캔들 시가 (절반 먼저)
            sl_price = c + atr * SL_ATR_MULT
            tp_dist = (c - full_tp) / c
            if tp_dist <= MAX_TP_PCT and partial_tp < c:  # 시가가 종가보다 낮아야 의미있음
                position = {
                    "side": "short", "entry_price": c, "size": size,
                    "full_tp": full_tp, "partial_tp": partial_tp,
                    "sl_price": sl_price, "partial_taken": False, "bars_open": 0,
                }

        elif is_bearish:
            # 음봉 스파이크 → Long
            # 하락추세에선 추세 방향 스파이크 → 진입 스킵
            if downtrend:
                equity.append(balance)
                continue
            full_tp = h       # 전체 TP: 캔들 고가
            partial_tp = o    # 부분 TP: 캔들 시가
            sl_price = c - atr * SL_ATR_MULT
            tp_dist = (full_tp - c) / c
            if tp_dist <= MAX_TP_PCT and partial_tp > c:  # 시가가 종가보다 높아야 의미있음
                position = {
                    "side": "long", "entry_price": c, "size": size,
                    "full_tp": full_tp, "partial_tp": partial_tp,
                    "sl_price": sl_price, "partial_taken": False, "bars_open": 0,
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
        pnl = size * (entry_price - c) if side == "short" else size * (c - entry_price)
        pnl -= size * (entry_price + c) * COMMISSION_RATE
        balance += pnl
        trades.append({
            "timestamp": ts, "side": side, "exit_reason": "END_OF_DATA",
            "entry_price": entry_price, "exit_price": c,
            "pnl": pnl, "balance_after": balance,
        })

    return trades, pd.Series(equity), balance


def fetch_ohlcv(symbol: str = SYMBOL, timeframe: str = TIMEFRAME, limit: int = 300) -> pd.DataFrame:
    import ccxt
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def print_summary(trades: list, equity: pd.Series, initial: float, final: float, tf: str = TIMEFRAME):
    print("\n" + "=" * 60)
    print("  Volume Contrarian v2 - 볼륨 스파이크 역추세 전략")
    print("=" * 60)
    print(f"  타임프레임     : {tf}")
    print(f"  Volume 조건    : 직전 {VOLUME_LOOKBACK}봉 평균 × {VOLUME_MULT}배")
    print(f"  DI 방향 필터   : |DI+−DI-| > {DI_DIFF_THRESHOLD} 시 추세 방향 스파이크 스킵")
    print(f"  부분 익절      : 시가(open)에서 {PARTIAL_TP_RATIO*100:.0f}% → SL 본전이동")
    print(f"  전체 익절      : 신호봉 저가/고가, 최대 {MAX_TP_PCT*100:.0f}%")
    print(f"  SL             : entry ± ATR × {SL_ATR_MULT}")
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
    parser = argparse.ArgumentParser(description="Volume Contrarian v2 백테스트")
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--timeframe", type=str, default=TIMEFRAME)
    parser.add_argument("--limit", type=int, default=300)
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
            print(f"  {t['timestamp']} | {t['side']:5s} | {t['exit_reason']:12s} | "
                  f"PnL: {t['pnl']:+.2f} | Bal: {t['balance_after']:.2f}")


if __name__ == "__main__":
    main()
