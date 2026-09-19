"""분산 추세추종 — "꾸준한 우상향"이 실제로 무엇으로 만들어지는지.

물타기의 대안으로 제시하기 위한 검증. 규칙은 이미 검증된 TREND_VT 와 같다
(20·60·120일 수익률 부호 평균 → 변동성 역수 비중 → 포트폴리오 변동성 목표).
파라미터를 이 데이터에 맞춰 다시 고르지 않았다. 연율화만 거래일 252 로 바꿨다.

핵심 질문: 부드러운 수익곡선은 승률에서 나오는가, 분산에서 나오는가.

    ./.venv-research/bin/python backtest/trend_multiasset.py
"""

from __future__ import annotations

import sys
import warnings
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from backtest.averaging import load_crypto, load_stock, simulate  # noqa: E402
from backtest.averaging_compare import CONFIGS, COST  # noqa: E402

ANN = 252  # 거래일 기준 연율화
LOOKBACKS = (20, 60, 120)
VOL_WIN = 30
TARGET_VOL = 0.20
ASSET_VOL = 0.40
MAX_W = 1.0
MAX_GROSS = 2.0
BAND = 0.25
COST_ONEWAY = 0.0010  # ETF 왕복 20bp 가정 (수수료+스프레드+슬리피지)

BASKET = {
    "SPY": "미국주식",
    "QQQ": "미국기술",
    "IWM": "미국소형",
    "EFA": "선진국",
    "EEM": "신흥국",
    "TLT": "미국장기채",
    "IEF": "미국중기채",
    "GLD": "금",
    "SLV": "은",
    "DBC": "원자재",
    "USO": "원유",
    "VNQ": "리츠",
    "UUP": "달러",
    "FXE": "유로",
    "FXY": "엔",
    "HYG": "하이일드",
}


# ------------------------------------------------------------------ 전략


