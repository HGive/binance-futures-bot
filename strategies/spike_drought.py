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
MIN_DAILY_QUOTE_VOL = 1e6    # 일 거래대금 하한. 500만→100만 으로 낮추자
                             # 거래 51→127건, 워크포워드 수익 +46%→+194%

# =====================================================================
#  진입 타이밍 — 조용하고 쌀 때
# =====================================================================
MIN_DROUGHT_DAYS = 90        # 마지막 슈팅 이후 최소 이만큼 조용할 것
MAX_PRICE_POS = 0.35         # 1년 고저 범위의 하위 35% 안에 있을 것
ATL_TOL = None               # 상장 이후 최저가 대비 +몇 % 이내에서만 살 것인가
                             # None 이면 이 조건을 쓰지 않는다
MIN_ATL_AGE = 0              # 그 최저가가 며칠 전에 만들어진 것이어야 하는가
                             # 0  → 신저가 갱신 중인 것도 허용 (= 떨어지는 칼)
                             # 90 → 예전에 찍은 바닥에 '다시 내려온' 것만 (= 지지선 재방문)

# =====================================================================
#  포지션 운용 — 현물 (spot)
#
#  왜 선물이 아니라 현물인가 (docs/backtest_spike_drought.md):
#    평균 보유가 35일이다. 선물이면 펀딩비가 17개월에 시드의 6.7%p 를 가져간다.
#    그리고 같은 금액을 넣는 한 3배·2배·1배의 손익은 완전히 동일했다.
#    즉 이 전략에서 레버리지가 주는 것은 수익이 아니라 '강제 손절선과 펀딩비'뿐이다.
#    사놓고 기다리는 전략에 선물을 쓸 이유가 없다.
# =====================================================================
LEVERAGE = 1                 # 현물
TICKER_MARGIN_PCT = 0.10     # 티커당 시드의 10% (STRATEGY_RULES.md 2절 상한)
MAX_CONCURRENT = 8           # 동시 8종목. 10~12칸으로 늘리면 하락장 노출이 커져 오히려 나빠진다
FIRST_ENTRY_FRAC = 1.0       # 물타기 없음 — 처음부터 전액
                             # 추매는 버렸다: 추매 직후에 손절선이 있으면 지는 쪽에만 돈을
                             # 두 배 넣게 되어 손실 증폭기가 된다 (docs/walkforward_results.md)
ADD_TRIGGER_ROI = -0.233     # (미사용, 참고용)
STOP_ROI_AFTER_ADD = -0.233  # (미사용, 참고용)
TAKE_PROFIT_ROI = 0.33       # (미사용) 목표는 target_gain() 으로 종목마다 따로 잡는다
DOUBLE_TP = 1.00             # 두 번째 목표: 가격 2배 (+100%)
SPLIT_AT_FIRST = 0.7         # 1차 목표에서 70% 매도, 남은 30% 는 2배까지 끌고 간다
                             # 전량 매도 대비: 총수익 +153%→+124% 로 낮아지지만
                             #   MDD -27.2%→-23.6%, PF 1.73→2.09, 수익구간 11→12/24,
                             #   탐색 미사용 구간 +18.9%→+31.3% 로 개선된다.
                             # 러너가 자본을 계속 굴려 동시보유도 1.95→3.15 종목으로 늘어난다.
REGIME_MA_DAYS = 100         # BTC 100일선 아래에서는 신규 진입 안 함
                             # 필터를 성적 보고 켜고 끄면 항상 늦는다. 상시 켜둔다.

# =====================================================================
#  비용
# =====================================================================
FEE_RATE = 0.0004
SLIPPAGE = 0.0005
FUNDING_PER_DAY = 0.0        # 현물은 펀딩비 없음
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


# =====================================================================
#  익절 목표 — 그 종목이 예전에 슈팅했을 때 오른 만큼의 절반
#
#  "이 종목은 터지면 보통 이만큼 오르더라" 를 과거 슈팅들에서 읽어내고,
#  이번엔 그 절반만 올라도 판다. 종목마다 다르고, 시점마다 갱신된다.
#  33% 같은 고정값을 모든 종목에 똑같이 쓰면 안 되는 이유다.
# =====================================================================
TP_FRACTION = 0.5            # 과거 슈팅 크기의 몇 배를 목표로 할 것인가
TP_MIN, TP_MAX = 0.08, 0.80  # 목표 상하한 (터무니없는 값 방지)
STOP_PCT = -0.20             # 손절 (진입가 대비). 고정이 맞다 — 아래 참고

