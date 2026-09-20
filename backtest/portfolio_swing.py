"""물타기 포트폴리오 — 사용자 확정 사양.

    손절 평단 -50% / 물타기 1회 / 익절 +1~3%
    코인·국장·미장 무관, 단 변동성 크고 급락하는 종목은 제외
    시드를 여러 칸으로 나눠 노는 현금을 줄인다

종목 선정이 이 검증의 핵심이다. **지금 시점에서 "안 망한 종목"을 고르면
그게 생존편향이다.** 매 리밸런싱 시점마다 그 시점까지의 데이터만으로
자격을 다시 판정한다(rolling). 자격 판정에 쓰는 창은 전부 t-1 까지다.

    ./.venv-research/bin/python backtest/portfolio_swing.py
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

ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------- 설정


@dataclass(frozen=True)
class PParams:
    slots: int = 10  # 시드를 몇 칸으로 나눌지
    tp: float = 0.02  # 평단 대비 익절 (1~3% 구간)
    step: float = 0.15  # 이만큼 빠지면 물타기 1회
    stop: float = 0.50  # 평단 대비 손절
    fee: float = 0.0006  # 편도 (수수료+슬리피지)
    tax_sell: float = 0.0  # 국장이면 0.0015
    cash_yield: float = 0.03  # 노는 현금
    cooldown: int = 1  # 청산 후 같은 종목 재진입 대기
    entry: str = "dip2"  # "dip2"=2일연속하락 / "any"=자리 나면 바로


@dataclass(frozen=True)
class Filter:
    """'정상적인 상승 하락을 그리는 종목'의 정의. 전부 과거 데이터만 쓴다."""

    min_age: int = 250  # 최소 상장 기간(봉)
    vol_win: int = 180  # 변동성 측정 창
    max_vol: float = 0.80  # 연 변동성 상한
    crash_win: int = 250  # 급락 점검 창
    crash_day: float = -0.20  # 하루 이만큼 빠지면 '급락'
    max_crashes: int = 0  # 창 안에 급락이 이 횟수를 넘으면 제외
    dd_win: int = 500  # 낙폭 점검 창
    max_dd: float = -0.70  # 창 안 최대 낙폭이 이보다 깊으면 제외


def eligibility(close: pd.DataFrame, f: Filter) -> pd.DataFrame:
    """t 행 = t 시점 종가까지의 정보로 판정한 자격. 진입은 t+1 시가라 미래참조 없다."""
    ret = close.pct_change()
    age = close.notna().cumsum()
    vol = ret.rolling(f.vol_win, min_periods=f.vol_win // 2).std() * np.sqrt(252)
    crashes = (ret <= f.crash_day).rolling(f.crash_win, min_periods=20).sum()
    roll_max = close.rolling(f.dd_win, min_periods=60).max()
    dd = close / roll_max - 1
    trough = dd.rolling(f.dd_win, min_periods=60).min()
    ok = (
        (age >= f.min_age)
        & (vol <= f.max_vol)
        & (crashes <= f.max_crashes)
        & (trough >= f.max_dd)
        & close.notna()
    )
    return ok.fillna(False)


def entry_signal(close: pd.DataFrame, kind: str) -> pd.DataFrame:
    if kind == "any":
        return close.notna()
    r = close.pct_change()
    return ((r < 0) & (r.shift(1) < 0)).fillna(False)


# ----------------------------------------------------------------------- 엔진


def run(o, h, low, c, elig, sig, p: PParams, equity0: float = 1.0):
    """칸 단위 포트폴리오. 한 칸에 한 종목, 종목당 물타기 1회.

    봉 처리: 이자 → 보유분 손절/물타기/익절 → 빈 칸 채우기 → 자산 기록.
    물타기한 봉에서는 익절하지 않는다(미래참조 방지).
    """
    dates = o.index
    names = list(o.columns)
    O, H, L, C = (x.to_numpy(float) for x in (o, h, low, c))
    E, S = elig.to_numpy(bool), sig.to_numpy(bool)
    n, k = C.shape

    cash = equity0
    # 칸 상태: 종목 인덱스 / 수량 / 투입액 / 물탔는지 / 예산 / 진입봉
    slot_a = np.full(p.slots, -1)
    slot_u = np.zeros(p.slots)
    slot_b = np.zeros(p.slots)
    slot_added = np.zeros(p.slots, bool)
    slot_budget = np.zeros(p.slots)
    slot_i = np.zeros(p.slots, int)
    wait = np.zeros(k)

    eq = np.zeros(n)
    expo = np.zeros(n)
    trades: list[dict] = []
    days = np.diff(dates.to_numpy().astype("datetime64[D]").astype(int), prepend=0)
    days[0] = 0

    def close_slot(s, px, why, j):
        nonlocal cash
        a = slot_a[s]
        cash += slot_u[s] * px * (1 - p.fee - p.tax_sell)
        trades.append({
            "종목": names[a], "진입": dates[slot_i[s]], "청산": dates[j],
            "보유": j - slot_i[s], "물탐": bool(slot_added[s]),
            "수익률": (slot_u[s] * px * (1 - p.fee - p.tax_sell) - slot_b[s]) / slot_b[s],
            "사유": why,
        })
        wait[a] = p.cooldown
        slot_a[s] = -1
        slot_u[s] = slot_b[s] = slot_budget[s] = 0.0
        slot_added[s] = False

    for j in range(n):
        if days[j] > 0 and cash > 0:
            cash *= (1 + p.cash_yield) ** (days[j] / 365.25)
        wait = np.maximum(wait - 1, 0)

        # --- 보유분 처리 ---
        for s in range(p.slots):
            a = slot_a[s]
            if a < 0 or not np.isfinite(C[j, a]):
                continue
            avg = slot_b[s] / slot_u[s]
            added_here = False

            if L[j, a] <= avg * (1 - p.stop):
                close_slot(s, min(avg * (1 - p.stop), O[j, a]), "손절", j)
                continue

            if not slot_added[s]:
                lvl = avg * (1 - p.step)
                if L[j, a] <= lvl:
                    px = min(lvl, O[j, a])
                    spend = min(slot_budget[s] / 2, cash)
                    if spend > 1e-12:
                        slot_u[s] += spend * (1 - p.fee) / px
                        slot_b[s] += spend
                        cash -= spend
                        slot_added[s] = True
                        added_here = True
                        avg = slot_b[s] / slot_u[s]
                        if L[j, a] <= avg * (1 - p.stop):
                            close_slot(s, min(avg * (1 - p.stop), px), "손절", j)
                            continue

            if not added_here and H[j, a] >= avg * (1 + p.tp):
                close_slot(s, max(avg * (1 + p.tp), O[j, a]), "익절", j)

        # --- 빈 칸 채우기 ---
        free = [s for s in range(p.slots) if slot_a[s] < 0]
        if free and j > 0:
            held = set(slot_a[slot_a >= 0])
            cands = [a for a in range(k)
                     if E[j - 1, a] and S[j - 1, a] and a not in held
                     and wait[a] <= 0 and np.isfinite(O[j, a]) and O[j, a] > 0]
            if cands:
                # 변동성이 낮은(=정상적인) 순으로 채운다
                rng = np.random.default_rng(j)
                rng.shuffle(cands)
                eq_now = cash + sum(slot_u[s] * C[j, slot_a[s]]
                                    for s in range(p.slots)
                                    if slot_a[s] >= 0 and np.isfinite(C[j, slot_a[s]]))
                budget = eq_now / p.slots
                for s in free:
                    if not cands:
                        break
                    a = cands.pop()
                    spend = min(budget / 2, cash)
                    if spend <= 1e-12:
                        break
                    slot_a[s] = a
                    slot_u[s] = spend * (1 - p.fee) / O[j, a]
                    slot_b[s] = spend
                    slot_budget[s] = budget
                    slot_added[s] = False
                    slot_i[s] = j
                    cash -= spend

        pos = sum(slot_u[s] * C[j, slot_a[s]] for s in range(p.slots)
                  if slot_a[s] >= 0 and np.isfinite(C[j, slot_a[s]]))
        eq[j] = cash + pos
        expo[j] = pos / eq[j] if eq[j] > 0 else 0.0

    s_ = pd.Series(eq, index=dates)
    t = pd.DataFrame(trades)
    return {"곡선": s_, "거래내역": t, "평균투입": float(expo.mean()),
            "승률": float((t["수익률"] > 0).mean()) if len(t) else np.nan,
            "거래": len(t)}


def metrics(e: pd.Series, ann: int = 252) -> dict:
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
