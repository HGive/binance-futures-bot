"""
spike_fade — 슈팅 페이드 (숏)

배경:
  "갑자기 훅 올랐다가 바로 내려가는 종목이 있다"는 관찰에서 출발했다.
  같은 관찰의 롱 버전(눌린 자리에서 사서 다음 슈팅을 기다림)은 in-sample 2,000일·221종에서
  기대값이 0 부근이었다 (docs/backtest_shooting_reversion.md). 이 종목들은 바닥이 계속
  내려가기 때문에 '기다리는' 쪽에 서면 시간이 적이 된다.

  반면 관찰의 나머지 절반 — 슈팅 후 '되돌아온다' — 은 표본 2,073건에서 일관되게
  양의 기대값을 보였다. 그래서 슈팅을 사는 대신 슈팅을 판다.

  원칙은 그대로다: 과열된 자리에서 팔고, 되돌아오면 욕심부리지 않고 덮는다.
  (STRATEGY_RULES.md 1.1의 거울상)

STRATEGY_RULES.md 3.1 — 상수와 신호 함수는 이 파일이 유일한 출처다.
"""
import numpy as np
import pandas as pd

# =====================================================================
#  진입 신호 — 슈팅 감지
# =====================================================================
SPIKE_BASE_DAYS = 3          # 슈팅 직전 저점을 볼 구간
SPIKE_MIN_PCT = 0.35         # 직전 저점 대비 +35% 이상 급등 → 슈팅
MIN_DAILY_QUOTE_VOL = 5e6    # 30일 중앙값 일 거래대금 하한 (USDT)
BTC_MA_DAYS = 50             # 시장 레짐 필터
REQUIRE_BTC_UP = True        # BTC 50일선 위에서만 진입 (in-sample: +2.4% vs +0.3%)
COOLDOWN_DAYS = 5            # 같은 심볼 재진입 최소 간격

# =====================================================================
#  포지션 운용 — 숏, 단일 진입 (추매 없음)
# =====================================================================
SIDE = -1                    # 숏
LEVERAGE = 3                 # SL +25% vs 청산 +32.8% → 손절이 앞. 갭상승은 시가 체결로 처리
TICKER_MARGIN_PCT = 0.03     # 티커당 증거금 = 시드의 3% (최대 노출 21%)
TAKE_PROFIT_PCT = 0.12       # 진입가 대비 -12% 에서 덮는다 (ROI +36%)
HARD_STOP_PCT = 0.25         # 진입가 대비 +25% 에서 손절 (ROI -75%, 청산 +32.8%)
MAX_HOLD_DAYS = 30           # 30일 안에 안 빠지면 논지 소멸 → 정리
MAX_CONCURRENT = 7

# 추매를 넣지 않는 이유: 오르는 숏에 물타는 것은 손실이 무한대로 열린 방향으로
# 노출을 키우는 행위다. 롱의 추매와 대칭이 아니다.

# =====================================================================
#  비용 (STRATEGY_RULES.md 4.1)
# =====================================================================
FEE_RATE = 0.0004
SLIPPAGE = 0.0005
FUNDING_PER_DAY = 0.0        # 숏은 대개 펀딩을 '받는' 쪽 → 0 으로 보수적 처리
MMR = 0.005


def to_daily(df4h: pd.DataFrame) -> pd.DataFrame:
    d = df4h.set_index("dt").resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    d["quote_vol"] = d["volume"] * d["close"]
    return d.reset_index()


def precompute(d: pd.DataFrame) -> dict:
    low, high = d["low"].values, d["high"].values
    n = len(d)
    base = pd.Series(low).shift(1).rolling(SPIKE_BASE_DAYS).min().values
    with np.errstate(invalid="ignore", divide="ignore"):
        runup = high / base - 1
    return {
        "runup": runup,
        "vol_med": d["quote_vol"].rolling(30).median().values,
    }


def btc_regime(btc_daily: pd.DataFrame) -> dict:
    ma = btc_daily["close"].rolling(BTC_MA_DAYS).mean()
    return dict(zip(btc_daily["dt"].values, (btc_daily["close"] > ma).values))


def entry_signal(pre: dict, i: int, btc_up: bool) -> bool:
    """i일 종가 기준. 오늘 슈팅이 나왔는가."""
    if REQUIRE_BTC_UP and not btc_up:
        return False
    v = pre["vol_med"][i]
    if np.isnan(v) or v < MIN_DAILY_QUOTE_VOL:
        return False
    r = pre["runup"][i]
    return bool(not np.isnan(r) and r >= SPIKE_MIN_PCT)


# =====================================================================
#  가격 산식 (숏)
# =====================================================================
def take_profit_price(entry: float) -> float:
    return entry * (1 - TAKE_PROFIT_PCT)


def hard_stop_price(entry: float) -> float:
    return entry * (1 + HARD_STOP_PCT)


def liq_price(entry: float) -> float:
    """격리 숏 청산가 (근사)."""
    return entry * (1 + 1 / LEVERAGE - MMR)


def roi(entry: float, price: float) -> float:
    return (entry / price - 1) * LEVERAGE if price > 0 else -1.0
