"""SWING_DCA 전략·봇 테스트.

가장 중요한 것은 **계정 공유 가드**다. 같은 계정에서 TREND_VT 가 BTC·ETH 롱을
들고 있는데 SWING_DCA 가 그걸 자기 포지션으로 오인하면 서로의 손절 주문을 지운다.
"""
import asyncio
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import strategies.swing_dca as S  # noqa: E402
from live import swing_bot  # noqa: E402


# ------------------------------------------------------------------ 가격 계산


def test_price_levels():
    p = S.SParams(tp=0.02, step=0.15, stop=0.50)
    assert S.tp_price(100, p) == pytest.approx(102.0)
    assert S.add_price(100, p) == pytest.approx(85.0)
    assert S.stop_price(100, p) == pytest.approx(50.0)


def test_levels_ordered():
    """손절 < 물타기 < 평단 < 익절. 이 순서가 깨지면 전략이 성립하지 않는다."""
    p = S.LIVE
    avg = 123.45
    assert S.stop_price(avg, p) < S.add_price(avg, p) < avg < S.tp_price(avg, p)


def test_first_buy_fraction_no_leverage():
    """사다리를 다 써도 자본을 넘지 않아야 한다 (레버리지 없음)."""
    for slots in (1, 5, 10, 20):
        p = S.SParams(slots=slots, add_mult=1.0)
        f = S.first_buy_fraction(p)
        assert f * (1 + p.add_mult) * slots == pytest.approx(1.0)


def test_has_added():
    """취득원가(qty × 평균진입가)로 판정한다. 가격이 움직여도 이 값은 안 변한다."""
    p = S.SParams(slots=10, add_mult=1.0)
    budget = 100.0                    # 칸 예산
    assert not S.has_added(50.0, budget, p)      # 첫 매수만 (원가 50)
    assert S.has_added(100.0, budget, p)         # 물타기까지 (원가 100)
    assert not S.has_added(60.0, budget, p)      # 부분체결 조금
    assert S.has_added(90.0, budget, p)          # 부분체결 거의 다


def test_cost_basis_is_price_independent():
    """취득원가는 시세와 무관하다 — 이게 물탔는지 판정의 근거다."""
    assert S.cost_basis(10.0, 5.0) == pytest.approx(50.0)
    # 평균진입가는 그대로인데 시세만 2배가 되어도 원가는 그대로다
    assert S.cost_basis(10.0, 5.0) == pytest.approx(50.0)


def test_remaining_add_self_corrects():
    """부분체결이 나도 남은 예산만큼만 더 넣는다. 예산을 넘지 않는다."""
    budget = 100.0
    assert S.remaining_add(10.0, 5.0, budget) == pytest.approx(50.0)   # 원가 50 → 50 남음
    assert S.remaining_add(15.0, 5.0, budget) == pytest.approx(25.0)   # 원가 75 → 25 남음
    assert S.remaining_add(20.0, 5.0, budget) == pytest.approx(0.0)    # 원가 100 → 끝
    assert S.remaining_add(30.0, 5.0, budget) == pytest.approx(0.0)    # 초과해도 음수 아님


# ------------------------------------------------------------------ 미래참조


