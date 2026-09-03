"""
진입한 그 봉에서 손절을 검사하는가.

이 버그를 세 번 냈다 (COIL, REBOUND, HIBERNATE).
"진입 봉에서 청산하지 않는다"(규칙 3.3)를 손절에까지 적용한 것이 원인이다.
익절은 봉 안 경로를 몰라서 미루는 게 맞지만, 손절은 거래소에 걸어둔 주문이라
그 봉 안에서 값이 닿으면 그냥 터진다. 미루면 손절 맞았어야 할 거래가
살아남아 회복할 시간을 얻고, 손절이 좁을수록 성적이 통째로 부풀려진다.

REBOUND 에서는 이걸 고치자 PF 1.50 -> 0.87 로 뒤집혔다.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import strategies.rebound as R  # noqa: E402
from backtest import rebound as BR  # noqa: E402


def _bars(rows):
    """rows: (open, high, low, close, volume) 목록 → simulate 가 먹는 형태."""
    a = np.array(rows, dtype=float)
    c = a[:, 3]
    qv = a[:, 4] * c
    n = len(a)
    d = {
        "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": c,
        "qv": qv,
        "qv_ma": np.full(n, float(R.MIN_QV_MA)),   # 유동성 하한은 통과시키되
        "ts": np.arange(n, dtype=np.int64) * 60_000,
    }
    for k in (3, 5, 10, 15, 20, 30):
        s = pd.Series(c)
        d[f"d{k}"] = (s / s.shift(k) - 1).fillna(0).values
    return d


def test_체결봉에서_손절선을_뚫으면_손절로_센다():
    """지정가로 받은 그 1분 안에 손절선 아래로 내려간 경우."""
    n = 400
    rows = [[100.0, 100.0, 100.0, 100.0, 1.0] for _ in range(n)]
    sig = 150                                  # 이 봉에서 신호가 나게 만든다
    for k in range(sig - R.DROP_BARS, sig + 1):  # 5분 동안 -3% 급락
        px = 100.0 * (1 - 0.03 * (k - (sig - R.DROP_BARS)) / R.DROP_BARS)
        rows[k] = [px, px, px, px, 1.0]
    rows[sig][4] = R.MIN_QV_MA * R.VOL_MULT * 2 / rows[sig][3]   # 거래대금 폭발
    limit = rows[sig][3]                       # 신호봉 종가에 지정가

    # 체결 봉: 시가는 지정가 위(postOnly 통과), 저가는 손절선 아래로 뚫는다
    fill_bar = sig + 1
    stop = limit * (1 - R.STOP_LOSS)
    rows[fill_bar] = [limit * 1.001, limit * 1.001, stop * 0.999, limit, 1.0]
    # 그 뒤로는 크게 회복시킨다 — 손절을 안 세면 익절로 잡히게
    for k in range(fill_bar + 1, n):
        px = limit * 1.05
        rows[k] = [px, px, px, px, 1.0]

    df = BR.simulate({"T/USDT:USDT": _bars(rows)},
                     drop_bars=R.DROP_BARS, drop_pct=R.DROP_PCT, vol_mult=R.VOL_MULT,
                     tp=R.TAKE_PROFIT, sl=R.STOP_LOSS, hold=R.MAX_HOLD_BARS)
    filled = df[df.filled == 1]
    assert len(filled) == 1, f"체결이 1건이어야 한다: {df.to_dict('records')}"
    assert filled.iloc[0]["why"] == "SL", (
        "체결된 봉에서 손절선을 뚫었으면 SL 이어야 한다. "
        f"실제: {filled.iloc[0]['why']} — 진입 봉 손절 검사가 빠졌다")
    assert filled.iloc[0]["ret"] < 0


def test_체결봉이_멀쩡하면_손절로_세지_않는다():
    """위와 같은데 체결 봉이 손절선을 안 건드리는 경우 — 익절까지 가야 한다."""
    n = 400
    rows = [[100.0, 100.0, 100.0, 100.0, 1.0] for _ in range(n)]
    sig = 150
    for k in range(sig - R.DROP_BARS, sig + 1):
        px = 100.0 * (1 - 0.03 * (k - (sig - R.DROP_BARS)) / R.DROP_BARS)
        rows[k] = [px, px, px, px, 1.0]
    rows[sig][4] = R.MIN_QV_MA * R.VOL_MULT * 2 / rows[sig][3]
    limit = rows[sig][3]

    fill_bar = sig + 1
    stop = limit * (1 - R.STOP_LOSS)
    rows[fill_bar] = [limit * 1.001, limit * 1.001, stop * 1.001, limit, 1.0]  # 손절 위에서 멈춤
    for k in range(fill_bar + 1, n):
        px = limit * (1 + R.TAKE_PROFIT * 1.5)
        rows[k] = [px, px, px, px, 1.0]

    df = BR.simulate({"T/USDT:USDT": _bars(rows)},
                     drop_bars=R.DROP_BARS, drop_pct=R.DROP_PCT, vol_mult=R.VOL_MULT,
                     tp=R.TAKE_PROFIT, sl=R.STOP_LOSS, hold=R.MAX_HOLD_BARS)
    filled = df[df.filled == 1]
    assert len(filled) == 1
    assert filled.iloc[0]["why"] == "TP", (
        f"손절을 안 건드렸으면 익절이어야 한다. 실제: {filled.iloc[0]['why']}")


def test_postOnly_는_이미_지나간_값에_안_건다():
    """주문을 낼 때 값이 이미 지정가 아래면 거래소가 거부한다 (-5022)."""
    n = 400
    rows = [[100.0, 100.0, 100.0, 100.0, 1.0] for _ in range(n)]
    sig = 150
    for k in range(sig - R.DROP_BARS, sig + 1):
        px = 100.0 * (1 - 0.03 * (k - (sig - R.DROP_BARS)) / R.DROP_BARS)
        rows[k] = [px, px, px, px, 1.0]
    rows[sig][4] = R.MIN_QV_MA * R.VOL_MULT * 2 / rows[sig][3]
    limit = rows[sig][3]
    # 다음 봉이 지정가보다 **아래에서** 열린다 → postOnly 거부
    lower = limit * 0.99
    for k in range(sig + 1, n):
        rows[k] = [lower, lower, lower, lower, 1.0]

    df = BR.simulate({"T/USDT:USDT": _bars(rows)},
                     drop_bars=R.DROP_BARS, drop_pct=R.DROP_PCT, vol_mult=R.VOL_MULT,
                     tp=R.TAKE_PROFIT, sl=R.STOP_LOSS, hold=R.MAX_HOLD_BARS)
    assert (df["why"] == "POSTONLY").any(), "지정가 아래에서 열리면 체결되면 안 된다"
    assert df[df.filled == 1].empty


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
