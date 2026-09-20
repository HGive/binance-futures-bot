"""
SWING_DCA — 코인 스윙 물타기. 하락 진입 → 1회 물타기 → 소폭 익절, 재난 손절.

사용자 확정 사양 (2026-09-20):
    시드를 SLOTS 칸으로 나눈다. 한 칸에 한 종목.
    2일 연속 하락한 종목에 칸의 절반을 넣는다.
    평단 대비 -STEP 까지 빠지면 나머지 절반으로 한 번만 물탄다.
    평단 대비 +TP 에 전량 익절한다.
    평단 대비 -STOP 이면 손절한다. 레버리지 없음.
    변동성이 너무 크거나 급락이 잦은 종목은 제외한다.

**기대치를 먼저 적는다 (docs/research_averaging_2026-09-19.md).**
    코인 536종(상장폐지 34% 포함) 2018-08~2026-08 검증:
    연 +24%, MDD -62%, 승률 97%, 손절 발동 3%, 수익월 58%.
    같은 기간 BTC·ETH 반반 보유는 연 +33%, MDD -77%.
    **이 전략은 보유를 이기지 못한다.** 24개 조합 전부에서, 같은 투입비중만큼만
    사놓고 현금으로 두는 쪽이 위험 대비 수익이 더 높았다. 그걸 알고 돌린다.
    수익률보다 '작동하는가'를 보는 것이 목적이다.

백테스트(backtest/portfolio_swing.py)와 실행봇(live/swing_bot.py)이
이 파일의 상수와 함수를 그대로 쓴다 (규칙 3.1, 3.7).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

VERSION = "1.0"
TIMEFRAME = "1d"
HISTORY_DAYS = 560          # 필터 창(500) + 여유
QUOTE = "USDT"

LEVERAGE = 1                # 레버리지 없음 — 검증에서 2배는 모든 지표가 나빠졌다
KILL_DD = 0.25              # 전략 자본 고점 대비 -25% → 전부 정리하고 멈춘다 (규칙 7)


@dataclass(frozen=True)
class SParams:
    slots: int = 10             # 시드를 몇 칸으로
    tp: float = 0.02            # 평단 대비 익절 (사용자 지정 1~3%)
    step: float = 0.15          # 평단 대비 이만큼 빠지면 물타기 1회
    stop: float = 0.50          # 평단 대비 손절 (사용자 지정)
    add_mult: float = 1.0       # 물타기 금액 = 첫 매수와 동일
    cooldown_days: int = 1      # 청산 후 같은 종목 재진입 대기
    # --- 종목 자격 (전부 과거 데이터만 쓴다) ---
    min_age: int = 250          # 최소 상장 봉수
    vol_win: int = 180          # 변동성 창
    max_vol: float = 1.00       # 연 변동성 상한
    crash_win: int = 250        # 급락 점검 창
    crash_day: float = -0.20    # 하루 이만큼이면 급락
    max_crashes: int = 1        # 창 안 급락 허용 횟수
    dd_win: int = 500           # 낙폭 점검 창
    max_dd: float = -0.80       # 창 안 최대 낙폭 하한
    min_qv: float = 5e6         # 일평균 거래대금 하한 (USDT)
    # --- 비용 (백테스트용) ---
    fee: float = 0.0006


LIVE = SParams()


def first_buy_fraction(p: SParams = LIVE) -> float:
    """첫 매수가 자본에서 차지하는 비율. 물타기까지 해도 레버리지가 걸리지 않게."""
    return 1.0 / (p.slots * (1.0 + p.add_mult))


def eligible(close: pd.DataFrame, qv: pd.DataFrame | None = None,
             p: SParams = LIVE) -> pd.DataFrame:
    """'정상적인 상승 하락을 그리는 종목'. i 행은 i 일 종가까지만 쓴다.

    진입은 i+1 일에 일어나므로 미래참조가 없다. 지금 시점에서 '안 망한 종목'을
    고르면 그게 생존편향이므로, 매 시점 과거 데이터로만 다시 판정한다.
    """
    ret = close.pct_change(fill_method=None)
    age = close.notna().cumsum()
    vol = ret.rolling(p.vol_win, min_periods=p.vol_win // 2).std() * np.sqrt(365)
    crashes = (ret <= p.crash_day).rolling(p.crash_win, min_periods=20).sum()
    trough = (close / close.rolling(p.dd_win, min_periods=60).max() - 1) \
        .rolling(p.dd_win, min_periods=60).min()
    ok = ((age >= p.min_age) & (vol <= p.max_vol) & (crashes <= p.max_crashes)
          & (trough >= p.max_dd) & close.notna())
    if qv is not None:
        ok &= qv.rolling(30, min_periods=20).mean() >= p.min_qv
    return ok.fillna(False)


def entry_signal(close: pd.DataFrame) -> pd.DataFrame:
    """2일 연속 하락. i 행은 i 일 종가까지만 쓴다."""
    r = close.pct_change(fill_method=None)
    return ((r < 0) & (r.shift(1) < 0)).fillna(False)


def add_price(avg: float, p: SParams = LIVE) -> float:
    """물타기 지정가."""
    return avg * (1 - p.step)


def tp_price(avg: float, p: SParams = LIVE) -> float:
    """익절 지정가."""
    return avg * (1 + p.tp)


def stop_price(avg: float, p: SParams = LIVE) -> float:
    """재난 손절가."""
    return avg * (1 - p.stop)


def cost_basis(qty: float, avg: float) -> float:
    """취득원가. 거래소의 entryPrice 는 평균 진입가이므로 qty × avg 는 **쓴 돈**이다.

    시가평가액이 아니라서 가격이 움직여도 변하지 않는다. 물탔는지 판정에 쓸 수 있는
    이유가 이것이다.
    """
    return qty * avg


def remaining_add(qty: float, avg: float, slot_budget: float) -> float:
    """아직 더 넣을 수 있는 금액. 부분체결이 나도 스스로 맞춰진다.

    칸 예산을 넘지 않으므로 레버리지가 걸리지 않는다.
    """
    return max(0.0, slot_budget - cost_basis(qty, avg))


def has_added(notional: float, slot_budget: float, p: SParams = LIVE) -> bool:
    """이미 물탔는지를 거래소에서 읽은 **취득원가**로 판정한다 (규칙 3.6).

    첫 매수 원가는 예산의 1/(1+add_mult), 물타기까지 하면 예산 전액이다.
    그 중간을 넘으면 물탄 것으로 본다.
    """
    base = slot_budget / (1.0 + p.add_mult)
    return notional > base * (1.0 + p.add_mult / 2.0)


__all__ = ["VERSION", "TIMEFRAME", "HISTORY_DAYS", "QUOTE", "LEVERAGE", "KILL_DD",
           "SParams", "LIVE", "first_buy_fraction", "eligible", "entry_signal",
           "add_price", "tp_price", "stop_price", "has_added",
           "cost_basis", "remaining_add"]
