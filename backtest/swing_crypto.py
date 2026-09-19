"""코인 스윙 물타기 — 하락 진입 + 손절 + 레버리지 1~2배.

사용자 확정 조건:
- 승률 100% 는 필요 없다. 적당한 손절을 둔다.
- 소액 시드 → 국내주식 1주 가격 제약을 피해 코인으로.
- 바이낸스 선물, 레버리지 1배 또는 최대 2배.

주식 검증(backtest/samsung_swing.py)과 같은 구조에 코인 고유 비용을 더했다.
**펀딩비**가 핵심이다. 선물 롱은 대부분의 기간 펀딩을 낸다 (BTC 평균 연 11.6%,
ETH 13.8%, XRP 14.6%). 현물에는 없는 비용이라 1배 롱이면 현물이 무조건 유리하다.

    ./.venv-research/bin/python backtest/swing_crypto.py
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
OHLCV = ROOT / "data" / "ohlcv"
FUNDING = ROOT / "data" / "funding"


@dataclass(frozen=True)
class CSwing:
    tp: float = 0.03  # 평단 대비 익절
    step: float = 0.05  # 이만큼 빠지면 물타기
    max_adds: int = 1  # 물타기 횟수
    add_mult: float = 1.0  # 물타기 금액 배수
    stop: float = 0.10  # 평단 대비 손절 (필수)
    lev: float = 1.0  # 레버리지 (1.0 = 현물과 동일한 노출)
    fee: float = 0.0002  # 편도 수수료 (지정가 메이커 0.018% + 여유)
    mmr: float = 0.005  # 유지증거금률
    futures: bool = True  # True 면 펀딩비를 낸다
    cash_yield: float = 0.04  # 유휴 USDT 적립 (바이낸스 Earn 유연상품 수준)
    max_hold: int = 0  # 0 = 무제한


def run(df: pd.DataFrame, p: CSwing, allow: np.ndarray,
        funding: pd.Series | None = None, equity0: float = 1.0):
    """연속 매매. allow[j] 가 True 면 j+1 봉 시가에 진입.

    처리 순서: 예약 진입 → 펀딩·이자 → 청산 → 손절 → 물타기(같은 봉 손절 재확인)
    → 익절 → 기록. 물타기한 봉에서는 익절하지 않는다(미래참조 방지).
    """
    o, h, low, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    n = len(c)
    fund = (
        funding.reindex(df.index).fillna(0.0).to_numpy()
        if (funding is not None and p.futures)
        else np.zeros(n)
    )
    # 사다리를 다 써도 명목이 자본의 lev 배를 넘지 않게
    first = p.lev / (1.0 + p.add_mult * p.max_adds)

    cash, units, basis = equity0, 0.0, 0.0
    avg = add_px = np.nan
    stop_px = -np.inf
    adds = 0
    inpos = pending = False
    open_eq = equity0
    entry_i = 0

    curve = np.zeros(n)
    expo = np.zeros(n)
    trades: list[dict] = []
    funding_paid = 0.0
    blown: pd.Timestamp | None = None
    days = np.diff(df.index.to_numpy().astype("datetime64[D]").astype(int), prepend=0)
    days[0] = 0

    def close_all(px, why, j):
        nonlocal cash, units, basis, inpos, adds, stop_px
        cash += units * px * (1 - p.fee)
        trades.append({"진입": df.index[entry_i], "청산": df.index[j], "보유": j - entry_i,
                       "물타기": adds, "수익률": (cash - open_eq) / open_eq, "사유": why})
        units = basis = 0.0
        adds = 0
        inpos = False
        stop_px = -np.inf

    for j in range(n):
        if days[j] > 0:
            if cash > 0:
                cash *= (1 + p.cash_yield) ** (days[j] / 365.25)
            if units > 0 and fund[j]:
                pay = units * c[j - 1] * fund[j]  # 롱이 내는 펀딩
                cash -= pay
                funding_paid += pay / max(cash + units * c[j], 1e-12)

        # 회계: 명목을 사면 현금이 그만큼 줄고, 모자라면 음수(=차입)가 된다.
        # 자산 = 현금 + 코인평가액. 레버리지 = 명목 / 자산. 현물(lev=1)이면
        # 현금이 음수로 가지 않으므로 같은 식이 그대로 성립한다.
        if pending and not inpos:
            pending = False
            open_eq = cash + units * o[j]
            entry_i = j
            notional = first * open_eq
            if notional > 0:
                units = notional / o[j]
                basis = notional
                cash -= notional * (1 + p.fee)
                avg = o[j]
                add_px = o[j] * (1 - p.step)
                stop_px = avg * (1 - p.stop)
                inpos = True

        if inpos:
            added_here = False
            if p.max_hold and (j - entry_i) >= p.max_hold:
                close_all(c[j], "기간만료", j)
            elif cash + units * low[j] <= units * low[j] * p.mmr:
                # 강제청산: 봉 저가 기준으로 유지증거금 미달
                cash += units * low[j] * (1 - p.fee)
                trades.append({"진입": df.index[entry_i], "청산": df.index[j],
                               "보유": j - entry_i, "물타기": adds,
                               "수익률": (cash - open_eq) / open_eq, "사유": "강제청산"})
                units = basis = 0.0
                adds = 0
                inpos = False
                stop_px = -np.inf
            elif low[j] <= stop_px:
                close_all(min(stop_px, o[j]), "손절", j)
            else:
                while adds < p.max_adds and low[j] <= add_px:
                    px = min(add_px, o[j])
                    notional = first * open_eq * p.add_mult
                    eq_now = cash + units * px
                    if units * px + notional > p.lev * max(eq_now, 1e-12):
                        break  # 레버리지 상한
                    units += notional / px
                    basis += notional
                    cash -= notional * (1 + p.fee)
                    avg = basis / units
                    adds += 1
                    added_here = True
                    add_px = px * (1 - p.step)
                    stop_px = avg * (1 - p.stop)
                if inpos and low[j] <= stop_px:
                    close_all(min(stop_px, o[j]), "손절", j)
                elif inpos and not added_here and h[j] >= avg * (1 + p.tp):
                    close_all(max(avg * (1 + p.tp), o[j]), "익절", j)

        eq = cash + units * c[j]
        if eq <= 0:
            blown = df.index[j]
            curve[j:] = 0.0
            break
        curve[j] = eq
        expo[j] = units * c[j] / eq

        if not inpos and j + 1 < n and allow[j]:
            pending = True

    s = pd.Series(curve, index=df.index)
    t = pd.DataFrame(trades)
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    final = float(s.iloc[-1])
    return {
        "연수익": (final / equity0) ** (1 / yrs) - 1 if final > 0 else -1.0,
        "MDD": float((s / s.cummax() - 1).min()),
        "승률": float((t["수익률"] > 0).mean()) if len(t) else np.nan,
        "거래": len(t),
        "평균보유": float(t["보유"].mean()) if len(t) else np.nan,
        "평균투입": float(expo.mean()),
        "최악거래": float(t["수익률"].min()) if len(t) else np.nan,
        "청산": blown,
        "펀딩누적": funding_paid,
        "곡선": s,
        "거래내역": t,
    }


# --------------------------------------------------------------------------- 데이터


def load(sym: str) -> pd.DataFrame:
    d = pd.read_csv(OHLCV / f"spot_{sym}_USDT_1d.csv")
    d["date"] = pd.to_datetime(d["timestamp"], unit="ms").dt.normalize()
    return d.set_index("date").sort_index()[["open", "high", "low", "close"]].dropna()


def load_funding(sym: str, index: pd.DatetimeIndex) -> pd.Series:
    """8시간 펀딩을 일 단위로 합산."""
    p = FUNDING / f"{sym}USDT.csv"
    if not p.exists():
        return pd.Series(0.0, index=index)
    d = pd.read_csv(p, parse_dates=["time"])
    daily = d.set_index("time")["rate"].resample("D").sum()
    return daily.reindex(index).fillna(0.0)


def signals(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """i 봉 종가까지만 쓴다. 진입은 i+1 봉 시가."""
    c = df["close"]
    ret = c.pct_change()
    out = {"아무때나": np.ones(len(df), bool)}
    out["2일연속하락"] = ((ret < 0) & (ret.shift(1) < 0)).fillna(False).to_numpy()
    out["3일연속하락"] = ((ret < 0) & (ret.shift(1) < 0) & (ret.shift(2) < 0)).fillna(False).to_numpy()
    out["하루 -5% 이상"] = (ret <= -0.05).fillna(False).to_numpy()
    for w, dd in ((20, 0.10), (60, 0.20)):
        out[f"{w}일고점 -{dd:.0%}"] = ((c / c.rolling(w).max() - 1) <= -dd).fillna(False).to_numpy()
    d_ = c.diff()
    up = d_.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d_.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    out["RSI 30 이하"] = (rsi <= 30).fillna(False).to_numpy()
    ma200 = c.rolling(200).mean()
    out["200일선 위 + 2일하락"] = (out["2일연속하락"] & (c > ma200).fillna(False).to_numpy())
    return out
