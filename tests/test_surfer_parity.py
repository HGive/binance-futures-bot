"""
SURFER 실전 어댑터와 백테스트가 같은 신호를 내는지 대조.
STRATEGY_RULES 3.7 — 같은 규칙을 두 군데에 구현하지 않는다.
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.surfer as SF
import backtest.trailing_atr as B
from live import adapters


def test_parity(limit=25):
    raw = B.load_spot_daily()
    cfg = dict(B.DEFAULT, src="spot1d", tf=1, entry="stoch_dip",
               thr_long=SF.STOCH_OVERSOLD, thr_short=100 - SF.STOCH_OVERSOLD,
               ema_f=SF.EMA_FAST, ema_s=SF.EMA_SLOW, slope=SF.SLOPE_BARS)
    A = adapters.get("surfer")
    syms = sorted(raw)[:limit]
    bad = tot = 0
    for s in syms:
        df = raw[s]
        bt = B.precompute(s, df, cfg)                 # 백테스트 쪽
        lv = A.precompute(df)                         # 실전 어댑터 쪽
        for i in range(200, len(df)):
            b = bool(bt["up"][i] and bt["stk"][i] < cfg["thr_long"])
            l = A.signal(df, lv, i)
            tot += 1
            if b != l:
                bad += 1
                if bad <= 5:
                    print(f"  불일치 {s} i={i}  백테스트={b} 실전={l} "
                          f"(up {bt['up'][i]}/{lv['uptrend'][i]}  K {bt['stk'][i]:.1f}/{lv['stoch_k'][i]:.1f})")
    assert bad == 0, f"{len(syms)}종목 {tot:,}봉에서 불일치 {bad}건"
    print(f"OK — {len(syms)}종목 {tot:,}봉 전부 일치")


def test_stop_parity():
    """손절 산식이 같은가 (ATR 비례 + 최대폭 상한)."""
    for entry, atr in ((100.0, 3.0), (100.0, 10.0), (0.5, 0.02)):
        live = SF.initial_stop(entry, atr)
        back = max(entry - atr * SF.STOP_ATR_MULT, entry * (1 - SF.STOP_CAP))
        assert abs(live - back) < 1e-12, f"{entry}/{atr}: {live} != {back}"
    print("OK — 손절 산식 일치")


if __name__ == "__main__":
    test_parity()
    test_stop_parity()
