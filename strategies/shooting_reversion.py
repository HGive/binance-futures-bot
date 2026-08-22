"""
shooting_reversion — 슈팅 되돌림 매집 전략

스펙: docs/shooting_reversion.md
규칙: STRATEGY_RULES.md

가끔 크게 슈팅했다 원래 자리로 되돌아오는 성질의 종목을,
눌린 자리에서 보수적으로 매수하고 다음 슈팅에서 판다.

STRATEGY_RULES.md 3.1에 따라 상수와 신호 판단 함수는 이 파일에만 정의한다.
백테스트는 여기서 import 한다. 복사 금지.
"""
import numpy as np
import pandas as pd

# =====================================================================
#  슈팅 종목 판정 (스캐너)
# =====================================================================
SPIKE_BASE_DAYS = 3          # 슈팅 직전 저점을 볼 구간
SPIKE_MIN_PCT = 0.35         # 직전 저점 대비 +35% 이상 → 슈팅
RETRACE_DAYS = 10            # 슈팅 후 되돌림을 볼 구간
RETRACE_TOL = 0.15           # 직전 저점 +15% 이내로 복귀하면 "되돌림 완료"
SPIKE_CLUSTER_DAYS = 5       # 같은 슈팅으로 볼 최소 간격
SPIKE_WINDOW_DAYS = 180      # 슈팅 이력을 셀 구간
MIN_SPIKES = 3               # 이 구간에 최소 몇 번 슈팅했는가
MIN_DAILY_QUOTE_VOL = 5e6    # 30일 중앙값 일 거래대금 하한 (USDT)

# =====================================================================
#  진입 조건 — 아주 보수적으로
#
#  in-sample 측정 결과 "눌린 자리"만으로는 기대값이 음수였다 (30일 후 중앙값 -15%).
#  떨어지는 칼을 잡기 때문이다. 그래서 두 개를 필수로 넣는다.
#    (1) 하락이 실제로 멈췄는가  — 최근 20일간 60일 신저가를 만들지 않았을 것
#    (2) 시장이 받쳐주는가       — BTC 가 50일선 위일 것
#  (2)를 빼면 같은 조건의 기대값이 +15.8% → -0.8% 로 무너진다.
# =====================================================================
BASE_LOOKBACK_DAYS = 90      # 바닥 기준 구간
HIGH_LOOKBACK_DAYS = 30      # 고점 기준 구간
ENTRY_MAX_ABOVE_BASE = 0.20  # 90일 저점 대비 +20% 이내에서만 산다
ENTRY_MAX_RUNUP = 0.15       # 최근 3일 상승률이 15% 넘으면 진입 안 함 (추격 금지)
LOW_WINDOW_DAYS = 60         # 신저가 판정 구간
NO_NEW_LOW_DAYS = 20         # 이 기간 동안 신저가를 만들지 않았어야 함
NEW_LOW_TOL = 0.02           # 신저가 판정 여유
BTC_MA_DAYS = 50             # 시장 레짐 판정 이동평균
STABILIZE_DAYS = 3           # (참고용) 단기 안정화 판정

# =====================================================================
#  포지션 운용
# =====================================================================
#  손절/추매는 ROI 가 아니라 '가격' 기준으로 잡는다.
#  ROI 기준으로 잡으면 레버리지를 바꿀 때마다 손절 위치가 같이 움직여
#  추매 지점과 손절 지점이 붙어버린다 (추매 직후 손절되는 구조).
LEVERAGE = 3                 # TP +33% = ROI +100%
TICKER_MARGIN_PCT = 0.05     # 티커당 총 증거금 = 시드의 5%
FIRST_ENTRY_FRAC = 0.5       # 1차 진입 = 티커 예산의 50% (아주 보수적으로)
ADD_TRIGGER_PCT = -0.15      # 진입가 대비 -15% 에서 딱 한 번 추매 (3배 → ROI -45%)
HARD_STOP_PCT = -0.25        # 진입가 대비 -25% 에서 손절 (추매 후 평단 대비 ROI 약 -60%)
TAKE_PROFIT_PCT = 0.33       # 평단 대비 +33% 에서 전량 청산 (3배 → ROI +100%)
MAX_CONCURRENT = 7           # 동시 보유 포지션 상한

# =====================================================================
#  거래 비용 / 거래소 상수  (STRATEGY_RULES.md 4.1)
# =====================================================================
FEE_RATE = 0.0004            # 편도 0.04% 테이커
SLIPPAGE = 0.0005            # 편도 0.05% (알트)
FUNDING_PER_DAY = 0.0003     # 8시간마다 0.01% → 하루 0.03%
MMR = 0.005                  # 유지증거금률 (근사)


# =====================================================================
#  지표 — 전부 "그 시점까지의 과거"만 사용 (미래 정보 금지)
# =====================================================================
def to_daily(df4h: pd.DataFrame) -> pd.DataFrame:
    """4시간봉 → 일봉."""
    d = df4h.set_index("dt").resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    d["quote_vol"] = d["volume"] * d["close"]
    return d.reset_index()


