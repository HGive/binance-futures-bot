"""Ross Cameron RSI + Bollinger Band 전략 백테스팅
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
아이디어:
  RSI를 1차 필터(과매도/과매수 구간 확인), 볼린저 밴드 + 장악형 캔들로
  실제 반전 시점을 포착하는 쌍바닥/쌍봉 패턴 전략.

[Long 진입 조건 - 쌍바닥]
  1. RSI < 30 + 음봉이 BB 하단을 터치/이탈
  2. 직전 음봉을 집어삼키는 첫 장악형 양봉 출현
  3. 두 번째 바닥(음봉, BB 하단 위에서 형성)  ← l_sec_seen
  4. 두 번째 바닥 직후 양봉 종가 → 진입

[Short 진입 조건 - 쌍봉]
  1. RSI > 70 + 양봉이 BB 상단을 터치/이탈
  2. 직전 양봉을 집어삼키는 첫 장악형 음봉 출현
  3. 두 번째 고점(양봉, BB 상단 아래에서 형성) ← s_sec_seen
  4. 두 번째 고점 직후 음봉 종가 → 진입

[청산]
  - Long  SL : BB 하단 / TP : R:R 1:2
  - Short SL : 직전 고점 / TP : R:R 1:1
  - 타임스탑 : 20봉 보유 후 강제 청산

[선택] REQUIRE_MACD = True 시
  - Long  : MACD 골든크로스 확인
  - Short : MACD 데드크로스 확인

[수수료]
  - 바이낸스 테이커 기준 편도 0.04% → 왕복 0.08%
"""
import sys
import math
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ═══════════════════════════════════════════════
# 상수
# ═══════════════════════════════════════════════
TIMEFRAME       = "1h"
LEVERAGE        = 3

RSI_PERIOD      = 14
RSI_OVERSOLD    = 30
RSI_OVERBOUGHT  = 70

BB_PERIOD       = 20
BB_STD          = 2.0

MACD_FAST       = 12
MACD_SLOW       = 26
MACD_SIGNAL_P   = 9

REQUIRE_MACD    = False   # True → MACD 골든/데드 크로스 필터 (전략 A)

# ── 전략 B: RSI 다이버전스 + MACD ──
ENABLE_DIVERGENCE   = True    # True → RSI 다이버전스 + MACD 진입 활성화
DIV_LOOKBACK        = 30      # 다이버전스 탐지 범위 (봉)
PIVOT_LEN           = 3       # 스윙 피봇 확인 길이 (좌우 N봉)
DIV_MIN_DIST        = 5       # 두 피봇 간 최소 거리 (봉)
MACD_CROSS_LOOKBACK = 3       # MACD 크로스 확인 범위 (봉)

LONG_RR         = 2.0     # Long  R:R  1:2
SHORT_RR        = 1.0     # Short R:R  1:1
MAX_SL_PCT      = 0.08    # SL 거리 > 8% → 스킵
MAX_SETUP_BARS  = 15      # 패턴 완성 최대 허용 봉
TIME_STOP_BARS  = 20      # 포지션 최대 보유 봉

COMMISSION_RATE = 0.0004  # 편도 0.04%
POSITION_SIZE_PCT = 0.10
MIN_BUY_UNIT    = 5
INITIAL_BALANCE = 1000.0
SYMBOL          = "BTC/USDT:USDT"


