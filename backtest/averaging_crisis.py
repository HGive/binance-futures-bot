"""물타기를 위기 구간에 통과시킨다.

"잡코인 말고 우량주·지수로 바꾸면 되는 것 아니냐"는 가설의 결정적 시험.
최고의 자산(S&P500)으로, 최고의 조건(무레버리지)으로 해도 2000·2008 을
통과하는지, 그리고 수익이라는 것이 사실은 예금이자가 아닌지 확인한다.

    ./.venv-research/bin/python backtest/averaging_crisis.py
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

from backtest.averaging import AvgParams, Result, load_crypto, load_stock, simulate  # noqa: E402
from backtest.averaging_compare import CONFIGS, COST, clip  # noqa: E402

CRISES = [
    ("닷컴버블", "1999-01-01", "2003-12-31", "나스닥 -78%, 회복까지 15년"),
    ("금융위기", "2007-06-01", "2010-12-31", "S&P -57%"),
    ("코로나", "2020-01-01", "2020-12-31", "한 달 만에 -34%"),
    ("22년긴축", "2021-11-01", "2023-06-30", "나스닥 -36%, 느리게 오래"),
    ("최근강세장", "2023-01-01", "2026-09-01", "지인들이 벌고 있는 구간"),
]

LONG = [
    ("S&P500", "us_GSPC.csv", "us"),
    ("나스닥100", "us_NDX.csv", "us"),
    ("코카콜라", "us_KO.csv", "us"),
    ("존슨앤존슨", "us_JNJ.csv", "us"),
    ("GE", "us_GE.csv", "us"),
    ("IBM", "us_IBM.csv", "us"),
    ("씨티그룹", "us_C.csv", "us"),
    ("코스피", "kr_KS11.csv", "kr"),
]


def run1(df: pd.DataFrame, p: AvgParams, market: str, lev: float = 1.0, **kw) -> Result:
    cost, tax = COST[market]
    # 사다리가 자본의 lev 배를 쓰도록 크기를 맞춘다. 이래야 레버리지가 실제로 걸린다.
    q = replace(p.sized(lev), cost=cost, tax_sell=tax, lev=lev, **kw)
    return simulate(df, q)


def cagr_of(df: pd.DataFrame) -> float:
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    return float(df["close"].iloc[-1] / df["close"].iloc[0]) ** (1 / yrs) - 1


def mdd_of(df: pd.DataFrame) -> float:
    return float((df["close"] / df["close"].cummax() - 1).min())


def h(t: str) -> None:
    print(f"\n{'=' * 104}\n{t}\n{'=' * 104}")


def pct(v) -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v))):
        return "–"
    return f"{v:+.1%}"


def main() -> int:
    p = CONFIGS["공격"]
    wide = CONFIGS["넓게"]

    # ------------------------------------------------------------------ 1
    h("1. 수익의 정체 — 물타기가 버는 돈 vs 그냥 놀린 현금의 이자 (전 기간, 무레버리지)")
    print(f"{'종목':<11}{'기간':<12}{'물타기':>9}{'이자0일때':>10}{'전략순수익':>11}"
          f"{'보유':>9}{'물타기MDD':>10}{'보유MDD':>9}{'현금비중':>9}")
    for name, f, mkt in LONG:
        d = load_stock(f)
        r = run1(d, p, mkt)
        r0 = run1(d, p, mkt, cash_yield=0.0)
        inmkt = r.bars_in_market / r.bars
        print(
            f"{name:<11}{d.index[0].year}~{d.index[-1].year:<7}{pct(r.cagr):>9}{pct(r0.cagr):>10}"
            f"{pct(r0.cagr):>11}{pct(cagr_of(d)):>9}{pct(r.mdd):>10}{pct(mdd_of(d)):>9}"
            f"{1 - inmkt:>8.0%}"
        )
    print("\n  '이자0일때' = 예금이자를 0으로 놓은 순수 전략 수익. 이게 전략이 실제로 만든 값이다.")

    # ------------------------------------------------------------------ 2
    h("2. 체결 가정 검증 — 일봉 내부 지정가 체결 vs 종가에만 체결 (전 기간, 무레버리지)")
    print(f"{'종목':<11}{'봉내부체결':>12}{'종가만체결':>12}{'차이':>10}{'보유':>10}")
    for name, f, mkt in LONG:
        d = load_stock(f)
        a = run1(d, p, mkt).cagr
        b = run1(d, p, mkt, fill="close").cagr
        print(f"{name:<11}{pct(a):>12}{pct(b):>12}{pct(b - a):>10}{pct(cagr_of(d)):>10}")
    print("\n  차이가 크면 '저가에 다 받아먹는다'는 가정이 결과를 만든 것이다.")

    # ------------------------------------------------------------------ 3
    h("3. 위기 구간별 — S&P500, 무레버리지, 사다리 -29.6% 감당")
    print(f"{'구간':<11}{'기간':<20}{'물타기':>9}{'물타기MDD':>10}{'보유':>9}{'보유MDD':>9}{'사다리':>8}  설명")
    g = load_stock("us_GSPC.csv")
    for name, s, e, note in CRISES:
        d = clip(g, s, e)
        r = run1(d, p, "us")
        print(
            f"{name:<11}{s[:7]}~{e[:7]:<12}{pct(r.final / 10_000 - 1):>9}{pct(r.mdd):>10}"
            f"{pct(d['close'].iloc[-1] / d['close'].iloc[0] - 1):>9}{pct(mdd_of(d)):>9}"
            f"{r.max_adds_used:>6}/8  {note}"
        )

    # ------------------------------------------------------------------ 4
    h("4. 레버리지 — 사다리가 자본의 N배를 쓰도록 맞춘 뒤. S&P500 지수다, 개별주도 아니다")
    print(f"{'구간':<11}" + "".join(f"{f'{lv}배':>15}" for lv in (1, 1.5, 2, 3)))
    for name, s, e, _ in CRISES:
        d = clip(g, s, e)
        cells = []
        for lv in (1, 1.5, 2, 3):
            r = run1(d, p, "us", lev=lv)
            cells.append(f"청산 {r.blown.date()}" if r.blown is not None else pct(r.final / 10_000 - 1))
        print(f"{name:<11}" + "".join(f"{c:>15}" for c in cells))

    print()
    print("  전 기간(1950~2026) 레버리지별 결과:")
    for lv in (1, 1.5, 2, 3):
        r = run1(g, p, "us", lev=lv)
        tag = f"{r.blown.date()} 계좌 소멸" if r.blown is not None else f"연 {r.cagr:+.1%}, MDD {r.mdd:+.1%}"
        print(f"    {lv}배 → {tag}")

    # ------------------------------------------------------------------ 5
    h("5. 사다리를 넓히면 해결되는가 — 설정=넓게(-98.7% 감당, 금액 균등), 무레버리지")
    print(f"{'구간':<11}{'물타기':>10}{'물타기MDD':>11}{'보유':>10}{'보유MDD':>10}{'사다리':>9}")
    for name, s, e, _ in CRISES:
        d = clip(g, s, e)
        r = run1(d, wide, "us")
        print(
            f"{name:<11}{pct(r.final / 10_000 - 1):>10}{pct(r.mdd):>11}"
            f"{pct(d['close'].iloc[-1] / d['close'].iloc[0] - 1):>10}{pct(mdd_of(d)):>10}"
            f"{r.max_adds_used:>7}/12"
        )
    rw = run1(g, wide, "us")
    print(f"\n  전 기간(1950~2026): 연 {rw.cagr:+.1%}, MDD {rw.mdd:+.1%} (보유 연 {cagr_of(g):+.1%})")

    # ------------------------------------------------------------------ 6
    h("6. 코인 — BTC, 레버리지별 (2018-08~2026-08)")
    btc = load_crypto("spot_BTC_USDT_1d.csv")
    for lv in (1, 2, 3, 5):
        r = run1(btc, p, "crypto", lev=lv)
        tag = f"청산 {r.blown.date()} 계좌 소멸" if r.blown is not None else f"연 {r.cagr:+.1%}, MDD {r.mdd:+.1%}"
        print(f"    {lv}배 → {tag}")
    print(f"    참고: BTC 그냥 보유 = 연 {cagr_of(btc):+.1%}, MDD {mdd_of(btc):+.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