def _panel(n=600, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    out = {}
    for i, name in enumerate(["A", "B", "C"]):
        r = rng.normal(0.0005, 0.03 + i * 0.01, n)
        out[name] = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
    return pd.DataFrame(out)


def test_entry_signal_no_lookahead():
    """앞부분만 잘라 계산해도 그 구간 신호가 같아야 한다."""
    c = _panel()
    full = S.entry_signal(c)
    for cut in (300, 450, 599):
        part = S.entry_signal(c.iloc[:cut])
        pd.testing.assert_frame_equal(full.iloc[:cut], part)


def test_eligible_no_lookahead():
    c = _panel()
    full = S.eligible(c)
    for cut in (300, 450, 599):
        part = S.eligible(c.iloc[:cut])
        pd.testing.assert_frame_equal(full.iloc[:cut], part)


def test_entry_signal_is_two_down_days():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    c = pd.DataFrame({"X": [100.0, 99.0, 98.0, 99.0, 98.0]}, index=idx)
    s = S.entry_signal(c)["X"].tolist()
    #        t0     t1(1일하락)  t2(2일연속)  t3(상승)  t4(1일하락)
    assert s == [False, False, True, False, False]


def test_eligible_rejects_crashers():
    """하루 -20% 가 잦은 종목은 자격에서 빠진다."""
    n = 400
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    calm = 100 * np.exp(np.cumsum(np.full(n, 0.0003)))
    crash = calm.copy()
    for k in (100, 200, 300):
        crash[k:] *= 0.7            # -30% 급락 3회
    c = pd.DataFrame({"CALM": calm, "CRASH": crash}, index=idx)
    e = S.eligible(c, None, S.SParams(min_age=250, max_crashes=1, crash_day=-0.20))
    last = e.index[-1]
    assert bool(e.loc[last, "CALM"])
    assert not bool(e.loc[last, "CRASH"])


def test_eligible_rejects_young():
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    old = pd.Series(100.0 * np.exp(np.cumsum(np.full(n, 0.0002))), index=idx)
    young = old.copy()
    young.iloc[:200] = np.nan       # 100봉만 존재
    c = pd.DataFrame({"OLD": old, "YOUNG": young})
    e = S.eligible(c, None, S.SParams(min_age=250))
    assert bool(e.iloc[-1]["OLD"])
    assert not bool(e.iloc[-1]["YOUNG"])


# ------------------------------------------------------------------ 계정 가드


class FakeState(dict):
    pass


def _bot(owned=None, reserved=None):
    b = swing_bot.SwingBot.__new__(swing_bot.SwingBot)
    b.st = {"owned": list(owned or [])}
    b.reserved = set(reserved or ())
    b.capital = 0.0
    return b


def test_guard_flags_other_bots_long():
    """TREND_VT 의 BTC·ETH 롱은 남의 포지션으로 걸러야 한다."""
    b = _bot(owned=["CAKE/USDT:USDT"])
    pos = [
        {"symbol": "BTC/USDT:USDT", "contracts": 0.02, "side": "long"},
        {"symbol": "ETH/USDT:USDT", "contracts": 0.6, "side": "long"},
        {"symbol": "CAKE/USDT:USDT", "contracts": 20.0, "side": "long"},
    ]
    others = b.foreign_positions(pos)
    assert set(others) == {"BTC/USDT:USDT", "ETH/USDT:USDT"}


def test_guard_allows_own_positions():
    b = _bot(owned=["CAKE/USDT:USDT", "XRP/USDT:USDT"])
    pos = [
        {"symbol": "CAKE/USDT:USDT", "contracts": 20.0, "side": "long"},
        {"symbol": "XRP/USDT:USDT", "contracts": 100.0, "side": "long"},
    ]
    assert b.foreign_positions(pos) == []


def test_guard_flags_any_short():
    """이 전략은 숏을 만들지 않는다. 기록에 있어도 숏이면 남의 것이다."""
    b = _bot(owned=["SOL/USDT:USDT"])
    pos = [{"symbol": "SOL/USDT:USDT", "contracts": 5.0, "side": "short"}]
    assert b.foreign_positions(pos) == ["SOL/USDT:USDT"]


def test_guard_ignores_zero_positions():
    b = _bot(owned=[])
    pos = [{"symbol": "BTC/USDT:USDT", "contracts": 0.0, "side": "long"}]
    assert b.foreign_positions(pos) == []


def test_remember_forget():
    b = _bot()
    b.remember("A/USDT:USDT")
    b.remember("A/USDT:USDT")            # 두 번 넣어도 하나
    assert b.st["owned"] == ["A/USDT:USDT"]
    b.forget("A/USDT:USDT")
    assert b.st["owned"] == []
    b.forget("없는것")                     # 없는 걸 지워도 안 터진다


def test_owned_ever_survives_forget():
    """실현손익을 되짚으려면 청산 뒤에도 종목 이름이 남아 있어야 한다."""
    b = _bot()
    b.remember("A/USDT:USDT")
    b.forget("A/USDT:USDT")
    assert b.st["owned"] == []
    assert b.st["owned_ever"] == ["A/USDT:USDT"]


# ------------------------------------------------------- 계정 공유 (reserved)


def test_reserved_positions_are_tolerated():
    """--reserved 로 '남의 것인 줄 안다'고 선언한 종목은 가드를 막지 않는다."""
    b = _bot(owned=["CAKE/USDT:USDT"],
             reserved=["BTC/USDT:USDT", "ETH/USDT:USDT"])
    pos = [
        {"symbol": "BTC/USDT:USDT", "contracts": 0.02, "side": "long"},
        {"symbol": "ETH/USDT:USDT", "contracts": 0.6, "side": "short"},
        {"symbol": "CAKE/USDT:USDT", "contracts": 20.0, "side": "long"},
    ]
    assert b.foreign_positions(pos) == []


def test_reserved_does_not_whitelist_everything():
    """예약 안 한 남의 포지션은 여전히 걸러야 한다."""
    b = _bot(owned=[], reserved=["BTC/USDT:USDT"])
    pos = [
        {"symbol": "BTC/USDT:USDT", "contracts": 0.02, "side": "long"},
        {"symbol": "SOL/USDT:USDT", "contracts": 5.0, "side": "long"},
    ]
    assert b.foreign_positions(pos) == ["SOL/USDT:USDT"]


def test_positions_only_returns_owned():
    """**가장 위험한 경우.** 자기가 열지 않은 롱을 돌려주면 그 포지션에
    익절·손절 주문을 걸어 다른 봇을 망가뜨린다."""
    async def fake_fetch():
        return [
            {"symbol": "BTC/USDT:USDT", "contracts": 0.02, "side": "long", "entryPrice": 81000.0},
            {"symbol": "CAKE/USDT:USDT", "contracts": 20.0, "side": "long", "entryPrice": 2.4},
        ]

    b = _bot(owned=["CAKE/USDT:USDT"], reserved=["BTC/USDT:USDT"])
    b.dry = False
    b.ex = type("E", (), {"fetch_positions": staticmethod(fake_fetch)})()
    out = asyncio.run(b.positions())
    assert list(out) == ["CAKE/USDT:USDT"]


def test_parse_symbols():
    assert swing_bot.parse_symbols("") == set()
    assert swing_bot.parse_symbols(None) == set()
    assert swing_bot.parse_symbols("BTC/USDT:USDT, ETH/USDT:USDT") == {
        "BTC/USDT:USDT", "ETH/USDT:USDT"}


def test_reserved_symbols_excluded_from_universe():
    """후보 선정에서 예약 종목을 빼는 코드가 있어야 한다.

    이게 빠지면 SWING_DCA 가 BTC 를 골라 TREND_VT 포지션과 합쳐진다.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "live", "swing_bot.py"), encoding="utf-8").read()
    body = src[src.index("async def universe"):src.index("async def equity")]
    assert "self.reserved" in body


# ------------------------------------------------------------------ 주문 수량


class FakeEx:
    def __init__(self, step=0.001):
        self.step = step

    def amount_to_precision(self, sym, amt):
        import ccxt
        q = np.floor(float(amt) / self.step) * self.step
        if q <= 0:
            raise ccxt.InvalidOrder("amount below step")
        return f"{q:.8f}"


def test_precise_amount_below_step_returns_zero():
    """ccxt 는 단위 미만 수량에 예외를 던진다. 그대로 두면 봇이 멈춘다."""
    ex = FakeEx(step=0.001)
    assert swing_bot.precise_amount(ex, "BTC/USDT:USDT", 0.0004) == 0.0
    assert swing_bot.precise_amount(ex, "BTC/USDT:USDT", 0.0025) == pytest.approx(0.002)


# ------------------------------------------------------------------ 마감 봉


def test_closed_daily_drops_unfinished_bar():
    day = 86_400_000
    t0 = 1_700_000_000_000 // day * day
    rows = [[t0 + i * day, 1, 2, 0.5, 1.5, 10] for i in range(4)]
    now = t0 + 3 * day + 1000          # 마지막 봉은 아직 진행 중
    d = swing_bot.closed_daily(rows, now)
    assert len(d) == 3


# ------------------------------------------------------------------ 기대치


def test_documented_expectation_is_honest():
    """머리말에 '보유를 이기지 못한다'가 남아 있어야 한다.

    이 전략은 검증에서 보유 대비 열세였다. 문서에서 그 문장을 지우면
    나중에 기대치를 착각한다.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "strategies", "swing_dca.py"), encoding="utf-8").read()
    assert "보유를 이기지 못한다" in src