def targets(close: pd.DataFrame, long_only: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """마감 종가 → 목표 비중. i 행은 i 일 종가까지만 쓴다(미래참조 없음)."""
    ret = close.ffill().pct_change(fill_method=None)
    sig = sum(np.sign(close / close.shift(lb) - 1) for lb in LOOKBACKS) / len(LOOKBACKS)
    if long_only:
        sig = sig.clip(lower=0)
    vol = ret.rolling(VOL_WIN, min_periods=VOL_WIN).std() * np.sqrt(ANN)
    w = sig * (ASSET_VOL / vol)
    age = close.notna().cumsum()
    w = w.where(age >= max(LOOKBACKS), 0.0).fillna(0.0).clip(-MAX_W * 4, MAX_W * 4)

    port = (w.shift(1) * ret).sum(axis=1)
    pvol = port.rolling(60, min_periods=30).std() * np.sqrt(ANN)
    scale = (TARGET_VOL / pvol).clip(upper=3.0).fillna(1.0)
    w = w.mul(scale, axis=0).clip(-MAX_W, MAX_W)
    gross = w.abs().sum(axis=1)
    w = w.div(np.maximum(gross / MAX_GROSS, 1.0), axis=0)
    return w, ret


def run_trend(close: pd.DataFrame, long_only: bool = False, cash_yield: float = 0.03) -> pd.Series:
    """목표 비중을 밴드로 거래하고 비용을 뺀 자산곡선."""
    w, ret = targets(close, long_only)
    tgt = w.to_numpy()
    held = np.zeros(tgt.shape[1])
    pos = np.zeros_like(tgt)
    for i in range(len(tgt)):
        t = tgt[i]
        move = np.abs(t - held) > BAND * np.maximum(np.abs(t), 0.02)
        move |= (t == 0) & (held != 0)
        held = np.where(move, t, held)
        pos[i] = held
    pos = pd.DataFrame(pos, index=close.index, columns=close.columns)
    turn = pos.diff().abs().sum(axis=1).fillna(0.0)
    gross_ret = (pos.shift(1) * ret).sum(axis=1).fillna(0.0)
    # 투입하지 않은 자본은 현금이자를 받는다 (물타기와 같은 조건)
    idle = (1.0 - pos.shift(1).abs().sum(axis=1).clip(upper=1.0)).fillna(1.0)
    net = gross_ret - turn * COST_ONEWAY + idle * (cash_yield / ANN)
    return (1 + net).cumprod() * 10_000


# ------------------------------------------------------------------ 지표


def stats(eq: pd.Series, name: str) -> dict:
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    r = eq.pct_change().dropna()
    vol = r.std() * np.sqrt(ANN)
    dd = eq / eq.cummax() - 1
    m = eq.resample("ME").last().pct_change().dropna()
    y12 = eq.resample("ME").last().pct_change(12).dropna()
    under = dd < -0.001
    runs, cur = [], 0
    for f in under:
        cur = cur + 1 if f else 0
        runs.append(cur)
    return {
        "전략": name,
        "연수익": cagr,
        "변동성": vol,
        "샤프": cagr / vol if vol > 0 else np.nan,
        "MDD": dd.min(),
        "수익월비율": (m > 0).mean(),
        "최악1년": y12.min() if len(y12) else np.nan,
        "최장물림": int(max(runs) / ANN * 365.25) if runs else 0,
    }


def show(rows: list[dict]) -> None:
    d = pd.DataFrame(rows)
    for c in ("연수익", "변동성", "MDD", "수익월비율", "최악1년"):
        d[c] = d[c].map(lambda v: "–" if pd.isna(v) else f"{v:+.1%}")
    d["샤프"] = d["샤프"].map(lambda v: "–" if pd.isna(v) else f"{v:.2f}")
    d["최장물림"] = d["최장물림"].map(lambda v: f"{v}일")
    print(d.to_string(index=False))


def h(t: str) -> None:
    print(f"\n{'=' * 104}\n{t}\n{'=' * 104}")


def main() -> int:
    px = {}
    for t in BASKET:
        p = Path("data/stocks") / f"us_{t}.csv"
        if p.exists():
            px[t] = load_stock(p.name)["close"]
    close = pd.DataFrame(px).dropna(how="all")
    close = close[close.index >= "2007-04-11"]  # 전 종목이 존재하는 구간
    close = close.dropna()
    print(f"바스켓 {len(close.columns)}개 자산, {len(close)}일  "
          f"{close.index[0].date()} → {close.index[-1].date()}")

    spy_df = load_stock("us_SPY.csv")
    spy_df = spy_df[spy_df.index >= close.index[0]]
    spy = close[["SPY"]]

    rows = []

    # 1) 물타기 (같은 구간, 같은 현금이자)
    cost, tax = COST["us"]
    q = replace(CONFIGS["공격"].sized(1.0), cost=cost, tax_sell=tax)
    rows.append(stats(simulate(spy_df, q).equity, "물타기 (SPY)"))

    # 2) 그냥 보유
    rows.append(stats(10_000 * spy_df["close"] / spy_df["close"].iloc[0], "SPY 그냥 보유"))

    # 3) 추세추종, 한 종목만
    rows.append(stats(run_trend(spy), "추세추종 (SPY 하나)"))

    # 4) 추세추종, 분산 바스켓
    rows.append(stats(run_trend(close), f"추세추종 ({len(close.columns)}개 분산)"))
    rows.append(stats(run_trend(close, long_only=True), f"추세추종 ({len(close.columns)}개, 롱만)"))

    # 5) 코인 추가
    crypto = {}
    for sym in ("BTC", "ETH"):
        p = Path("data/ohlcv") / f"spot_{sym}_USDT_1d.csv"
        if p.exists():
            crypto[sym] = load_crypto(p.name)["close"]
    if crypto:
        cc = pd.DataFrame(crypto)
        cc.index = cc.index.normalize()
        both = close.join(cc, how="left").ffill()
        both = both[both.index >= cc.index[0]]
        rows.append(stats(run_trend(both), f"추세추종 ({len(both.columns)}개 + 코인, 2018~)"))
        sub = close[close.index >= cc.index[0]]
        rows.append(stats(run_trend(sub), f"추세추종 ({len(sub.columns)}개 주식만, 2018~ 비교용)"))

    h("꾸준한 우상향은 무엇으로 만들어지는가 — 2007-04 ~ 2026-09 (비용·현금이자 반영)")
    show(rows)

    h("자산 수를 늘리면 실제로 부드러워지는가")
    order = ["SPY", "TLT", "GLD", "EEM", "DBC", "IEF", "EFA", "USO", "QQQ", "FXY",
             "IWM", "VNQ", "SLV", "UUP", "FXE", "HYG"]
    order = [t for t in order if t in close.columns]
    rows2 = []
    for k in (1, 2, 4, 8, 12, len(order)):
        sub = close[order[:k]]
        rows2.append(stats(run_trend(sub), f"{k}개 자산"))
    show(rows2)

    h("각 자산 하나만으로 추세추종했을 때 (왜 섞어야 하는지)")
    rows3 = []
    for t in order:
        rows3.append(stats(run_trend(close[[t]]), f"{t} ({BASKET[t]})"))
    show(sorted(rows3, key=lambda r: -r["샤프"]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
