"""
진입 판정이 두 군데(전략 함수 / 백테스트 벡터)에서 갈리지 않는지 대조한다.
STRATEGY_RULES 3.1 — 상수·신호는 strategies/ 가 유일한 출처.
실전 봇이 백테스트와 다르게 사는 사고를 막기 위한 테스트다.
"""
import os, sys, glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.hibernate as S
from backtest.walkforward import signal_mask
from backtest.hibernate import load_all


def test_parity(limit=40):
    res = load_all(min_days=430, market="spot")
    daily = res[1] if isinstance(res, tuple) else res
    syms = sorted(daily)[:limit]
    pre = {s: S.precompute(daily[s]) for s in syms}
    bad = 0
    for s in syms:
        m = signal_mask(pre, daily, s, S.MIN_DROUGHT_DAYS, S.MAX_PRICE_POS, S.MIN_SPIKES)
        c = daily[s]["close"].values
        for i in range(430, len(c)):
            e = S.entry_signal(pre[s], i, float(c[i]))
            if bool(m[i]) != e:
                bad += 1
                if bad <= 5:
                    print(f"  불일치 {s} i={i}  mask={bool(m[i])} entry_signal={e}")
    assert bad == 0, f"{len(syms)}종목에서 불일치 {bad}건"
    print(f"OK — {len(syms)}종목 전 구간 일치")


if __name__ == "__main__":
    test_parity()