def find_spikes(d: pd.DataFrame) -> np.ndarray:
    """
    슈팅 이벤트를 찾아 '확정 시점 인덱스' 배열로 반환.

    슈팅   : 직전 SPIKE_BASE_DAYS 저점 대비 고가가 +SPIKE_MIN_PCT 이상
    되돌림 : 이후 RETRACE_DAYS 안에 그 저점 +RETRACE_TOL 이내로 복귀
    확정   : 슈팅일 + RETRACE_DAYS  ← 이 시점 이후에만 카운트에 쓴다 (미래 정보 차단)
    """
    high, low = d["high"].values, d["low"].values
    n = len(d)
    confirmed = []
    last = -SPIKE_CLUSTER_DAYS
    for t in range(SPIKE_BASE_DAYS, n - RETRACE_DAYS):
        if t - last < SPIKE_CLUSTER_DAYS:
            continue
        base = low[t - SPIKE_BASE_DAYS:t].min()
        if base <= 0:
            continue
        if high[t] / base - 1 < SPIKE_MIN_PCT:
            continue
        if low[t + 1:t + 1 + RETRACE_DAYS].min() > base * (1 + RETRACE_TOL):
            continue          # 되돌아오지 않음 → 이 전략 대상 아님
        confirmed.append(t + RETRACE_DAYS)
        last = t
    return np.array(confirmed, dtype=int)


def precompute(d: pd.DataFrame) -> dict:
    """심볼 1개에 대해 스캔/진입 판정에 쓸 배열을 미리 계산."""
    n = len(d)
    low, high, close = d["low"].values, d["high"].values, d["close"].values
    spikes = find_spikes(d)

    spike_cnt = np.zeros(n, dtype=int)
    if len(spikes):
        for i in range(n):
            lo = i - SPIKE_WINDOW_DAYS
            spike_cnt[i] = int(((spikes <= i) & (spikes > lo)).sum())

    base_low = pd.Series(low).rolling(BASE_LOOKBACK_DAYS).min().values
    hi_30 = pd.Series(high).rolling(HIGH_LOOKBACK_DAYS).max().values
    vol_med = d["quote_vol"].rolling(30).median().values
    runup3 = np.full(n, np.nan)
    runup3[3:] = close[3:] / close[:-3] - 1
    # 하락 정지: 최근 NO_NEW_LOW_DAYS 동안 LOW_WINDOW_DAYS 신저가를 만들지 않았는가
    low_s = pd.Series(low)
    low_win = low_s.rolling(LOW_WINDOW_DAYS).min().values
    low_recent = low_s.rolling(NO_NEW_LOW_DAYS).min().values
    with np.errstate(invalid="ignore"):
        downtrend_stopped = low_recent > low_win * (1 + NEW_LOW_TOL)
    # 최근 STABILIZE_DAYS 동안 신저가를 갱신하지 않았는가
    roll_min_prev = pd.Series(low).shift(1).rolling(STABILIZE_DAYS).min().values
    stabilized = np.zeros(n, dtype=bool)
    stabilized[1:] = low[1:] > roll_min_prev[1:]

    return {
        "spike_cnt": spike_cnt, "base_low": base_low, "hi_30": hi_30,
        "vol_med": vol_med, "runup3": runup3, "stabilized": stabilized,
        "downtrend_stopped": np.nan_to_num(downtrend_stopped, nan=0).astype(bool),
    }


def btc_regime(btc_daily: pd.DataFrame) -> dict:
    """BTC 일봉 → {날짜: 50일선 위인가}. 시장 레짐 필터."""
    ma = btc_daily["close"].rolling(BTC_MA_DAYS).mean()
    return dict(zip(btc_daily["dt"].values, (btc_daily["close"] > ma).values))


def scan_ok(pre: dict, i: int) -> bool:
    """i일 종가 기준, 이 종목이 '슈팅 성질'을 가졌는가."""
    if pre["spike_cnt"][i] < MIN_SPIKES:
        return False
    v = pre["vol_med"][i]
    return not (np.isnan(v) or v < MIN_DAILY_QUOTE_VOL)


def entry_signal(pre: dict, close_i: float, i: int, btc_up: bool) -> bool:
    """i일 종가 기준 진입 조건. 아주 보수적으로."""
    if not btc_up:
        return False                        # 시장 레짐 — 이게 없으면 기대값이 음수
    base, run = pre["base_low"][i], pre["runup3"][i]
    if np.isnan(base) or np.isnan(run) or base <= 0:
        return False
    if close_i > base * (1 + ENTRY_MAX_ABOVE_BASE):
        return False                        # 바닥에서 이미 많이 올라옴
    if run > ENTRY_MAX_RUNUP:
        return False                        # 이미 튀는 중 → 추격 금지
    return bool(pre["downtrend_stopped"][i])  # 하락이 멈췄는가


# =====================================================================
#  포지션 산식
# =====================================================================
def roi(avg_price: float, price: float, side: int = 1) -> float:
    """증거금 대비 손익률. side: 1=long"""
    return side * (price / avg_price - 1) * LEVERAGE


def liq_price(avg_price: float) -> float:
    """격리 롱 청산가 (근사)."""
    return avg_price * (1 - 1 / LEVERAGE + MMR)


def take_profit_price(avg_price: float) -> float:
    """평단 대비 목표가."""
    return avg_price * (1 + TAKE_PROFIT_PCT)


def add_trigger_price(entry_price: float) -> float:
    """최초 진입가 대비 추매 지점. 딱 한 번."""
    return entry_price * (1 + ADD_TRIGGER_PCT)


def hard_stop_price(entry_price: float) -> float:
    """최초 진입가 대비 손절가. 평단이 아니라 진입가 기준 (추매와 간격 확보)."""
    return entry_price * (1 + HARD_STOP_PCT)
