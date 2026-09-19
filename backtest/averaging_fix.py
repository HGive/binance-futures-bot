"""(1) 사용자의 실제 경험이 재현되는가 (2) 고칠 수 있는가.

1. 잡코인 + 레버리지 = 사용자가 실제로 겪은 조건. 모델이 현실과 맞는지 확인.
2. 물타기의 구조적 결함은 "가장 많이 빠질 때 가장 크게 물려 있다"는 것.
   추세 필터로 그 결함을 막으면 쓸 만해지는지 시험한다.

    ./.venv-research/bin/python backtest/averaging_fix.py
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
from backtest.averaging_compare import CONFIGS, COST, load_alts  # noqa: E402

P = CONFIGS["공격"]


def trend_gate(df: pd.DataFrame, win: int = 200) -> np.ndarray:
    """종가가 win 일 이동평균 위일 때만 신규 진입을 허용한다.

    판단은 i 봉 종가로 하고 진입은 i+1 봉 시가에 일어나므로 미래참조가 없다.
    """
    ma = df["close"].rolling(win).mean()
    return (df["close"] > ma).fillna(False).to_numpy()


def run(df, p, market, lev=1.0, gate=None, **kw) -> Result:
    cost, tax = COST[market]
    q = replace(p.sized(lev), cost=cost, tax_sell=tax, lev=lev, **kw)
    return simulate(df, q, allow=gate)


def cagr_of(df: pd.DataFrame) -> float:
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    return float(df["close"].iloc[-1] / df["close"].iloc[0]) ** (1 / yrs) - 1


def h(t: str) -> None:
    print(f"\n{'=' * 104}\n{t}\n{'=' * 104}")


def pct(v) -> str:
    return "–" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:+.1%}"


def main() -> int:
    # ---------------------------------------------------------------- 1
    h("1. 사용자의 실제 조건 재현 — 잡코인 + 레버리지 (2018-08~2026-08)")
    alts = load_alts()
    print(f"  대상 {len(alts)}개 (상폐 포함)\n")
    print(f"{'레버리지':<10}{'연수익 중앙값':>14}{'돈번비율':>10}{'계좌소멸':>10}{'MDD중앙값':>12}{'승률중앙값':>12}")
    for lv in (1, 2, 3, 5, 10):
        cg, blown, mdds, wins = [], 0, [], []
        for sym, df in alts.items():
            r = run(df, P, "crypto", lev=lv)
            cg.append(r.cagr)
            mdds.append(r.mdd)
            if not np.isnan(r.win_rate):
                wins.append(r.win_rate)
            blown += r.blown is not None
        cg = np.array(cg)
        print(
            f"{f'{lv}배':<10}{pct(np.median(cg)):>14}{(cg > 0).mean():>9.0%}"
            f"{blown / len(alts):>10.0%}{pct(np.median(mdds)):>12}{np.median(wins):>11.0%}"
        )
    print("\n  → 승률은 끝까지 100% 근처인데 계좌는 사라진다. 이게 사용자가 겪은 일이다.")

    # ---------------------------------------------------------------- 2
    h("2. 진짜 결함은 무엇인가 — 자본을 거의 안 쓴다 (S&P500 1950~2026)")
    g = load_stock("us_GSPC.csv")
    r = run(g, P, "us")
    r0 = run(g, P, "us", cash_yield=0.0)
    ex = r.exposure
    fwd = g["close"].pct_change(60).shift(-60)
    q = pd.qcut(ex.rank(method="first"), 5, labels=["최저", "낮음", "보통", "높음", "최고"])
    tab = pd.DataFrame({"투입비중": ex, "향후3개월": fwd, "구간": q}).dropna()

    print("  가설이었던 '물타기는 하필 나쁠 때 크게 물려 있다'는 데이터가 반박한다:")
    print(f"\n  {'투입비중 구간':<14}{'평균 투입비중':>14}{'그 시점 이후 3개월 수익률':>26}")
    for lab in ["최저", "낮음", "보통", "높음", "최고"]:
        s = tab[tab["구간"] == lab]
        print(f"  {lab:<14}{s['투입비중'].mean():>13.1%}{s['향후3개월'].mean():>24.2%}")
    print("  → 많이 물려 있는 시점의 이후 수익이 오히려 약간 더 좋다. 진입 타이밍은 문제가 아니다.")

    drift = cagr_of(g)
    print(f"\n  진짜 문제는 자본 효율이다:")
    print(f"    평균 투입비중        {r.avg_exposure:>8.1%}   ← 95% 는 놀고 있다")
    print(f"    최대 투입비중        {r.max_exposure:>8.1%}   ← 폭락할 때만 전액 투입된다")
    print(f"    지수 연수익          {drift:>8.1%}")
    print(f"    기대 = 투입×수익     {r.avg_exposure * drift:>8.1%}")
    print(f"    실제 전략순수익      {r0.cagr:>8.1%}   ← 거의 정확히 일치한다")
    print(f"    전체 수익            {r.cagr:>8.1%}   (나머지는 놀린 현금의 이자)")
    print(f"    MDD                 {r.mdd:>8.1%}   ← 위험만은 전액 투입 기준으로 진다")
    print("\n  → 수익은 5% 노출로 벌고, 위험은 100% 노출로 진다. 이게 물타기의 정체다.")

    # ---------------------------------------------------------------- 3
    h("3. 고칠 수 있는가 — 200일 이동평균 위에서만 신규 진입 (무레버리지, 전 기간)")
    print(f"{'종목':<11}{'기간':<12}{'물타기':>9}{'+추세필터':>11}{'보유':>9}"
          f"{'MDD(원본)':>11}{'MDD(필터)':>11}{'투입비중':>10}")
    targets = [
        ("S&P500", "us_GSPC.csv", "us"),
        ("나스닥100", "us_NDX.csv", "us"),
        ("코카콜라", "us_KO.csv", "us"),
        ("GE", "us_GE.csv", "us"),
        ("IBM", "us_IBM.csv", "us"),
        ("코스피", "kr_KS11.csv", "kr"),
    ]
    for name, f, mkt in targets:
        d = load_stock(f)
        a = run(d, P, mkt)
        b = run(d, P, mkt, gate=trend_gate(d))
        print(
            f"{name:<11}{d.index[0].year}~{d.index[-1].year:<7}{pct(a.cagr):>9}{pct(b.cagr):>11}"
            f"{pct(cagr_of(d)):>9}{pct(a.mdd):>11}{pct(b.mdd):>11}{b.avg_exposure:>9.1%}"
        )

    # ---------------------------------------------------------------- 4
    h("4. 추세필터 + 레버리지 — 투입비중이 낮으니 레버리지를 올려도 되는가 (S&P500 1950~2026)")
    d = load_stock("us_GSPC.csv")
    gate = trend_gate(d)
    print(f"{'레버리지':<10}{'연수익':>10}{'MDD':>10}{'평균투입':>10}{'최대투입':>10}{'청산':>14}")
    for lv in (1, 2, 3, 4, 6):
        r = run(d, P, "us", lev=lv, gate=gate)
        tag = str(r.blown.date()) if r.blown is not None else "없음"
        print(
            f"{f'{lv}배':<10}{pct(r.cagr):>10}{pct(r.mdd):>10}"
            f"{r.avg_exposure:>9.1%}{r.max_exposure:>10.1%}{tag:>14}"
        )
    print(f"\n  참고: S&P500 그냥 보유 = 연 {cagr_of(d):+.1%}, "
          f"MDD {float((d['close'] / d['close'].cummax() - 1).min()):+.1%}")

    # ---------------------------------------------------------------- 5
    h("5. 코인에서도 추세필터가 듣는가 — BTC (2018-08~2026-08)")
    btc = load_crypto("spot_BTC_USDT_1d.csv")
    bgate = trend_gate(btc)
    print(f"{'조건':<24}{'연수익':>10}{'MDD':>10}{'평균투입':>10}{'청산':>12}")
    for label, lv, gt in [
        ("물타기 1배", 1, None),
        ("물타기 3배", 3, None),
        ("물타기+추세 1배", 1, bgate),
        ("물타기+추세 3배", 3, bgate),
        ("물타기+추세 5배", 5, bgate),
    ]:
        r = run(btc, P, "crypto", lev=lv, gate=gt)
        tag = str(r.blown.date()) if r.blown is not None else "없음"
        print(f"{label:<24}{pct(r.cagr):>10}{pct(r.mdd):>10}{r.avg_exposure:>9.1%}{tag:>12}")
    print(f"\n  참고: BTC 그냥 보유 = 연 {cagr_of(btc):+.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
