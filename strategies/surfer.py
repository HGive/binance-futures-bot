"""
SURFER (파도타기) v2.0 — trailing_atr 의 후속. 롱 전용 현물.

  상승 추세 중에 잠깐 과매도로 눌린 자리를 사서,
  오르면 일부 익절하고 나머지는 트레일링으로 끝까지 끌고 간다.

이름 그대로 파도가 와야 탄다. 아래 '한계' 를 반드시 읽을 것.

────────────────────────────────────────────────────────────
원본(trailing_atr v1) 이 왜 졌는가 — 측정치
────────────────────────────────────────────────────────────
v1: 15분봉 / 손절 ATR×2.0 / 익절 5% / 레버리지 3배 / 롱숏
  워크포워드 12구간 수익구간 0~2, 총수익 -100%, 파라미터 64조합 전부 마이너스.
  포지션 승률 25% — 4개 중 3개가 익절 근처도 못 가고 손절.
  수수료를 0 으로 놔도 -93.5% 였다. 신호가 아니라 설계가 문제였다.

바꾼 것과 그 근거 (현물 일봉 8년 507종, 연속 7년):

  ① 봉을 키웠다 (15분 → 일봉)
     15분 PF 0.68~0.85 / 1시간 0.82~0.87 / 4시간 0.86~0.98 / 일봉 0.77~0.96
     회전율이 수수료를 결정한다. 15분봉은 4년에 2만 번 거래한다.

  ② 손절을 넓혔다 (ATR×2 → ATR×4, 단 최대 -20%)
     ATR×1.5~2.0 조합은 전부 마이너스, ATR×3.0 이상은 전부 플러스.
     ATR 비례만 쓰면 조용한 종목에서 손절이 -50% 까지 벌어지므로 상한을 씌운다.
     (HIBERNATE 에서 확인된 것과 같다 — 목표는 종목별로, 손절은 고정)

  ③ 레버리지를 없앴다 (3배 → 1배) + 총노출 45% (3% × 15칸)
     남은 55% 는 현금. MDD 를 -70%대에서 -24% 로 내린 것이 이것이다.

  ④ 시장 필터를 넣었다 (BTC 100일선 위에서만 신규 진입)
     없으면 MDD -36%, 있으면 -24%.

  ⑤ 진입을 StochRSI 과매도로 바꿨다 (RSI<55 → StochRSI K<25)
     같은 격자에서 rsi_mom / none 계열은 워크포워드에서 전부 마이너스,
     stoch_dip 계열은 전부 플러스였다.

  ⑥ (v2.1) 시장 필터를 "알트가 오르는 중 + BTC 가 추세를 타는 중" 으로 바꿨다
     BTC 100일선 하나만 보면 알트가 죽은 시기를 못 거른다.
     PF 1.38 → 1.69, MDD -24% → -15%, 거래 907 → 568.

결과: 연속 7년 +362%, PF 1.69, MDD -15%, 568거래.
     트레일링 청산 188건의 평균 ROI 가 +116.5% — 여기서 다 번다.

────────────────────────────────────────────────────────────
한계 — 아직 실전 배포 기준을 통과하지 못했다
────────────────────────────────────────────────────────────
수익이 한 시기에 몰려 있다.

  2019~2022   +372%   PF 3.16   314거래
  2023~2026      -7%   PF 0.92   289거래     ← 최근 4년은 제자리

  필터를 켜도 최근 4년의 손실은 -7% 로 작지만 플러스가 되지도 않는다.
  필터는 나쁜 구간을 걸러줄 뿐, 없는 수익을 만들지 못한다.

2021년 알트 폭등장 하나가 대부분이다. 시장 필터를 BTC MA50/MA100/시장폭(25~55%)
어느 것으로 바꿔도 최근 4년은 살아나지 않았다 (PF 0.82~0.99).
독립 데이터(선물 일봉 4년 95종)에서도 -11% (PF 0.72).

익절을 늦추면(20% → 35~50%) 최근 구간이 +9~21% 로 겨우 플러스가 되지만
PF 1.07~1.14 로 통과 기준(1.3)에 못 미친다.

**이 전략의 수익원은 소수의 대박이다.** 트레일링 청산 296건의 평균 ROI 가 +86.8%,
손절 406건이 -19.2%. 알트가 크게 달리는 시기에만 성립한다.

그래서 지금 상태:
  - 돈을 넣지 않는다. 페이퍼로 돌리며 앞으로의 데이터를 쌓는다.
  - 통과 기준(PF 1.3 / MDD 30% / 거래 100)을 최근 구간에서 만족하면 그때 소액 실전.

STRATEGY_RULES.md 3.1 — 상수/신호 함수는 이 파일이 유일한 출처.
"""
import numpy as np
import pandas as pd

VERSION = "2.1"

# ── 시장 ─────────────────────────────────────────────
MARKET = "spot"
TIMEFRAME = "1d"
SIDE = "long"                 # 롱 전용
LEVERAGE = 1                  # 현물

# ── 추세 판정 ────────────────────────────────────────
EMA_FAST = 20
EMA_SLOW = 120
SLOPE_BARS = 3                # 두 EMA 가 이만큼 전보다 위여야 상승추세

