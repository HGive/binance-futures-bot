"""
volume_pop — 거래량 터진 뒤 눌릴 때 받아서 작게 먹기 (롱 전용)

원칙 (STRATEGY_RULES.md 1.1):
  낮을 때 사고, 좀 오르면 판다. 욕심부리지 않는다.
  목표는 +1%. 도달하면 판다. 진입과 동시에 익절/손절을 같이 걸어둔다.

왜 '눌림'인가 (in-sample 9,053건 측정, docs/backtest_volume_pop.md):
  거래량이 터진 봉 '다음'은 평균적으로 눌린다 — 1시간 후 -0.08%, 24시간 후 -0.40%.
  그래서 급등을 따라 사면 (승률 48%, 트레이드당 -0.19%) 그 눌림을 그대로 맞는다.
  같은 신호를 -1% 아래 지정가로 기다렸다 받으면 (승률 63%, +0.20%) 눌림이 할인이 된다.

  덤으로 지정가 진입은 메이커 수수료(0.02%)에 슬리피지가 0이다.
  +1% 를 노리는 전략에서 이 차이는 작지 않다.

STRATEGY_RULES.md 3.1 — 상수/신호 함수는 이 파일이 유일한 출처.
"""
import numpy as np
import pandas as pd

TIMEFRAME = "15m"
BARS_PER_DAY = 96

# =====================================================================
#  1단계 — 거래량 급등 감지 (관심 종목 등록)
# =====================================================================
VOL_MA_BARS = 20             # 거래량 평균 구간
VOL_MULT = 2.0               # 평균 대비 2배 이상 터질 것
BODY_MIN = 0.005             # 그 봉이 +0.5% 이상 양봉일 것
BREAKOUT_BARS = 20           # 최근 20봉 고점 돌파
COOLDOWN_BARS = 24           # 같은 심볼 재신호 최소 간격
MIN_DAILY_QUOTE_VOL = 5e6

# =====================================================================
#  2단계 — 눌림 대기 후 지정가 진입
# =====================================================================
DIP_PCT = 0.010              # 급등봉 종가 대비 -1.0% 에 매수 지정가
FILL_WINDOW_BARS = 24        # 6시간 안에 안 눌리면 주문 취소

# =====================================================================
#  3단계 — 청산 (진입과 동시에 예약)
# =====================================================================
TAKE_PROFIT = 0.010          # +1.0% 지정가 익절 (3배 → ROI +3%)
STOP_LOSS = 0.010            # -1.0% 손절   (3배 → ROI -3%)
MAX_HOLD_BARS = 96           # 24시간 안에 결판 안 나면 정리

# =====================================================================
#  포지션 / 비용
# =====================================================================
LEVERAGE = 3
TICKER_MARGIN_PCT = 0.02     # 티커당 시드의 2% — 손절이 -1% 라 실제 위험은 시드의 0.06%
MAX_CONCURRENT = 10

MAKER_FEE = 0.0002           # 지정가 (진입, 익절)
TAKER_FEE = 0.0004           # 시장가 (손절, 시간청산)
SLIPPAGE = 0.0005            # 시장가에만 적용
FUNDING_PER_DAY = 0.0003
MMR = 0.005


def precompute(df: pd.DataFrame) -> dict:
    v, h, c, o = (df["volume"].values, df["high"].values,
                  df["close"].values, df["open"].values)
    vol_ma = pd.Series(v).shift(1).rolling(VOL_MA_BARS).mean().values
    prior_high = pd.Series(h).shift(1).rolling(BREAKOUT_BARS).max().values
    with np.errstate(invalid="ignore", divide="ignore"):
        vol_ratio = v / vol_ma
        body = c / o - 1
    daily_qv = pd.Series(v * c).rolling(BARS_PER_DAY * 30).median().values * BARS_PER_DAY
    return {"vol_ratio": vol_ratio, "body": body, "prior_high": prior_high,
            "close": c, "daily_qv": daily_qv}


def pop_signal(pre: dict, i: int) -> bool:
    """i번 봉이 마감된 시점: 거래량이 터졌는가."""
    vr, bd, ph, c = pre["vol_ratio"][i], pre["body"][i], pre["prior_high"][i], pre["close"][i]
    if np.isnan(vr) or np.isnan(ph):
        return False
    qv = pre["daily_qv"][i]
    if np.isnan(qv) or qv < MIN_DAILY_QUOTE_VOL:
        return False
    return bool(vr >= VOL_MULT and bd >= BODY_MIN and c > ph)


def limit_price(pop_close: float) -> float:
    """급등봉 종가 대비 눌림 매수가."""
    return pop_close * (1 - DIP_PCT)


def take_profit_price(entry: float) -> float:
    return entry * (1 + TAKE_PROFIT)


def stop_price(entry: float) -> float:
    return entry * (1 - STOP_LOSS)


def liq_price(entry: float) -> float:
    return entry * (1 - 1 / LEVERAGE + MMR)


def breakeven_win_rate() -> float:
    """비용까지 넣은 손익분기 승률."""
    win = TAKE_PROFIT - MAKER_FEE - MAKER_FEE
    lose = STOP_LOSS + MAKER_FEE + SLIPPAGE + TAKER_FEE
    return lose / (win + lose)