# ---------------------------------------------------------------------
#  "들어갈 자리" — 목표가 그 종목의 평소 진폭 대비 어느 위치인가
#
#  reach = 최근 1년간 90일 고저 진폭의 중앙값 = "이 종목은 보통 이만큼 움직인다"
#  ratio = 목표 / reach
#
#  탐색 결과(2018~2023, 17,898건):
#     ratio 0.15 이하  승률 35%  기대값 -5.2%   ← 목표가 흔들림보다 작아 손절에 먼저 털림
#     ratio 0.25~0.30  승률 50%  기대값 +1.5%
#     ratio 0.30~0.40  승률 56%  기대값 +3.5%
#     ratio 0.60 이상  승률  3%  기대값 -18.2%  ← 목표가 도달 불가능한 거리
#
#  즉 자리에는 위아래 경계가 둘 다 있다. 같은 진입 규칙이라도 이 밴드 밖이면 들어가지 않는다.
#
#  손절을 진폭 비례로 바꿔보기도 했으나 전 조합 마이너스였다.
#  조용한 종목일수록 손절이 좁아져 평소 흔들림에 털리기 때문이다. 손절은 고정이 맞다.
# ---------------------------------------------------------------------
REACH_WINDOW = 90            # 진폭을 재는 구간
MIN_TGT_RATIO = 0.25         # 이보다 낮으면 목표가 그 종목 흔들림에 비해 작아 손절에 먼저 털린다
MAX_TGT_RATIO = 0.60         # 이보다 높으면 목표가 못 간다


def spike_gains(d: pd.DataFrame):
    """
    (확정일, 그 슈팅의 상승폭) 목록.
    상승폭 = 슈팅 직전 저점 대비 슈팅 구간 최고가.
    확정일 이후에만 쓸 수 있다 (미래 정보 차단).
    """
    hi, lo = d["high"].values, d["low"].values
    out, last = [], -SPIKE_CLUSTER_DAYS
    for t in range(SPIKE_BASE_DAYS, len(d) - RETRACE_DAYS):
        if t - last < SPIKE_CLUSTER_DAYS:
            continue
        base = lo[t - SPIKE_BASE_DAYS:t].min()
        if base <= 0:
            continue
        peak = hi[t:t + RETRACE_DAYS].max()
        gain = peak / base - 1
        if gain < SPIKE_MIN_PCT:
            continue
        if lo[t + 1:t + 1 + RETRACE_DAYS].min() > base * (1 + RETRACE_TOL):
            continue
        out.append((t + RETRACE_DAYS, gain))
        last = t
    return out