# ── 진입 ─────────────────────────────────────────────
STOCH_RSI_PERIOD = 14
STOCH_K = 3
STOCH_OVERSOLD = 25           # 상승추세인데 StochRSI K 가 이 아래면 눌림 → 매수

# ── 청산 ─────────────────────────────────────────────
ATR_PERIOD = 14
PARTIAL_TP = 0.20             # +20% 도달 → 50% 매도, 손절을 본전으로, 트레일링 시작
TRAIL_ATR_MULT = 2.5          # 트레일링 = 최고가 - ATR × 2.5
STOP_ATR_MULT = 4.0           # 초기 손절 = 진입가 - ATR × 4.0
STOP_CAP = 0.20               # 단 -20% 보다 더 내려가진 않는다 (고정 상한)

# ── 비중 ─────────────────────────────────────────────
TICKER_PCT = 0.03             # 티커당 자본의 3%
MAX_CONCURRENT = 15           # 동시 15종목 → 총노출 45%, 현금 55%

# ── 시장 필터 (v2.1) ─────────────────────────────────
#  "잘 먹히는 구간에만 작동" — 거래일의 23% 만 열린다.
#  BTC 100일선 하나만 보던 v2.0 보다 PF 1.38 → 1.69, MDD -24% → -15%.
#  다만 없는 수익을 만들어내진 않는다 (2023~2026 은 여전히 -7%).
ALT_MEDIAN_DAYS = 60          # 전 종목 60일 수익률의 중앙값
ALT_MEDIAN_MIN = 0.0          # 그 중앙값이 0 초과 = 알트가 실제로 오르는 중
TREND_DAYS = 60               # BTC 추세성 측정 구간
TREND_MIN = 0.20              # |60일 순변화| / 60일 일별변화절대합.
                              # 1 에 가까우면 한 방향, 0 이면 횡보.
                              # 횡보장 PF 0.77 vs 추세장 2.3~4.3

# ── 비용 ─────────────────────────────────────────────
FEE_RATE = 0.0005
SLIPPAGE = 0.0003


def stoch_rsi_k(close: pd.Series, period=STOCH_RSI_PERIOD, k=STOCH_K) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    lo = rsi.rolling(period).min()
    hi = rsi.rolling(period).max()
    st = (rsi - lo) / (hi - lo).replace(0, np.nan) * 100
    return st.rolling(k).mean()


def atr(df: pd.DataFrame, period=ATR_PERIOD) -> pd.Series:
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def precompute(df: pd.DataFrame) -> dict:
    c = df["close"].astype(float)
    ef = c.ewm(span=EMA_FAST, adjust=False).mean()
    es = c.ewm(span=EMA_SLOW, adjust=False).mean()
    sf = ef - ef.shift(SLOPE_BARS)
    ss = es - es.shift(SLOPE_BARS)
    a = atr(df)
    return {
        "uptrend": ((c > ef) & (c > es) & (sf > 0) & (ss > 0)).values,
        "stoch_k": stoch_rsi_k(c).fillna(50).values,
        "atr": np.where(a.values > 0, a.values, c.values * 0.01),
    }


def entry_signal(pre: dict, i: int) -> bool:
    """상승 추세 안의 과매도 눌림. 이 함수가 진입 판정의 유일한 출처다."""
    return bool(pre["uptrend"][i] and pre["stoch_k"][i] < STOCH_OVERSOLD)


def market_gate(alt_median_60d: float, btc_trendiness_60d: float) -> bool:
    """오늘 신규 진입을 해도 되는 장인가.

    이 전략은 파도가 와야 탄다. 측정치(현물 8년 507종, 진입 시점 기준):
      BTC 추세성 0~0.15(횡보) → PF 0.77 / 0.15~0.25 → 2.34 / 0.25~0.35 → 1.02
      알트 60일 중앙값 -30~-10% → PF 0.63 / +10~40% → 1.42 / +40%↑ → 2.65
    둘을 같이 걸면 거래가 1092 → 568 건으로 줄고 PF 1.24 → 1.69, MDD -36% → -15%.
    """
    if not (np.isfinite(alt_median_60d) and np.isfinite(btc_trendiness_60d)):
        return False          # 못 재면 안 들어간다
    return alt_median_60d > ALT_MEDIAN_MIN and btc_trendiness_60d > TREND_MIN


def trendiness(close: pd.Series, days: int = TREND_DAYS) -> float:
    """|N일 순변화| ÷ N일 일별변화 절대합. 1=한 방향, 0=횡보."""
    if len(close) < days + 1:
        return float("nan")
    net = abs(float(close.iloc[-1]) - float(close.iloc[-1 - days]))
    tot = float(close.diff().abs().iloc[-days:].sum())
    return net / tot if tot > 0 else float("nan")


def initial_stop(entry: float, atr_now: float) -> float:
    """ATR 비례로 넓게, 단 -20% 상한."""
    return max(entry - atr_now * STOP_ATR_MULT, entry * (1 - STOP_CAP))


def partial_tp_price(entry: float) -> float:
    return entry * (1 + PARTIAL_TP)


def trail_stop(best: float, atr_now: float) -> float:
    return best - atr_now * TRAIL_ATR_MULT
