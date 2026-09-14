"""
TREND_VT — BTC·ETH 무기한 선물, 추세 방향 × 변동성 목표 (롱/숏).

추세추종 예외 전략이다 (STRATEGY_RULES 1.1: 추세추종은 예외로 명시하고 별도 검증).
익절가가 없는 대신 매일 목표 비중을 다시 계산해 신호로 줄이고 뒤집는다.

    신호   = 20·60·120일 수익률 부호의 평균            (-1 ~ +1)
    비중   = 신호 × 0.40 / 종목 30일 변동성
    포트폴리오 60일 변동성을 연 20% 에 맞춘다, 종목당 명목 1배·총 2배 상한
    목표와 현재 비중 차이가 25% 미만이면 거래하지 않는다
    재난 손절: 매일 종가 × (1 ∓ 5 × 일간 변동성) 에 STOP_MARKET (규칙 3.5)

검증: docs/research_2026-09-15.md
    사전 등록 후 검증 구간(2024-08 ~ 2026-09) 한 번 (손절 없이): 연 20.8%, MDD -17.9%, 상승·하락 국면 모두 수익
    재난 손절 포함 (backtest/trend.py 기본값, 실전과 같은 규칙): 수치는 문서 참조
    비용·실제 펀딩비 반영

백테스트(backtest/trend.py)는 이 파일의 TParams / targets / trade_mask 를 import 한다 (규칙 3.1, 3.7).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

VERSION = "1.0"
SYMBOLS = ("BTC/USDT:USDT", "ETH/USDT:USDT")   # ccxt 심볼
IDS = ("BTCUSDT", "ETHUSDT")                   # 데이터 열 이름
TIMEFRAME = "1d"
HISTORY_DAYS = 400          # 신호 계산에 필요한 과거 (120 + 60 + 30 보다 넉넉히)

LEVERAGE = 3                # 격리. 종목당 명목 1배 상한이라 증거금은 자본의 1/3 이하
STOP_K = 5.0                # 재난 손절 = 일간 변동성 × 5 (BTC 약 12%). 7년 21회 발동
KILL_DD = 0.20              # 전략 자본 고점 대비 -20% → 전부 정리하고 멈춘다 (규칙 7)


@dataclass(frozen=True)
class TParams:
    lookbacks: tuple = (20, 60, 120)
    vol_win: int = 30
    target_vol: float = 0.20      # 포트폴리오 연 변동성 목표
    asset_vol: float = 0.40       # 종목당 목표 (합산 후 포트폴리오로 재조정)
    max_gross: float = 2.0        # 총 명목 / 자본 상한
    max_w: float = 1.0            # 종목당 명목 상한 (자본 대비)
    long_only: bool = False
    top_n: int = 20               # fixed 가 비었을 때: 거래대금 상위 N 종목만
    min_qv: float = 50e6          # fixed 가 비었을 때: 일평균 거래대금 하한
    band: float = 0.25            # 목표 비중 변화가 현재의 25% 미만이면 거래 안 함
    cost: float = 0.0005 + 0.0005  # 편도 (수수료 + 슬리피지) — 백테스트용
    min_age: int = 180
    fixed: tuple = IDS            # 비어있지 않으면 이 종목만
    signal: str = "sign"          # "sign" | "donchian"
    stop_k: float | None = STOP_K  # 재난 손절 거리 (일간 변동성 배수). None = 손절 없음 (탐색용)
    stop_slip: float = 0.001      # 손절 체결 미끄러짐 (백테스트용)


LIVE = TParams()


def donchian_state(close, lb):
    """Zarattini 식: 종가가 lb일 최고 종가면 진입, (lb일 최고+최저)/2 아래로 마감하면 청산. 0/1."""
    hi = close.rolling(lb, min_periods=lb).max()
    lo = close.rolling(lb, min_periods=lb).min()
    mid = ((hi + lo) / 2).values
    enter = (close >= hi).values
    x = close.values
    out = np.zeros(close.shape)
    state = np.zeros(close.shape[1])
    stop = np.full(close.shape[1], -np.inf)
    for i in range(close.shape[0]):
        state = np.where(np.isnan(x[i]), 0, state)
        stop = np.where(state == 1, np.fmax(stop, mid[i]), -np.inf)
        exit_ = (state == 1) & (x[i] < stop)
        state = np.where(exit_, 0, state)
        new = (state == 0) & enter[i] & ~exit_
        state = np.where(new, 1, state)
        stop = np.where(new, mid[i], stop)
        out[i] = state
    return pd.DataFrame(out, index=close.index, columns=close.columns)


def targets(close: pd.DataFrame, qv: pd.DataFrame, p: TParams = LIVE):
    """마감된 일봉 종가 → 날짜별 목표 비중(자본 대비 명목, 부호=방향). 순수 함수.

    i 행의 비중은 i 일 종가까지의 정보만 쓴다. 그 종가에 체결해 i+1 일 수익을 받는다.
    """
    ret = close.ffill().pct_change(fill_method=None)
    if p.signal == "donchian":
        sig = sum(donchian_state(close, lb) for lb in p.lookbacks) / len(p.lookbacks)
    else:
        sig = sum(np.sign(close / close.shift(lb) - 1) for lb in p.lookbacks) / len(p.lookbacks)
    if p.long_only:
        sig = sig.clip(lower=0)
    vol = ret.rolling(p.vol_win, min_periods=p.vol_win).std() * np.sqrt(365)
    w = sig * (p.asset_vol / vol)
    age = close.notna().cumsum()
    if p.fixed:
        elig = pd.DataFrame(False, index=close.index, columns=close.columns)
        for s in p.fixed:
            if s in elig:
                elig[s] = True
    else:
        liq = qv.rolling(30, min_periods=20).mean()
        rank = liq.where(age >= p.min_age).rank(axis=1, ascending=False)
        elig = (rank <= p.top_n) & (liq >= p.min_qv)
    elig &= age >= max(p.lookbacks)
    w = w.where(elig, 0.0).fillna(0.0).clip(-p.max_w * 4, p.max_w * 4)
    # 포트폴리오 변동성 재조정: 최근 60일
    port_ret = (w.shift(1) * ret).sum(axis=1)
    pvol = port_ret.rolling(60, min_periods=30).std() * np.sqrt(365)
    scale = (p.target_vol / pvol).clip(upper=3.0).fillna(1.0)
    w = w.mul(scale, axis=0).clip(-p.max_w, p.max_w)
    gross = w.abs().sum(axis=1)
    w = w.div(np.maximum(gross / p.max_gross, 1.0), axis=0)
    return w, ret


def trade_mask(target, current, band=LIVE.band):
    """어느 종목을 거래할지. 차이가 목표의 band 이상이거나, 목표가 0 인데 들고 있으면."""
    target, current = np.asarray(target, float), np.asarray(current, float)
    m = np.abs(target - current) > band * np.maximum(np.abs(target), 0.02)
    return m | ((target == 0) & (current != 0))


def daily_vol(close: pd.DataFrame, p: TParams = LIVE):
    """손절 거리 계산용 일간 변동성 (연율 아님)."""
    ret = close.ffill().pct_change(fill_method=None)
    return ret.rolling(p.vol_win, min_periods=p.vol_win).std()


def stop_price(last_close, side, vol_d, k=STOP_K):
    """재난 손절가. side = +1 롱 / -1 숏."""
    return last_close * (1 - side * k * vol_d)
