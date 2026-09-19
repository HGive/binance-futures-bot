"""물타기 최종 검증 — 배당 미조정 가격 기준.

지정가 주문은 화면에 찍히는 실제 가격에 걸린다. 배당조정 가격으로 돌리면
존재하지 않는 가격에 익절이 체결되어 결과가 완전히 달라진다(SPY 기준
MDD -4.4% vs -37.6%). 여기서는 액면분할만 반영한 가격을 쓰고 배당은
보유 중 현금으로 받는다.

    ./.venv-research/bin/python backtest/averaging_final.py
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

from backtest.averaging import AvgParams, Result, load_crypto, simulate  # noqa: E402
from backtest.averaging_compare import CONFIGS, COST  # noqa: E402

RAW = Path(__file__).resolve().parent.parent / "data" / "stocks_raw"

# (표시이름, 파일, 배당수익률 추정)
TARGETS = [
    ("SPY", "us_SPY.csv", 0.019),
    ("QQQ", "us_QQQ.csv", 0.007),
    ("코카콜라", "us_KO.csv", 0.030),
    ("존슨앤존슨", "us_JNJ.csv", 0.026),
    ("IBM", "us_IBM.csv", 0.029),
    ("GE", "us_GE.csv", 0.028),
    ("인텔", "us_INTC.csv", 0.024),
    ("씨티그룹", "us_C.csv", 0.024),
    ("AT&T", "us_T.csv", 0.069),
    ("엑손모빌", "us_XOM.csv", 0.049),
]


def load_raw(f: str) -> pd.DataFrame:
    d = pd.read_csv(RAW / f, parse_dates=["date"], index_col="date")
    return d[["open", "high", "low", "close"]].dropna()


def run(df, p: AvgParams, lev=1.0, div=0.0, **kw) -> Result:
    cost, tax = COST["us"]
    q = replace(p.sized(lev), cost=cost, tax_sell=tax, lev=lev, div_yield=div, **kw)
    return simulate(df, q)


def total_return_cagr(df: pd.DataFrame, div: float) -> float:
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    price = (df["close"].iloc[-1] / df["close"].iloc[0]) ** (1 / yrs) - 1
    return price + div


def h(t: str) -> None:
    print(f"\n{'=' * 104}\n{t}\n{'=' * 104}")


def pct(v) -> str:
    return "–" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:+.1%}"


def main() -> int:
    P = CONFIGS["공격"]

    h("1. 물타기 vs 그냥 보유 — 배당 미조정 가격, 배당은 현금 수취, 무레버리지")
    print(f"{'종목':<10}{'기간':<12}{'물타기':>9}{'이자·배당0':>11}{'그냥보유':>9}"
          f"{'물타기MDD':>10}{'보유MDD':>9}{'평균투입':>9}{'최장물림':>9}")
    rows = []
    for name, f, div in TARGETS:
        if not (RAW / f).exists():
            continue
        d = load_raw(f)
        r = run(d, P, div=div)
        r0 = run(d, P, div=0.0, cash_yield=0.0)
        bh_mdd = float((d["close"] / d["close"].cummax() - 1).min())
        print(
            f"{name:<10}{d.index[0].year}~{d.index[-1].year:<7}{pct(r.cagr):>9}{pct(r0.cagr):>11}"
            f"{pct(total_return_cagr(d, div)):>9}{pct(r.mdd):>10}{pct(bh_mdd):>9}"
            f"{r.avg_exposure:>8.1%}{r.underwater_days:>7}일"
        )
        rows.append((name, r, r0))
    print("\n  '이자·배당0' = 전략 자체가 만든 수익. 나머지는 놀린 현금의 이자다.")

    h("2. 가장 위험한 순간 — 한 번 물리면 얼마나 오래 잡혀 있는가 (SPY)")
    d = load_raw("us_SPY.csv")
    r = run(d, P, div=0.019)
    t = r.trades.copy()
    t["연"] = pd.to_datetime(t["open"]).dt.year
    long_ones = t.nlargest(6, "bars")[["open", "close", "bars", "adds", "pnl_pct", "reason"]]
    print(f"  전체 거래 {len(t):,}건, 승률 {r.win_rate:.1%}, 손실 거래 {int((t['pnl'] <= 0).sum())}건")
    print(f"  보유기간 중앙값 {t['bars'].median():.0f}일, 최장 {t['bars'].max():.0f}일\n")
    print("  가장 오래 물린 거래:")
    for _, x in long_ones.iterrows():
        print(f"    {x['open'].date()} → {x['close'].date()}  {int(x['bars']):>4}거래일  "
              f"물타기 {int(x['adds'])}회  결과 {x['pnl_pct']:+.2%} ({x['reason']})")

    h("3. 레버리지 — 배당 미조정 기준 (SPY 1993~2026)")
    print(f"{'레버리지':<10}{'연수익':>10}{'MDD':>10}{'평균투입':>10}{'결과':>22}")
    for lv in (1, 1.5, 2, 3, 5):
        rr = run(d, P, lev=lv, div=0.019)
        tag = f"{rr.blown.date()} 계좌 소멸" if rr.blown is not None else "생존"
        print(f"{f'{lv}배':<10}{pct(rr.cagr):>10}{pct(rr.mdd):>10}{rr.avg_exposure:>9.1%}{tag:>22}")

    h("4. 설정을 바꾸면 달라지는가 (SPY 1993~2026, 무레버리지)")
    print(f"{'설정':<8}{'감당하락':>9}{'연수익':>9}{'전략순수익':>11}{'MDD':>9}{'평균투입':>9}{'거래수':>8}")
    for cname, cfg in CONFIGS.items():
        a = run(d, cfg, div=0.019)
        b = run(d, cfg, div=0.0, cash_yield=0.0)
        print(f"{cname:<8}{cfg.coverage():>8.1%}{pct(a.cagr):>9}{pct(b.cagr):>11}"
              f"{pct(a.mdd):>9}{a.avg_exposure:>8.1%}{len(a.trades):>8,}")
    print(f"\n  참고: SPY 그냥 보유 = 연 {total_return_cagr(d, 0.019):+.1%}, "
          f"MDD {float((d['close'] / d['close'].cummax() - 1).min()):+.1%}")

    h("5. 코인 (배당 문제 없음) — BTC 2018-08~2026-08")
    btc = load_crypto("spot_BTC_USDT_1d.csv")
    cost, tax = COST["crypto"]
    print(f"{'레버리지':<10}{'연수익':>10}{'MDD':>10}{'평균투입':>10}{'결과':>22}")
    for lv in (1, 2, 3, 5):
        q = replace(P.sized(lv), cost=cost, tax_sell=tax, lev=lv)
        rr = simulate(btc, q)
        tag = f"{rr.blown.date()} 계좌 소멸" if rr.blown is not None else "생존"
        print(f"{f'{lv}배':<10}{pct(rr.cagr):>10}{pct(rr.mdd):>10}{rr.avg_exposure:>9.1%}{tag:>22}")
    yrs = (btc.index[-1] - btc.index[0]).days / 365.25
    print(f"\n  참고: BTC 그냥 보유 = 연 "
          f"{(btc['close'].iloc[-1] / btc['close'].iloc[0]) ** (1 / yrs) - 1:+.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