def raw_runups(d: pd.DataFrame) -> np.ndarray:
    """
    되돌림 여부와 무관하게 '급등이 있었던 날' — 그날 바로 알 수 있는 사실이다.

    슈팅 공백(drought)은 이걸로 재야 한다. 되돌림이 확인된 슈팅만 세면
    '아직 안 돌아온 최근 슈팅'을 없는 것으로 취급하게 되어 미래 정보가 샌다.
    """
    hi, lo = d["high"].values, d["low"].values
    n = len(d)
    out = np.zeros(n, dtype=bool)
    for t in range(SPIKE_BASE_DAYS, n):
        base = lo[t - SPIKE_BASE_DAYS:t].min()
        if base > 0 and hi[t] / base - 1 >= SPIKE_MIN_PCT:
            out[t] = True
    return out


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
    confirm = np.array([b for _, b in sp], dtype=int)      # 되돌림까지 확인된 슈팅 (이력 판정용)
    sg = spike_gains(d)
    sg_day = np.array([a for a, _ in sg], dtype=int)
    sg_val = np.array([g for _, g in sg], dtype=float)
    runup = raw_runups(d)                                   # 그날 바로 아는 급등 (공백 판정용)

    cnt = np.zeros(n, dtype=int)
    drought = np.full(n, 9999, dtype=int)
    last_ru = -10**9
    for i in range(n):
        if len(confirm):
            cnt[i] = int(((confirm <= i) & (confirm > i - YEAR_WINDOW)).sum())
        if runup[i]:
            last_ru = i
        if last_ru > -10**8:
            drought[i] = i - last_ru

    # 그 시점까지 확정된 슈팅들의 상승폭 중앙값 → 익절 목표의 재료
    tp_base = np.full(n, np.nan)
    for i in range(n):
        if len(sg_day):
            m = sg_day <= i
            if m.any():
                tp_base[i] = float(np.median(sg_val[m]))

    # 이 종목이 보통 얼마나 움직이는가 (90일 고저 진폭의 1년 중앙값)
    rmax = pd.Series(hi).rolling(REACH_WINDOW).max().values
    rmin = pd.Series(lo).rolling(REACH_WINDOW).min().values
    with np.errstate(invalid="ignore", divide="ignore"):
        swing = rmax / rmin - 1
    reach = pd.Series(swing).rolling(YEAR_WINDOW).median().values

    atl = np.minimum.accumulate(lo)          # 상장 이후 최저가 (그날까지의 사실)
    # 그 최저가가 언제 만들어졌는지 → 신저가 갱신 중인지, 옛 바닥에 다시 온 건지 구분
    atl_age = np.zeros(n, dtype=int)
    last_new = 0
    for k in range(n):
        if lo[k] <= atl[k]:
            last_new = k
        atl_age[k] = k - last_new

    roll_lo = pd.Series(lo).rolling(YEAR_WINDOW).min().values
    roll_hi = pd.Series(hi).rolling(YEAR_WINDOW).max().values
    with np.errstate(invalid="ignore", divide="ignore"):
        pos = (c - roll_lo) / (roll_hi - roll_lo)
    year_ago = np.full(n, np.nan)
    year_ago[YEAR_WINDOW:] = c[:-YEAR_WINDOW]
    return {"spike_cnt": cnt, "drought": drought, "pos": pos, "tp_base": tp_base,
            "reach": reach, "year_ago": year_ago, "atl": atl, "atl_age": atl_age,
            "qv30": d["qv"].rolling(30).median().values}


def tgt_ratio(pre: dict, i: int) -> float:
    """목표 ÷ 그 종목의 평소 진폭. 이 값이 '들어갈 자리'인지를 결정한다."""
    r = pre["reach"][i]
    if np.isnan(r) or r <= 0:
        return np.nan
    return target_gain(pre, i) / r


def target_gain(pre: dict, i: int) -> float:
    """이 종목의 이번 익절 목표 (진입가 대비 상승률)."""
    b = pre["tp_base"][i]
    if np.isnan(b):
        return TAKE_PROFIT_ROI
    return float(np.clip(b * TP_FRACTION, TP_MIN, TP_MAX))


def scan_ok(pre: dict, close_i: float, i: int) -> bool:
    """년단위로 봐서 이 종목이 대상인가 — 슈팅 이력 있고, 안 오른 종목."""
    if pre["spike_cnt"][i] < MIN_SPIKES:
        return False
    ya = pre["year_ago"][i]
    if np.isnan(ya) or close_i > ya * NOT_UP_MAX:
        return False
    q = pre["qv30"][i]
    return not (np.isnan(q) or q < MIN_DAILY_QUOTE_VOL)


def entry_signal(pre: dict, i: int, close_i: float = None) -> bool:
    """지금 들어갈 자리인가 — 조용해졌고, 가격도 낮고, 목표가 갈 만한 거리인가."""
    if pre["drought"][i] < MIN_DROUGHT_DAYS:
        return False
    p = pre["pos"][i]
    if np.isnan(p) or p > MAX_PRICE_POS:
        return False
    if ATL_TOL is not None and close_i is not None:
        a = pre["atl"][i]
        if not (a > 0 and close_i <= a * (1 + ATL_TOL)):
            return False    # 상장 이후 최저가 부근이 아니면 안 산다
    r = tgt_ratio(pre, i)
    return bool(not np.isnan(r) and MIN_TGT_RATIO <= r <= MAX_TGT_RATIO)


# --------------------------------------------------------------- 가격 산식
def add_trigger_price(entry: float) -> float:
    return entry * (1 + ADD_TRIGGER_ROI / LEVERAGE)


def take_profit_price(avg: float) -> float:
    return avg * (1 + TAKE_PROFIT_ROI / LEVERAGE)


def stop_price(avg: float) -> float:
    return avg * (1 + STOP_ROI_AFTER_ADD / LEVERAGE)


def liq_price(avg: float) -> float:
    return avg * (1 - 1 / LEVERAGE + MMR)
