"""
spike_drought — 슈팅 이력은 있는데 한동안 조용한 종목을 미리 담아두기 (롱 전용)

손매매로 실제 수익을 내고 있는 방식을 그대로 옮긴 것.

  "년단위로 봤을 때 하락세거나 쭉 횡보인데, 그 중에 슈팅을 몇 번 한 종목.
   최근에 슈팅이 없었고 가격도 상대적으로 낮으면 일단 조금 진입.
   더 내려가면 추매하거나 초기화해서 계속 슈팅을 기다린다."

왜 '최근 슈팅이 없어야' 하는가 (in-sample 측정, docs/backtest_spike_drought.md):
  같은 종목이라도 마지막 슈팅 이후 경과일에 따라 결과가 갈린다.
    0~60일   → +33% 먼저 도달 40%
    60~120일 → 37%
    120~240일→ 58%
    240일+   → 64%
  슈팅 직후에 들어가면 진다. 조용해진 뒤에 들어가야 한다.

STRATEGY_RULES.md 3.1 — 상수/신호 함수는 이 파일이 유일한 출처.
"""
import numpy as np
import pandas as pd

# =====================================================================
#  슈팅 정의
# =====================================================================
SPIKE_BASE_DAYS = 3          # 슈팅 직전 저점 구간
SPIKE_MIN_PCT = 0.35         # 직전 저점 대비 +35% 이상
RETRACE_DAYS = 10            # 되돌림 관찰 구간
RETRACE_TOL = 0.15           # 저점 +15% 이내 복귀 → 되돌림 완료
SPIKE_CLUSTER_DAYS = 5       # 같은 슈팅으로 볼 최소 간격

# =====================================================================
#  종목 선정 — 년단위 시야
# =====================================================================
YEAR_WINDOW = 365            # 모든 판단은 1년 창 기준
MIN_SPIKES = 2               # 1년 안에 슈팅 2회 이상
NOT_UP_MAX = 1.10            # 1년 전 대비 +10% 초과 상승한 종목은 제외 (하락/횡보만)
MIN_DAILY_QUOTE_VOL = 5e6

# =====================================================================
#  진입 타이밍 — 조용하고 쌀 때
# =====================================================================
MIN_DROUGHT_DAYS = 120       # 마지막 슈팅 이후 최소 이만큼 조용할 것
MAX_PRICE_POS = 0.25         # 1년 고저 범위의 하위 25% 안에 있을 것

# =====================================================================
#  포지션 운용
# =====================================================================
LEVERAGE = 3
TICKER_MARGIN_PCT = 0.04     # 티커당 총 증거금 = 시드의 4%
FIRST_ENTRY_FRAC = 0.5       # 1차는 절반만 (아주 보수적으로)
ADD_TRIGGER_ROI = -0.70      # ROI -70% (3배 → 가격 -23.3%) 에서 딱 한 번 추매
STOP_ROI_AFTER_ADD = -0.70   # 추매 후 평단 대비 ROI -70% → 정리
TAKE_PROFIT_ROI = 1.00       # ROI +100% (3배 → 평단 +33%) 에서 전량 청산
MAX_CONCURRENT = 7

# =====================================================================
#  비용
# =====================================================================
FEE_RATE = 0.0004
SLIPPAGE = 0.0005
FUNDING_PER_DAY = 0.0003
MMR = 0.005


def to_daily(df4h: pd.DataFrame) -> pd.DataFrame:
    if "dt" not in df4h:
        df4h = df4h.copy()
        df4h["dt"] = pd.to_datetime(df4h["timestamp"], unit="ms", utc=True)
    d = df4h.set_index("dt").resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    d["qv"] = d["volume"] * d["close"]
    return d.reset_index()


def find_spikes(d: pd.DataFrame):
    """(슈팅일, 확정일) 목록. 확정일 = 슈팅일 + RETRACE_DAYS (미래 정보 차단)."""
    hi, lo = d["high"].values, d["low"].values
    out, last = [], -SPIKE_CLUSTER_DAYS
    for t in range(SPIKE_BASE_DAYS, len(d) - RETRACE_DAYS):
        if t - last < SPIKE_CLUSTER_DAYS:
            continue
        base = lo[t - SPIKE_BASE_DAYS:t].min()
        if base <= 0 or hi[t] / base - 1 < SPIKE_MIN_PCT:
            continue
        if lo[t + 1:t + 1 + RETRACE_DAYS].min() > base * (1 + RETRACE_TOL):
            continue
        out.append((t, t + RETRACE_DAYS))
        last = t
    return out


def precompute(d: pd.DataFrame) -> dict:
    n = len(d)
    c, hi, lo = d["close"].values, d["high"].values, d["low"].values
    sp = find_spikes(d)
    spike_day = np.array([a for a, _ in sp], dtype=int)
    confirm = np.array([b for _, b in sp], dtype=int)

    cnt = np.zeros(n, dtype=int)
    drought = np.full(n, 9999, dtype=int)
    for i in range(n):
        if len(confirm):
            cnt[i] = int(((confirm <= i) & (confirm > i - YEAR_WINDOW)).sum())
        past = spike_day[spike_day <= i] if len(spike_day) else np.array([], dtype=int)
        if len(past):
            drought[i] = i - int(past[-1])

    roll_lo = pd.Series(lo).rolling(YEAR_WINDOW).min().values
    roll_hi = pd.Series(hi).rolling(YEAR_WINDOW).max().values
    with np.errstate(invalid="ignore", divide="ignore"):
        pos = (c - roll_lo) / (roll_hi - roll_lo)
    year_ago = np.full(n, np.nan)
    year_ago[YEAR_WINDOW:] = c[:-YEAR_WINDOW]
    return {"spike_cnt": cnt, "drought": drought, "pos": pos,
            "year_ago": year_ago, "qv30": d["qv"].rolling(30).median().values}


def scan_ok(pre: dict, close_i: float, i: int) -> bool:
    """년단위로 봐서 이 종목이 대상인가 — 슈팅 이력 있고, 안 오른 종목."""
    if pre["spike_cnt"][i] < MIN_SPIKES:
        return False
    ya = pre["year_ago"][i]
    if np.isnan(ya) or close_i > ya * NOT_UP_MAX:
        return False
    q = pre["qv30"][i]
    return not (np.isnan(q) or q < MIN_DAILY_QUOTE_VOL)


def entry_signal(pre: dict, i: int) -> bool:
    """지금 들어갈 때인가 — 조용해졌고, 가격도 낮은가."""
    if pre["drought"][i] < MIN_DROUGHT_DAYS:
        return False
    p = pre["pos"][i]
    return bool(not np.isnan(p) and p <= MAX_PRICE_POS)


# --------------------------------------------------------------- 가격 산식
def add_trigger_price(entry: float) -> float:
    return entry * (1 + ADD_TRIGGER_ROI / LEVERAGE)


def take_profit_price(avg: float) -> float:
    return avg * (1 + TAKE_PROFIT_ROI / LEVERAGE)


def stop_price(avg: float) -> float:
    return avg * (1 + STOP_ROI_AFTER_ADD / LEVERAGE)


def liq_price(avg: float) -> float:
    return avg * (1 - 1 / LEVERAGE + MMR)
