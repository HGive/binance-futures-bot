"""분할매수(물타기) + 익절 목표 — 시드를 나눠 여러 종목에 굴리는 방식.

사용자 질문에서 나온 조합:
- 시드를 여러 종목에 나눈다 (하나가 물려도 나머지로 계속 굴린다)
- 각 종목은 고점 대비 일정 폭마다 1/N 씩 분할 매수
- 익절은 하되 얼마에 할지가 문제다 ("안 팔면 어떻게 돈을 버냐")

미래참조 방지 (앞선 엔진들과 같은 규칙):
- 고점은 **직전 봉까지**의 정보로만 갱신한다. 오늘 종가로 고점을 올린 뒤
  오늘 저가와 비교하면 미래참조다.
- **추가매수한 봉에서는 익절하지 않는다.** 낮아진 평단으로 그 봉의 고가를
  집으면 "저가에 사서 같은 날 고가에 판다"가 되어 결과가 부풀려진다.

    ./.venv-research/bin/python backtest/tranche.py
"""

from __future__ import annotations

import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")


@dataclass(frozen=True)
class TParams:
    tranches: int = 4  # 시드를 종목당 몇 칸으로 나눌지
    drop: float = 0.25  # 고점 대비 이만큼씩 내려갈 때마다 한 칸
    tp: float | None = 0.03  # 평단 대비 익절. None = 안 판다
    fee: float = 0.0002  # 편도
    cash_yield: float = 0.04  # 유휴 현금
    cooldown: int = 1  # 익절 후 재진입 대기
    tax_sell: float = 0.0  # 주식이면 거래세


def run(df: pd.DataFrame, p: TParams, equity0: float = 1.0):
    o, h, low, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    n = len(c)
    cash, units, basis = equity0, 0.0, 0.0
    per = 1.0 / p.tranches
    peak = -np.inf  # 직전 봉까지의 고점
    used = 0
    wait = 0
    eq = np.zeros(n)
    expo = np.zeros(n)
    trades: list[dict] = []
    entry_i = 0
    open_eq = equity0
    days = np.diff(df.index.to_numpy().astype("datetime64[D]").astype(int), prepend=0)
    days[0] = 0

    for j in range(n):
        if days[j] > 0 and cash > 0:
            cash *= (1 + p.cash_yield) ** (days[j] / 365.25)

        bought = False

        # 신규 시작 — 첫 칸을 시가에 산다
        if units == 0:
            if wait > 0:
                wait -= 1
            else:
                open_eq = cash
                entry_i = j
                b = min(per * cash, cash)
                if b > 0:
                    units += b * (1 - p.fee) / o[j]
                    cash -= b
                    basis += b
                    used = 1
                    peak = o[j]
                    bought = True

        # 추가매수 — 고점은 **직전 봉까지**의 값만 쓴다
        if units > 0:
            eq_now = cash + units * c[j]
            while used < p.tranches and cash > 1e-12 and low[j] <= peak * (1 - p.drop * used):
                px = min(peak * (1 - p.drop * used), o[j])
                b = min(per * eq_now, cash)
                if b <= 1e-12:
                    break
                units += b * (1 - p.fee) / px
                cash -= b
                basis += b
                used += 1
                bought = True

            # 익절 — 같은 봉에서 샀으면 미룬다
            if p.tp is not None and not bought:
                avg = basis / units
                if h[j] >= avg * (1 + p.tp):
                    px = max(avg * (1 + p.tp), o[j])
                    cash += units * px * (1 - p.fee - p.tax_sell)
                    trades.append({"진입": df.index[entry_i], "청산": df.index[j],
                                   "보유": j - entry_i, "칸": used,
                                   "수익률": (cash - open_eq) / open_eq})
                    units = basis = 0.0
                    used = 0
                    wait = p.cooldown
                    peak = -np.inf

        eq[j] = cash + units * c[j]
        expo[j] = units * c[j] / eq[j] if eq[j] > 0 else 0.0
        # 고점 갱신은 **맨 마지막에**. 다음 봉부터 반영된다.
        if units > 0:
            peak = max(peak, c[j])

    s = pd.Series(eq, index=df.index)
    t = pd.DataFrame(trades)
    return {"곡선": s, "거래내역": t, "평균투입": float(expo.mean()),
            "승률": float((t["수익률"] > 0).mean()) if len(t) else np.nan,
            "매도횟수": len(t)}


def metrics(e: pd.Series, ann: int = 365) -> dict:
    y = (e.index[-1] - e.index[0]).days / 365.25
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / y) - 1
    v = e.pct_change().std() * np.sqrt(ann)
    dd = e / e.cummax() - 1
    m = e.resample("ME").last().pct_change().dropna()
    und = dd < -0.001
    runs, cur = [], 0
    for f in und:
        cur = cur + 1 if f else 0
        runs.append(cur)
    return {"연수익": cagr, "변동성": v, "샤프": cagr / v if v > 0 else np.nan,
            "MDD": float(dd.min()), "수익월": float((m > 0).mean()),
            "최장물림": max(runs) / ann if runs else 0.0}
