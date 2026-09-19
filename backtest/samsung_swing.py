"""삼성전자 스윙 물타기 — "떨어졌을 때 사서 며칠 기다렸다 조금 먹고 나온다".

앞선 검증은 **무작위 진입**이었다. 사용자가 원한 것은 하락 시 진입이다.
단기 반전(떨어진 것은 조금 되돌린다)은 실제로 존재하는 현상이므로
진입 신호가 결과를 바꿀 수 있다. 그래서 따로 검증한다.

핵심 대조군: **같은 전략 + 무작위 진입**. 하락 진입이 무작위 진입보다
나아야 "떨어졌을 때 산다"는 신호에 값어치가 있는 것이다.

    ./.venv-research/bin/python backtest/samsung_swing.py
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
CACHE = ROOT / "data" / "stocks_raw"


# --------------------------------------------------------------------------- 설정


@dataclass(frozen=True)
class Swing:
    tp: float = 0.03  # 평단 대비 익절
    step: float = 0.05  # 이만큼 빠지면 물타기
    max_adds: int = 1  # 물타기 횟수
    add_mult: float = 1.0  # 물타기 금액 배수
    stop: float | None = None  # 평단 대비 손절 (None = 손절 없음)
    cost: float = 0.0002  # 편도 수수료+슬리피지
    tax_sell: float = 0.0015  # 한국 주식 매도 거래세 0.15%
    cash_yield: float = 0.03  # 놀고 있는 현금 (CMA)
    max_hold: int = 0  # 0 = 무제한


# --------------------------------------------------------------------------- 진입 신호


def signals(df: pd.DataFrame, kospi: pd.Series | None = None) -> dict[str, np.ndarray]:
    """i 봉 종가까지만 써서 만든다. 진입은 i+1 봉 시가 (미래참조 없음)."""
    c = df["close"]
    ret = c.pct_change()
    out: dict[str, np.ndarray] = {}

    out["아무때나(대조군)"] = np.ones(len(df), bool)
    out["2일연속하락"] = ((ret < 0) & (ret.shift(1) < 0)).fillna(False).to_numpy()
    out["3일연속하락"] = (
        (ret < 0) & (ret.shift(1) < 0) & (ret.shift(2) < 0)
    ).fillna(False).to_numpy()
    out["하루 -3% 이상"] = (ret <= -0.03).fillna(False).to_numpy()
    for w, d in ((20, 0.05), (60, 0.10), (120, 0.15)):
        hi = c.rolling(w).max()
        out[f"{w}일고점 -{d:.0%}"] = ((c / hi - 1) <= -d).fillna(False).to_numpy()
    for w in (20, 60):
        ma = c.rolling(w).mean()
        out[f"{w}일선 -5%"] = ((c / ma - 1) <= -0.05).fillna(False).to_numpy()

    # RSI(14)
    d_ = c.diff()
    up = d_.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d_.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    out["RSI 30 이하"] = (rsi <= 30).fillna(False).to_numpy()
    out["RSI 25 이하"] = (rsi <= 25).fillna(False).to_numpy()

    if kospi is not None:
        k = kospi.reindex(df.index).ffill()
        kma = k.rolling(60).mean()
        bad = (k < kma).fillna(False).to_numpy()
        out["장이 안좋을때(코스피<60일선)"] = bad
        out["장 안좋고 + 20일고점 -5%"] = bad & out["20일고점 -5%"]
    return out


# --------------------------------------------------------------------------- 엔진


def run(df: pd.DataFrame, p: Swing, allow: np.ndarray, equity0: float = 1.0):
    """연속 매매. allow[j] 가 True 인 봉의 **다음 봉 시가**에 신규 진입.

    봉 처리 순서: 예약 진입 → 손절 → 물타기(같은 봉 손절 재확인) → 익절 → 기록.
    물타기가 일어난 봉에서는 익절하지 않는다(낮아진 평단은 미래참조).
    """
    o, h, low, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    n = len(c)
    first = 1.0 / (1.0 + p.add_mult * p.max_adds)  # 사다리를 다 써도 레버리지 0

    cash, units, basis = equity0, 0.0, 0.0
    avg = add_px = np.nan
    stop_px = -np.inf
    adds = 0
    inpos = pending = False
    open_eq = equity0

    curve = np.zeros(n)
    expo = np.zeros(n)
    trades: list[dict] = []
    days = np.diff(df.index.to_numpy().astype("datetime64[D]").astype(int), prepend=0)
    days[0] = 0
    entry_i = 0

    def close_all(px, why, j):
        nonlocal cash, units, basis, inpos, adds, stop_px
        cash += units * px * (1 - p.cost - p.tax_sell)
        trades.append({
            "진입": df.index[entry_i], "청산": df.index[j], "보유": j - entry_i,
            "물타기": adds, "수익률": (cash - open_eq) / open_eq, "사유": why,
        })
        units = basis = 0.0
        adds = 0
        inpos = False
        stop_px = -np.inf

    for j in range(n):
        if days[j] > 0 and cash > 0:
            cash *= (1 + p.cash_yield) ** (days[j] / 365.25)

        if pending and not inpos:
            pending = False
            open_eq = cash
            entry_i = j
            spend = first * cash / (1 + p.cost)
            if spend > 0:
                units = spend / o[j]
                basis = spend
                cash -= spend * (1 + p.cost)
                avg = o[j]
                add_px = o[j] * (1 - p.step)
                stop_px = avg * (1 - p.stop) if p.stop else -np.inf
                inpos = True

        if inpos:
            added_here = False
            if p.max_hold and (j - entry_i) >= p.max_hold:
                close_all(c[j], "기간만료", j)
            elif low[j] <= stop_px:
                close_all(min(stop_px, o[j]), "손절", j)
            else:
                while adds < p.max_adds and low[j] <= add_px:
                    px = min(add_px, o[j])
                    spend = min(first * open_eq * p.add_mult, cash / (1 + p.cost))
                    if spend <= 0:
                        break
                    units += spend / px
                    basis += spend
                    cash -= spend * (1 + p.cost)
                    avg = basis / units
                    adds += 1
                    added_here = True
                    add_px = px * (1 - p.step)
                    if p.stop:
                        stop_px = avg * (1 - p.stop)
                if inpos and p.stop and low[j] <= stop_px:
                    close_all(min(stop_px, o[j]), "손절", j)
                elif inpos and not added_here and h[j] >= avg * (1 + p.tp):
                    close_all(max(avg * (1 + p.tp), o[j]), "익절", j)

        eq = cash + units * c[j]
        curve[j] = eq
        expo[j] = units * c[j] / eq if eq > 0 else 0.0

        if not inpos and j + 1 < n and allow[j]:
            pending = True

    s = pd.Series(curve, index=df.index)
    t = pd.DataFrame(trades)
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    return {
        "연수익": float(s.iloc[-1] / equity0) ** (1 / yrs) - 1,
        "MDD": float((s / s.cummax() - 1).min()),
        "승률": float((t["수익률"] > 0).mean()) if len(t) else np.nan,
        "거래": len(t),
        "평균보유": float(t["보유"].mean()) if len(t) else np.nan,
        "평균투입": float(expo.mean()),
        "최악거래": float(t["수익률"].min()) if len(t) else np.nan,
        "물탄비율": float((t["물타기"] > 0).mean()) if len(t) else np.nan,
        "곡선": s,
        "거래내역": t,
    }


# --------------------------------------------------------------------------- 데이터


def samsung() -> pd.DataFrame:
    p = CACHE / "kr_005930.csv"
    if not p.exists():
        import yfinance as yf

        d = yf.download("005930.KS", start="1990-01-01", auto_adjust=False,
                        progress=False, threads=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        d.columns = [x.lower() for x in d.columns]
        d = d[["open", "high", "low", "close"]].dropna()
        d.index.name = "date"
        p.parent.mkdir(parents=True, exist_ok=True)
        d.to_csv(p)
    d = pd.read_csv(p, parse_dates=["date"], index_col="date")
    return d[["open", "high", "low", "close"]].dropna()


def kospi() -> pd.Series:
    p = CACHE / "kr_KS11.csv"
    if not p.exists():
        import FinanceDataReader as fdr

        d = fdr.DataReader("KS11", "1990-01-01")
        d.columns = [x.lower() for x in d.columns]
        d.index.name = "date"
        d.to_csv(p)
    return pd.read_csv(p, parse_dates=["date"], index_col="date")["close"]
