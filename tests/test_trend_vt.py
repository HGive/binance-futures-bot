"""
TREND_VT 실전 봇과 백테스트가 같은 비중을 내는지, 봇의 순수 계산이 맞는지.
STRATEGY_RULES 3.1 / 3.2 / 3.7. 네트워크 없이 합성 데이터로 돈다.

실데이터 대조(2026-09-15): 거래소 일봉 404일 가격차 0, 최근 60일 비중 차이 8.8e-15.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.trend_vt as S  # noqa: E402
from live.trend_bot import TrendBot, closed_daily, plan  # noqa: E402

DAY = 86_400_000


def synthetic(n=1200, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="D")
    r = rng.normal(0.0005, 0.035, size=(n, 2))
    r[300:420, 0] += 0.004          # 추세 구간을 섞는다
    r[700:800, 1] -= 0.004
    return pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=idx, columns=list(S.IDS))


def ones(close):
    return pd.DataFrame(1.0, index=close.index, columns=close.columns)


def test_history_window_gives_same_weights():
    """봇은 최근 HISTORY_DAYS 일만 받는다. 전체 이력으로 계산한 백테스트 비중과 같아야 한다."""
    close = synthetic()
    full, _ = S.targets(close, ones(close))
    win = close.iloc[-S.HISTORY_DAYS:]
    part, _ = S.targets(win, ones(win))
    last = close.index[-30:]
    assert np.allclose(full.loc[last].values, part.loc[last].values, atol=1e-12)


def test_weights_use_only_past_closes():
    """미래 봉을 바꿔도 과거 비중은 안 변한다 (룩어헤드 없음)."""
    close = synthetic()
    w1, _ = S.targets(close, ones(close))
    bumped = close.copy()
    bumped.iloc[900:] *= 1.5
    w2, _ = S.targets(bumped, ones(bumped))
    assert np.allclose(w1.iloc[:900].values, w2.iloc[:900].values)


def test_caps():
    close = synthetic()
    w, _ = S.targets(close, ones(close))
    assert (w.abs() <= S.LIVE.max_w + 1e-12).all().all()
    assert (w.abs().sum(axis=1) <= S.LIVE.max_gross + 1e-12).all()


def test_trade_mask_band():
    assert not S.trade_mask([0.50], [0.45])[0]      # 10% 차이 < 밴드 25%
    assert S.trade_mask([0.50], [0.30])[0]
    assert S.trade_mask([0.0], [0.10])[0]           # 목표 0 이면 정리
    assert S.trade_mask([-0.2], [0.2])[0]           # 방향 전환


def test_stop_price_is_on_the_losing_side():
    assert S.stop_price(100.0, +1, 0.02) == 100.0 * (1 - S.STOP_K * 0.02)
    assert S.stop_price(100.0, -1, 0.02) == 100.0 * (1 + S.STOP_K * 0.02)


def test_closed_daily_drops_forming_bar():
    now = 10 * DAY + 5 * 3600_000          # 10일째 05시
    rows = [[d * DAY, 1, 1, 1, float(d), 1] for d in range(11)]   # 마지막(10일) 봉은 형성 중
    s = closed_daily(rows, now)
    assert len(s) == 10 and s.iloc[-1] == 9.0


def test_plan_matches_targets_and_sizes_in_units():
    close = synthetic()
    w, _ = S.targets(close, ones(close))
    prices = {sym: float(close[cid].iloc[-1]) for sym, cid in zip(S.SYMBOLS, S.IDS)}
    orders, bar = plan(close, 10_000.0, {}, prices)
    for sym, cid in zip(S.SYMBOLS, S.IDS):
        assert np.isclose(orders[sym]["target_w"], w[cid].iloc[-1])
        assert np.isclose(orders[sym]["target_qty"] * prices[sym], w[cid].iloc[-1] * 10_000.0)
    assert bar == str(close.index[-1].date())


def test_paper_ledger_realizes_pnl_and_fees(tmp_path, monkeypatch):
    import live.trend_bot as TB
    monkeypatch.setattr(TB, "LOG_DIR", str(tmp_path))
    b = TrendBot("paper", 1000.0)
    b.st.update(cash=1000.0, pos={})
    sym = S.SYMBOLS[0]
    b.paper_fill(sym, +1.0, 100.0)            # 롱 1개 @100
    b.paper_fill(sym, -1.0, 110.0)            # 전량 청산 @110
    fee = (100.0 + 110.0) * TB.PAPER_FEE
    assert sym not in b.st["pos"]
    assert np.isclose(b.st["cash"], 1000.0 + 10.0 - fee)
    b.paper_fill(sym, -2.0, 50.0)             # 숏 2 @50
    b.paper_fill(sym, +3.0, 40.0)             # 숏 청산(+20) 후 롱 1 @40
    assert np.isclose(b.st["pos"][sym]["qty"], 1.0) and np.isclose(b.st["pos"][sym]["entry"], 40.0)


def test_ai_gate_is_brake_only():
    from live.trend_bot import apply_gate
    open_ = {"allow_new": True, "size_mult": 1.0}
    assert apply_gate(0.4, 0.1, open_) == 0.4
    assert np.isclose(apply_gate(0.4, 0.1, {"allow_new": True, "size_mult": 0.5}), 0.2)
    block = {"allow_new": False, "size_mult": 1.0}
    assert apply_gate(0.4, 0.0, block) == 0.0          # 새로 열지 않는다
    assert apply_gate(0.4, 0.1, block) == 0.1          # 늘리지 않는다
    assert apply_gate(0.05, 0.1, block) == 0.05        # 줄이는 건 된다
    assert apply_gate(-0.3, 0.1, block) == 0.0         # 뒤집지 않고 정리만


# ── 리뷰 지적 회귀 테스트 (2026-09-15) ───────────────────────────
import asyncio  # noqa: E402


class FakeEx:
    """주문 순서만 기록하는 가짜 거래소."""
    def __init__(self, positions=None, fail_close=False):
        self.calls, self.algos = [], [
            {"algoId": "old-btc", "symbol": "BTCUSDT"}, {"algoId": "keep-eth", "symbol": "ETHUSDT"}]
        self.positions = dict(positions or {})
        self.fail_close = fail_close

    def market(self, sym):
        return {"id": sym.split("/")[0] + "USDT", "limits": {"cost": {"min": 5.0}}}

    def amount_to_precision(self, sym, x):
        import ccxt
        r = np.floor(x * 1000) / 1000
        if r == 0:                                   # ccxt 실제 동작: 단위 미만이면 예외
            raise ccxt.InvalidOrder("amount must be greater than minimum amount precision")
        return f"{r:.3f}"

    def price_to_precision(self, sym, x):
        return f"{x:.2f}"

    async def create_order(self, sym, typ, side, amt, price, params):
        self.calls.append(("create", sym, typ, side, amt, dict(params)))
        if typ == "STOP_MARKET":
            self.algos.append({"algoId": "new", "symbol": self.market(sym)["id"]})
            return {"id": "new"}
        if not (self.fail_close and params.get("reduceOnly")):
            q = self.positions.get(sym, 0.0) + (amt if side == "buy" else -amt)
            self.positions[sym] = q
        return {"id": "m1", "average": 0}

    async def fapiPrivateGetOpenAlgoOrders(self, params):
        return list(self.algos)

    async def fapiPrivateDeleteAlgoOrder(self, params):
        self.calls.append(("delete", params["algoId"]))
        self.algos = [a for a in self.algos if a["algoId"] != params["algoId"]]

    async def fetch_positions(self, syms):
        out = []
        for s in syms:
            q = self.positions.get(s, 0.0)
            if q:
                out.append({"symbol": s, "contracts": abs(q), "side": "long" if q > 0 else "short"})
        return out


def live_bot(ex, tmp_path, monkeypatch):
    import live.trend_bot as TB
    monkeypatch.setattr(TB, "LOG_DIR", str(tmp_path))
    b = TrendBot("testnet")
    b.ex = ex
    return b


def test_stop_is_placed_before_old_one_is_cancelled_and_only_same_symbol(tmp_path, monkeypatch):
    ex = FakeEx()
    b = live_bot(ex, tmp_path, monkeypatch)
    d = {"close": 100.0, "vol_d": 0.02}
    assert asyncio.run(b.refresh_stop(S.SYMBOLS[0], 0.01, d))
    kinds = [c[0] for c in ex.calls]
    assert kinds == ["create", "delete"]                       # 새 손절 먼저, 그 다음 취소
    assert ex.calls[1] == ("delete", "old-btc")                 # ETH 손절과 방금 건 것은 안 건드린다
    assert {a["algoId"] for a in ex.algos} == {"keep-eth", "new"}
    create = ex.calls[0]
    assert create[3] == "sell" and create[5]["reduceOnly"] and np.isclose(create[5]["stopPrice"], 100 * (1 - S.STOP_K * 0.02))


def test_flip_does_not_open_opposite_if_close_did_not_fill(tmp_path, monkeypatch):
    sym = S.SYMBOLS[0]
    ex = FakeEx(positions={sym: 0.01}, fail_close=True)
    b = live_bot(ex, tmp_path, monkeypatch)
    asyncio.run(b.rebalance(sym, {"cur_qty": 0.01, "target_qty": -0.02}, 100_000.0))
    opens = [c for c in ex.calls if c[0] == "create" and not c[5].get("reduceOnly")]
    assert opens == []                                          # 청산이 안 됐으면 숏을 열지 않는다
    assert ex.positions[sym] == 0.01


def test_stop_hit_uses_gap_open():
    from live.trend_bot import stop_hit
    assert stop_hit(+1, 90.0, 95.0, 96.0, 89.0) == 90.0         # 장중에 닿음
    assert stop_hit(+1, 90.0, 85.0, 88.0, 80.0) == 85.0         # 갭 하락: 시가에 체결
    assert stop_hit(-1, 110.0, 115.0, 120.0, 112.0) == 115.0    # 숏 갭 상승
    assert stop_hit(+1, 90.0, 95.0, 99.0, 91.0) is None


def test_backtest_applies_same_stop_rule():
    import backtest.trend as TR
    close = synthetic(n=600)
    qv = ones(close)
    fund = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    o, h, l = close.shift(1).fillna(close), close * 1.01, close * 0.99
    w, _ = S.targets(close, qv)
    vol = S.daily_vol(close)
    i = 500
    j = int(np.argmax(np.abs(w.iloc[i - 1].values)))
    side = np.sign(w.iloc[i - 1].values[j])
    assert side != 0
    stop = S.stop_price(close.iloc[i - 1, j], side, vol.iloc[i - 1, j])
    crash = close.iloc[i - 1, j] * (1 - side * 0.5)             # 반대로 50% 갭
    o.iloc[i, j] = crash
    h.iloc[i, j] = max(crash, close.iloc[i, j]) * 1.01
    l.iloc[i, j] = min(crash, close.iloc[i, j]) * 0.99
    _, st = TR.simulate(close, qv, fund, S.LIVE, ohl=(o, h, l))
    assert st["stops"] >= 1
    try:
        TR.simulate(close, qv, fund, S.LIVE)
        raise AssertionError("ohl 없이 손절 백테스트가 돌면 안 된다")
    except ValueError:
        pass


def test_ai_gate_accepts_trend_vt():
    from live import ai_gate
    assert "trend_vt" in ai_gate.STRATEGIES


def test_refuses_shared_account():
    from live.trend_bot import foreign_positions
    rows = [{"symbol": "BTC/USDT:USDT", "contracts": 0.01}, {"symbol": "HEMI/USDT:USDT", "contracts": 2336},
            {"symbol": "ETH/USDT:USDT", "contracts": 0}]
    assert foreign_positions(rows) == ["HEMI/USDT:USDT"]
    assert foreign_positions(rows[:1]) == []


def test_amount_below_step_is_skipped_not_raised(tmp_path, monkeypatch):
    """0.001 BTC 미만 조정은 건너뛴다. 예외가 나면 하루 실행이 죽고 10분에 5번이면 봇이 멈춘다."""
    sym = S.SYMBOLS[0]
    ex = FakeEx(positions={sym: 0.010})
    b = live_bot(ex, tmp_path, monkeypatch)
    asyncio.run(b.rebalance(sym, {"cur_qty": 0.010, "target_qty": 0.0104}, 78_000.0))
    assert [c for c in ex.calls if c[0] == "create"] == []
    assert b.skipped and b.skipped[0]["amount"] == 0.0
