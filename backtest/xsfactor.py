"""
횡단면 팩터 롱숏 (달러 중립) — 무기한 선물, 일봉, 주 1회 리밸런싱.

팩터 점수가 높은 쪽을 사고 낮은 쪽을 판다. 양쪽 명목 합이 같아 시장 방향과 무관.
    mom    : 과거 30일 수익 (최근 1일 제외)
    rev    : 과거 7일 수익의 반대 (단기 반전)
    carry  : 과거 7일 평균 펀딩비의 반대 (펀딩 높은 걸 숏 → 펀딩 수령)
    lowvol : 30일 변동성의 반대

비용: 편도 수수료+슬리피지, 실제 펀딩비.

    RESEARCH_DATA=... python backtest/xsfactor.py
"""
import os
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.trend import load_daily, metrics, fmt  # noqa: E402


@dataclass(frozen=True)
class XParams:
    factor: str = "mom"
    top_n: int = 60            # 거래대금 상위 N 에서
    quantile: float = 0.2      # 위·아래 20% 씩
    gross: float = 1.0         # 롱 0.5 + 숏 0.5
    rebalance_days: int = 7
    cost: float = 0.0005 + 0.0005
    min_age: int = 60
    min_qv: float = 20e6


def scores(close, fund, f):
    ret = close.pct_change()
    if f == "mom":
        return close.shift(1) / close.shift(31) - 1
    if f == "rev":
        return -(close / close.shift(7) - 1)
    if f == "carry":
        return -fund.rolling(7, min_periods=7).mean()
    if f == "lowvol":
        return -ret.rolling(30, min_periods=30).std()
    raise ValueError(f)


def simulate(close, qv, fund, p: XParams, start=None, end=None):
    ret = close.pct_change().fillna(0.0)
    sc = scores(close, fund, p.factor)
    age = close.notna().cumsum()
    liq = qv.rolling(30, min_periods=20).mean()
    rank = liq.where((age >= p.min_age) & close.notna()).rank(axis=1, ascending=False)
    elig = (rank <= p.top_n) & (liq >= p.min_qv) & sc.notna()
    sl = np.ones(len(close), bool)
    if start is not None:
        sl &= close.index >= start
    if end is not None:
        sl &= close.index < end
    ii = np.where(sl)[0]
    R, FU = ret.values, fund.values
    cur = np.zeros(close.shape[1])
    eq, curve = 1.0, []
    for n, i in enumerate(ii):
        if n > 0:
            pnl = np.nan_to_num(cur * R[i] - cur * FU[i]).sum()
            eq *= 1 + pnl
            cur = cur * (1 + R[i]) / (1 + pnl)
        if n % p.rebalance_days == 0:
            e = elig.iloc[i].values
            tgt = np.zeros_like(cur)
            m = int(e.sum())
            if m >= 10:
                s = sc.iloc[i].values
                order = np.argsort(np.where(e, s, np.nan))  # NaN 은 뒤로
                valid = order[:m]
                q = max(int(m * p.quantile), 2)
                lo, hi = valid[:q], valid[-q:]
                tgt[hi] = p.gross / 2 / q
                tgt[lo] = -p.gross / 2 / q
            d = tgt - cur
            eq *= 1 - np.abs(d).sum() * p.cost
            cur = tgt
        curve.append(eq)
    return pd.Series(curve, index=close.index[ii])


if __name__ == "__main__":
    close, qv, fund = load_daily()
    t0, t1 = close.index[0], close.index[-1]
    split = t0 + (t1 - t0) * 0.7
    start = pd.Timestamp("2020-06-01")
    print(f"종목 {close.shape[1]} | {start.date()} ~ {t1.date()} | 개발/검증 경계 {split.date()}")
    for f in ("mom", "rev", "carry", "lowvol"):
        c = simulate(close, qv, fund, XParams(factor=f), start=start, end=split)
        print(f"[개발] {f:7s} " + fmt(metrics(c)))