# ------------------------------------------------------------------ 조건부 주문 응답


def test_algo_rows_handles_list():
    """바이낸스는 조건부 주문을 **list** 로 준다.

    dict 로만 처리하면 항상 빈 목록이 나와서 '손절이 없다'고 판단하고,
    매 실행마다 손절을 새로 걸고 이전 것을 지운다. 실제로 이 버그가 있었다.
    """
    rows = [{"algoId": "1", "symbol": "BTCUSDT", "stopPrice": "71722.8"}]
    assert swing_bot.SwingBot.algo_rows(rows) == rows


def test_algo_rows_handles_dict():
    rows = [{"algoId": "1", "symbol": "BTCUSDT"}]
    assert swing_bot.SwingBot.algo_rows({"orders": rows}) == rows
    assert swing_bot.SwingBot.algo_rows({}) == []
    assert swing_bot.SwingBot.algo_rows(None) == []


def test_algo_stop_price_reads_both_fields():
    assert swing_bot.SwingBot.algo_stop_price({"stopPrice": "100.5"}) == pytest.approx(100.5)
    assert swing_bot.SwingBot.algo_stop_price({"triggerPrice": "99"}) == pytest.approx(99.0)
    assert swing_bot.SwingBot.algo_stop_price({}) == 0.0


def test_stop_placed_before_old_cancelled():
    """손절은 새로 건 **다음에** 옛것을 지워야 한다. 순서가 반대면 손절 없는 구간이 생긴다."""
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "live", "swing_bot.py"), encoding="utf-8").read()
    body = src[src.index("if not have_stop:"):src.index("if not have_tp:")]
    create = body.index("create_order")
    delete = body.index("DeleteAlgoOrder")
    assert create < delete, "손절 생성이 삭제보다 먼저여야 한다"