# ═══════════════════════════════════════════════
# 지표 계산
# ═══════════════════════════════════════════════
def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag = gain.ewm(span=period, adjust=False).mean()
    al = loss.ewm(span=period, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def calc_bb(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    """(upper, mid, lower) 반환"""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid + std_mult * std, mid, mid - std_mult * std


def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    """(macd_line, signal_line) 반환"""
    macd   = close.ewm(span=fast, adjust=False).mean() \
           - close.ewm(span=slow, adjust=False).mean()
    signal = macd.ewm(span=sig, adjust=False).mean()
    return macd, signal


# ═══════════════════════════════════════════════
# 캔들 패턴 헬퍼
# ═══════════════════════════════════════════════
def bullish_engulf(o: float, c: float, o_p: float, c_p: float) -> bool:
    """현재 양봉이 이전 음봉을 완전히 장악"""
    return c_p < o_p and c > o and c > o_p and o < c_p


def bearish_engulf(o: float, c: float, o_p: float, c_p: float) -> bool:
    """현재 음봉이 이전 양봉을 완전히 장악"""
    return c_p > o_p and c < o and c < o_p and o > c_p


# ═══════════════════════════════════════════════
# RSI 다이버전스 탐지 (전략 B)
# ═══════════════════════════════════════════════
def _find_pivot_lows(low_arr, rsi_arr, end_idx,
                     lookback=DIV_LOOKBACK, pivot_len=PIVOT_LEN):
    """end_idx 이전에 확인된 스윙 저점 리스트 반환: [(idx, low, rsi), ...]"""
    start  = max(pivot_len, end_idx - lookback)
    end    = end_idx - pivot_len + 1          # pivot_len 봉 뒤까지 확인 필요
    pivots = []
    for j in range(start, end):
        low_j = low_arr[j]
        is_pivot = True
        for k in range(1, pivot_len + 1):
            if j - k < 0 or low_j >= low_arr[j - k] or low_j >= low_arr[j + k]:
                is_pivot = False
                break
        if is_pivot:
            pivots.append((j, low_j, rsi_arr[j]))
    return pivots


def _find_pivot_highs(high_arr, rsi_arr, end_idx,
                      lookback=DIV_LOOKBACK, pivot_len=PIVOT_LEN):
    """end_idx 이전에 확인된 스윙 고점 리스트 반환: [(idx, high, rsi), ...]"""
    start  = max(pivot_len, end_idx - lookback)
    end    = end_idx - pivot_len + 1
    pivots = []
    for j in range(start, end):
        hi_j = high_arr[j]
        is_pivot = True
        for k in range(1, pivot_len + 1):
            if j - k < 0 or hi_j <= high_arr[j - k] or hi_j <= high_arr[j + k]:
                is_pivot = False
                break
        if is_pivot:
            pivots.append((j, hi_j, rsi_arr[j]))
    return pivots


def check_bullish_divergence(low_arr, rsi_arr, i,
                             lookback=DIV_LOOKBACK, pivot_len=PIVOT_LEN,
                             min_dist=DIV_MIN_DIST):
    """상승 다이버전스: 가격 lower low, RSI higher low → (True, 전저점) or (False, 0)"""
    pivots = _find_pivot_lows(low_arr, rsi_arr, i, lookback, pivot_len)
    if len(pivots) < 2:
        return False, 0.0
    p1, p2 = pivots[-2], pivots[-1]
    if p2[0] - p1[0] < min_dist:
        return False, 0.0
    if p2[1] < p1[1] and p2[2] > p1[2]:      # price ↓ RSI ↑
        return True, p2[1]                     # SL = 전저점
    return False, 0.0


def check_bearish_divergence(high_arr, rsi_arr, i,
                              lookback=DIV_LOOKBACK, pivot_len=PIVOT_LEN,
                              min_dist=DIV_MIN_DIST):
    """하락 다이버전스: 가격 higher high, RSI lower high → (True, 직전고점) or (False, 0)"""
    pivots = _find_pivot_highs(high_arr, rsi_arr, i, lookback, pivot_len)
    if len(pivots) < 2:
        return False, 0.0
    p1, p2 = pivots[-2], pivots[-1]
    if p2[0] - p1[0] < min_dist:
        return False, 0.0
    if p2[1] > p1[1] and p2[2] < p1[2]:      # price ↑ RSI ↓
        return True, p2[1]                     # SL = 직전 고점
    return False, 0.0


def check_recent_macd_cross(macd_arr, sig_arr, i, direction="golden",
                             lookback=MACD_CROSS_LOOKBACK):
    """최근 lookback 봉 이내에 MACD 골든/데드 크로스 발생 여부"""
    for j in range(max(1, i - lookback + 1), i + 1):
        if direction == "golden":
            if macd_arr[j] > sig_arr[j] and macd_arr[j - 1] <= sig_arr[j - 1]:
                return True
        else:  # dead
            if macd_arr[j] < sig_arr[j] and macd_arr[j - 1] >= sig_arr[j - 1]:
                return True
    return False


# ═══════════════════════════════════════════════
# 백테스트 메인
# ═══════════════════════════════════════════════
def run_backtest(df: pd.DataFrame, initial_balance: float = INITIAL_BALANCE) -> tuple:
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]
    if "timestamp" not in df.columns:
        df = df.reset_index()

    close_s = df["close"].astype(float)
    high_s  = df["high"].astype(float)
    low_s   = df["low"].astype(float)
    open_s  = df["open"].astype(float)

    rsi_s             = calc_rsi(close_s, RSI_PERIOD)
    bb_upper_s, _, bb_lower_s = calc_bb(close_s, BB_PERIOD, BB_STD)
    macd_line_s, macd_sig_s   = calc_macd(close_s, MACD_FAST, MACD_SLOW, MACD_SIGNAL_P)

    # numpy 배열 (다이버전스 탐지용 — iloc 보다 빠름)
    low_arr  = low_s.values
    high_arr = high_s.values
    rsi_arr  = rsi_s.values
    macd_arr = macd_line_s.values
    msig_arr = macd_sig_s.values

    balance  = initial_balance
    position = None
    trades   = []
    equity   = [initial_balance]

    # ── Long 패턴 상태 (쌍바닥)
    # 0=idle, 1=first_bottom_seen, 2=engulf_seen
    ls          = 0
    ls_bar      = 0
    l_first_low = 0.0
    l_engulf_h  = 0.0
    l_sec_seen  = False   # 두 번째 바닥 음봉(BB 하단 위) 확인 여부

    # ── Short 패턴 상태 (쌍봉)
    ss          = 0
    ss_bar      = 0
    s_first_hi  = 0.0
    s_sl_ref    = 0.0    # SL = 직전 고점
    s_sec_seen  = False  # 두 번째 고점 양봉(BB 상단 아래) 확인 여부

    min_bars = max(BB_PERIOD, MACD_SLOW + MACD_SIGNAL_P, RSI_PERIOD) + 5

    for i in range(min_bars, len(df)):
        row = df.iloc[i]
        ts  = row.get("timestamp", df.index[i])

        o   = open_s.iloc[i];  o_p = open_s.iloc[i - 1]
        h   = high_s.iloc[i];  h_p = high_s.iloc[i - 1]
        l   = low_s.iloc[i]
        c   = close_s.iloc[i]; c_p = close_s.iloc[i - 1]

        rsi   = rsi_s.iloc[i]
        bb_u  = bb_upper_s.iloc[i]
        bb_l  = bb_lower_s.iloc[i]
        mc    = macd_line_s.iloc[i];  mc_p = macd_line_s.iloc[i - 1]
        ms    = macd_sig_s.iloc[i];   ms_p = macd_sig_s.iloc[i - 1]

        buy_unit = max(math.floor(balance * POSITION_SIZE_PCT), MIN_BUY_UNIT)

        # ══════════════════════════════════════
        # 포지션 관리
        # ══════════════════════════════════════
        if position is not None:
            side = position["side"]
            ep   = position["entry_price"]
            sz   = position["size"]
            sl   = position["sl_price"]
            tp   = position["tp_price"]
            position["bars_open"] += 1

            exit_price  = None
            exit_reason = None

            # 타임스탑
            if position["bars_open"] >= TIME_STOP_BARS:
                exit_price  = c
                exit_reason = "TIME_STOP"
            elif side == "long":
                if l <= sl:
                    exit_price, exit_reason = sl, "STOP_LOSS"
                elif h >= tp:
                    exit_price, exit_reason = tp, "TAKE_PROFIT"
            else:  # short
                if h >= sl:
                    exit_price, exit_reason = sl, "STOP_LOSS"
                elif l <= tp:
                    exit_price, exit_reason = tp, "TAKE_PROFIT"

            if exit_price is not None:
                pnl  = sz * ((ep - exit_price) if side == "short" else (exit_price - ep))
                pnl -= sz * (ep + exit_price) * COMMISSION_RATE
                balance += pnl
                trades.append({
                    "timestamp": ts, "side": side, "exit_reason": exit_reason,
                    "entry_price": ep, "exit_price": exit_price,
                    "pnl": pnl, "balance_after": balance,
                    "entry_type": position.get("entry_type", "BB"),
                })
                position = None

            equity.append(balance)
            continue

        # NaN 가드
        if pd.isna(rsi) or pd.isna(bb_u) or pd.isna(bb_l):
            equity.append(balance)
            continue

        entered = False  # 이번 바에서 진입 여부

        # ══════════════════════════════════════
        # LONG 패턴 상태 머신 (쌍바닥)
        # ══════════════════════════════════════
        if ls == 0:
            # 조건 1: RSI<30 + 음봉 + BB 하단 터치
            if rsi < RSI_OVERSOLD and c < o and l <= bb_l:
                ls = 1
                ls_bar = i
                l_first_low = l
                l_sec_seen  = False

        elif ls == 1:
            # 시간 초과 또는 RSI 회복 → 리셋
            if i - ls_bar > MAX_SETUP_BARS or rsi > 50:
                ls = 0
            # 추가 음봉이 BB 하단 갱신 → first bottom 업데이트
            elif c < o and l <= bb_l:
                l_first_low = min(l_first_low, l)
                ls_bar = i
            # 조건 2: 장악형 양봉 → State 2
            elif bullish_engulf(o, c, o_p, c_p):
                ls = 2
                ls_bar = i
                l_engulf_h = h
                l_sec_seen = False

        elif ls == 2:
            # 시간 초과
            if i - ls_bar > MAX_SETUP_BARS:
                ls = 0
            # BB 상단 돌파 → 패턴 실패
            elif h > bb_u:
                ls = 0
            # 두 번째 바닥이 BB 하단 아래로 이탈 → 패턴 실패
            elif l < bb_l:
                ls = 0
            else:
                # 조건 3: 두 번째 바닥 음봉 (BB 하단 위에서 형성)
                if c < o and l > bb_l:
                    l_sec_seen = True
                # 조건 4: 두 번째 바닥 직후 양봉 → 진입
                elif c > o and l > bb_l and l_sec_seen:
                    macd_ok = (mc > ms and mc_p <= ms_p) if REQUIRE_MACD else True
                    if macd_ok:
                        sl_p = bb_l
                        sl_d = c - sl_p
                        if 0 < sl_d / c <= MAX_SL_PCT and balance >= buy_unit:
                            tp_p = c + sl_d * LONG_RR
                            position = {
                                "side": "long", "entry_price": c,
                                "size": buy_unit * LEVERAGE / c,
                                "sl_price": sl_p, "tp_price": tp_p,
                                "bars_open": 0, "entry_type": "BB",
                            }
                            ls = 0
                            ss = 0  # 반대 패턴도 리셋
                            entered = True

        # ══════════════════════════════════════
        # SHORT 패턴 상태 머신 (쌍봉)
        # ══════════════════════════════════════
        if not entered:
            if ss == 0:
                # 조건 1: RSI>70 + 양봉 + BB 상단 터치
                if rsi > RSI_OVERBOUGHT and c > o and h >= bb_u:
                    ss = 1
                    ss_bar = i
                    s_first_hi = h
                    s_sl_ref   = h
                    s_sec_seen = False

            elif ss == 1:
                # 시간 초과 또는 RSI 하락 → 리셋
                if i - ss_bar > MAX_SETUP_BARS or rsi < 50:
                    ss = 0
                # 추가 양봉이 BB 상단 갱신 → first top 업데이트
                elif c > o and h >= bb_u:
                    s_first_hi = max(s_first_hi, h)
                    s_sl_ref   = s_first_hi
                    ss_bar = i
                # 조건 2: 장악형 음봉 → State 2
                elif bearish_engulf(o, c, o_p, c_p):
                    ss = 2
                    ss_bar = i
                    s_sl_ref   = max(s_first_hi, h_p)  # SL = 직전 고점
                    s_sec_seen = False

            elif ss == 2:
                # 시간 초과
                if i - ss_bar > MAX_SETUP_BARS:
                    ss = 0
                # BB 하단 이탈 → 패턴 실패
                elif l < bb_l:
                    ss = 0
                # 두 번째 고점이 BB 상단 위로 돌파 → 패턴 실패
                elif h > bb_u:
                    ss = 0
                else:
                    # 조건 3: 두 번째 고점 양봉 (BB 상단 아래에서 형성)
                    if c > o and h < bb_u:
                        s_sec_seen = True
                        s_sl_ref   = max(s_sl_ref, h)   # SL = 두 번째 고점 갱신
                    # 조건 4: 두 번째 고점 직후 음봉 → 진입
                    elif c < o and h < bb_u and s_sec_seen:
                        macd_ok = (mc < ms and mc_p >= ms_p) if REQUIRE_MACD else True
                        if macd_ok:
                            sl_p = s_sl_ref
                            sl_d = sl_p - c
                            if 0 < sl_d / c <= MAX_SL_PCT and balance >= buy_unit:
                                tp_p = c - sl_d * SHORT_RR
                                if tp_p > 0:
                                    position = {
                                        "side": "short", "entry_price": c,
                                        "size": buy_unit * LEVERAGE / c,
                                        "sl_price": sl_p, "tp_price": tp_p,
                                        "bars_open": 0, "entry_type": "BB",
                                    }
                                    ss = 0
                                    ls = 0  # 반대 패턴도 리셋

        # ══════════════════════════════════════
        # 전략 B: RSI 다이버전스 + MACD + 장악형 캔들
        # ══════════════════════════════════════
        if not entered and position is None and ENABLE_DIVERGENCE:
            # Long: 상승 다이버전스 + MACD 골든크로스 + 장악형 양봉
            if bullish_engulf(o, c, o_p, c_p):
                has_div, div_low = check_bullish_divergence(
                    low_arr, rsi_arr, i, DIV_LOOKBACK, PIVOT_LEN, DIV_MIN_DIST)
                if has_div and check_recent_macd_cross(
                        macd_arr, msig_arr, i, "golden", MACD_CROSS_LOOKBACK):
                    sl_p = div_low          # SL = 전저점
                    sl_d = c - sl_p
                    if 0 < sl_d / c <= MAX_SL_PCT and balance >= buy_unit:
                        tp_p = c + sl_d * LONG_RR
                        position = {
                            "side": "long", "entry_price": c,
                            "size": buy_unit * LEVERAGE / c,
                            "sl_price": sl_p, "tp_price": tp_p,
                            "bars_open": 0, "entry_type": "DIV",
                        }
                        ls = 0; ss = 0
                        entered = True

            # Short: 하락 다이버전스 + MACD 데드크로스 + 장악형 음봉
            if not entered and bearish_engulf(o, c, o_p, c_p):
                has_div, div_high = check_bearish_divergence(
                    high_arr, rsi_arr, i, DIV_LOOKBACK, PIVOT_LEN, DIV_MIN_DIST)
                if has_div and check_recent_macd_cross(
                        macd_arr, msig_arr, i, "dead", MACD_CROSS_LOOKBACK):
                    sl_p = div_high         # SL = 직전 고점
                    sl_d = sl_p - c
                    if 0 < sl_d / c <= MAX_SL_PCT and balance >= buy_unit:
                        tp_p = c - sl_d * SHORT_RR
                        if tp_p > 0:
                            position = {
                                "side": "short", "entry_price": c,
                                "size": buy_unit * LEVERAGE / c,
                                "sl_price": sl_p, "tp_price": tp_p,
                                "bars_open": 0, "entry_type": "DIV",
                            }
                            ls = 0; ss = 0

        equity.append(balance)

    # 마지막 포지션 강제 청산
    if position is not None:
        c    = float(df.iloc[-1]["close"])
        ts   = df.iloc[-1].get("timestamp", df.index[-1])
        side = position["side"]
        ep   = position["entry_price"]
        sz   = position["size"]
        pnl  = sz * ((ep - c) if side == "short" else (c - ep))
        pnl -= sz * (ep + c) * COMMISSION_RATE
        balance += pnl
        trades.append({
            "timestamp": ts, "side": side, "exit_reason": "END_OF_DATA",
            "entry_price": ep, "exit_price": c,
            "pnl": pnl, "balance_after": balance,
            "entry_type": position.get("entry_type", "BB"),
        })

    return trades, pd.Series(equity), balance


# ═══════════════════════════════════════════════
# 데이터 수집
# ═══════════════════════════════════════════════
def fetch_ohlcv(symbol: str = SYMBOL, timeframe: str = TIMEFRAME, limit: int = 500) -> pd.DataFrame:
    import ccxt
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


# ═══════════════════════════════════════════════
# 결과 출력
# ═══════════════════════════════════════════════
def print_summary(trades: list, equity: pd.Series, initial: float, final: float,
                  symbol: str = "", tf: str = TIMEFRAME):
    print("\n" + "=" * 64)
    print("  Ross Cameron  RSI + BB 전략  (쌍바닥 / 쌍봉)")
    print("=" * 64)
    if symbol:
        print(f"  심볼           : {symbol}")
    print(f"  타임프레임     : {tf}")
    print(f"  RSI            : 과매도 < {RSI_OVERSOLD}  /  과매수 > {RSI_OVERBOUGHT}")
    print(f"  BB             : 기간 {BB_PERIOD}, {BB_STD}σ")
    print(f"  MACD 필터(A)   : {'ON (골든/데드 크로스 필요)' if REQUIRE_MACD else 'OFF'}")
    print(f"  다이버전스(B)  : {'ON (RSI div + MACD cross + engulf)' if ENABLE_DIVERGENCE else 'OFF'}")
    print(f"  Long  TP R:R   : 1:{LONG_RR:.1f}  |  Short TP R:R : 1:{SHORT_RR:.1f}")
    print(f"  최대 SL 거리   : {MAX_SL_PCT * 100:.0f}%")
    print(f"  패턴 최대 봉   : {MAX_SETUP_BARS}  |  타임스탑 : {TIME_STOP_BARS}봉")
    print(f"  수수료         : 왕복 {COMMISSION_RATE * 2 * 100:.2f}%")
    print("-" * 64)
    print(f"  봉 수          : {len(equity)}")
    print(f"  초기 잔고      : {initial:,.2f} USDT")
    print(f"  최종 잔고      : {final:,.2f} USDT")
    ret_pct = (final - initial) / initial * 100
    print(f"  수익률         : {ret_pct:+.2f}%")
    print(f"  총 거래 횟수   : {len(trades)}")

    if trades:
        pnls   = [t["pnl"] for t in trades]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        wr     = len(wins) / len(pnls) * 100 if pnls else 0
        print(f"  승률           : {wr:.1f}%  ({len(wins)}W / {len(losses)}L)")
        if wins:
            print(f"  평균 익절      : {sum(wins)/len(wins):+.2f} USDT")
        if losses:
            print(f"  평균 손절      : {sum(losses)/len(losses):+.2f} USDT")
        if wins and losses:
            rr = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
            print(f"  실현 R:R       : {rr:.2f}")
        eq  = pd.Series(equity)
        mdd = ((eq - eq.cummax()) / eq.cummax() * 100).min()
        print(f"  최대 낙폭(MDD) : {mdd:.2f}%")
        by_reason = {}
        for t in trades:
            r = t["exit_reason"]
            by_reason[r] = by_reason.get(r, 0) + 1
        print(f"  청산 사유      : {by_reason}")
        long_c  = sum(1 for t in trades if t["side"] == "long")
        short_c = sum(1 for t in trades if t["side"] == "short")
        print(f"  Long / Short   : {long_c} / {short_c}")
        bb_c  = sum(1 for t in trades if t.get("entry_type") == "BB")
        div_c = sum(1 for t in trades if t.get("entry_type") == "DIV")
        if div_c > 0:
            print(f"  BB패턴 / 다이버전스 : {bb_c} / {div_c}")
    print("=" * 64 + "\n")


# ═══════════════════════════════════════════════
# 엔트리 포인트
# ═══════════════════════════════════════════════
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ross Cameron RSI+BB 전략 백테스트")
    parser.add_argument("--symbol",    default=SYMBOL,            help="심볼 (기본: BTC/USDT:USDT)")
    parser.add_argument("--timeframe", default=TIMEFRAME,         help="타임프레임 (기본: 1h)")
    parser.add_argument("--limit",     type=int, default=500,     help="캔들 수 (기본: 500)")
    parser.add_argument("--balance",   type=float, default=INITIAL_BALANCE)
    parser.add_argument("--csv",       default="",                help="로컬 CSV 경로 (선택)")
    args = parser.parse_args()

    tf = args.timeframe

    if args.csv:
        df = pd.read_csv(args.csv)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
    else:
        print(f"Fetching {args.limit} × {tf} candles for {args.symbol} ...")
        df = fetch_ohlcv(symbol=args.symbol, timeframe=tf, limit=args.limit)

    print(f"Data: {len(df)} rows")
    trades, equity, final_balance = run_backtest(df, initial_balance=args.balance)
    print_summary(trades, equity, args.balance, final_balance, symbol=args.symbol, tf=tf)

    if trades:
        print("최근 10건 거래:")
        for t in trades[-10:]:
            xp = f"{t['exit_price']:.4f}" if t["exit_price"] is not None else "—"
            print(f"  {t['timestamp']} | {t['side']:5s} | {t['exit_reason']:12s} | "
                  f"EP: {t['entry_price']:.4f} → XP: {xp} | "
                  f"PnL: {t['pnl']:+.2f} | Bal: {t['balance_after']:.2f}")


if __name__ == "__main__":
    main()
