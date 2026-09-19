"""물타기(DCA·평단 낮추기) 전략 엔진.

사용자가 원래 하던 전략을 그대로 구현한다: 사고, 떨어지면 더 사고,
평단 대비 조금만 오르면 전량 익절. 승률은 거의 100% 가 나온다.
승률이 100% 인데도 돈을 잃는 이유를 숫자로 보기 위한 코드다.

핵심 설계 원칙
- 지정가 주문은 가격이 닿으면 체결된다고 본다(봉 내부 체결). 단 같은 봉에서
  추가매수가 일어나면 그 봉에서는 익절을 허용하지 않는다. 추가매수로 낮아진
  평단은 봉 앞부분 가격으로 채울 수 없기 때문이다(미래참조 방지).
- 갭은 불리하게 처리한다. 시가가 추가매수 가격보다 아래면 시가에 산다.
- 청산은 봉 저가 기준으로 판정한다.
- 무레버리지(lev=1)면 청산이 원천적으로 없다. 대신 손실은 평가손으로 남는다.

    ./.venv-research/bin/python backtest/averaging.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
STOCKS = ROOT / "data" / "stocks"
OHLCV = ROOT / "data" / "ohlcv"


# ----------------------------------------------------------------------------- 파라미터


@dataclass(frozen=True)
class AvgParams:
    """물타기 설정. 기본값은 3commas/바이낸스 DCA 봇에서 흔히 쓰는 값."""

    tp: float = 0.015  # 평단 대비 익절 목표
    step: float = 0.025  # 직전 매수가 대비 이만큼 빠지면 추가매수
    step_scale: float = 1.15  # 내려갈수록 간격을 넓힌다
    mult: float = 1.5  # 추가매수 금액 배수 (1.0=균등, 2.0=마틴게일)
    max_adds: int = 8  # 최대 추가매수 횟수
    base: float = 0.02  # 첫 매수 = 진입 시점 자기자본의 이 비율
    lev: float = 1.0  # 명목/자기자본 상한. 1.0 = 현금거래
    mmr: float = 0.005  # 유지증거금률 (선물 기준. 주식 신용은 0.25)
    cost: float = 0.0005  # 편도 체결비용 (수수료+슬리피지)
    tax_sell: float = 0.0  # 매도 시 거래세 (한국 주식 0.0015)
    hard_stop: float | None = None  # 평단 대비 이만큼 빠지면 손절. None=존버
    cooldown: int = 1  # 청산 후 재진입까지 대기 봉수
    cash_yield: float = 0.03  # 놀고 있는 현금의 연이율 (CMA·MMF·USDT 적립)
    borrow_rate: float = 0.06  # 빌린 돈의 연이율 (신용·선물 자금조달)
    fill: str = "intrabar"  # "intrabar"=지정가가 봉 안에서 체결 / "close"=종가에만 체결
    div_yield: float = 0.0  # 보유 중 받는 배당 연이율 (가격은 배당 미조정이어야 한다)

    def coverage(self) -> float:
        """사다리가 감당하는 최대 하락폭. 이걸 넘으면 전략이 깨진다."""
        price = 1.0
        step = self.step
        for _ in range(self.max_adds):
            price *= 1 - step
            step *= self.step_scale
        return 1 - price

    def ladder_cost(self) -> float:
        """사다리를 다 쓸 때 필요한 자본 = 첫 매수의 몇 배인지."""
        return sum(self.mult**i for i in range(self.max_adds + 1))

    def sized(self, deploy: float = 1.0) -> "AvgParams":
        """사다리를 끝까지 써서 자본의 deploy 배를 정확히 쓰도록 첫 매수 크기를 맞춘다.

        설정마다 투입 자본이 달라지면 비교가 무의미해지므로 항상 이걸 거쳐서 쓴다.
        """
        return replace(self, base=deploy / self.ladder_cost())


# ----------------------------------------------------------------------------- 결과


@dataclass
class Result:
    equity: pd.Series
    trades: pd.DataFrame
    blown: pd.Timestamp | None  # 청산으로 계좌가 끝난 날
    max_adds_used: int
    bars_in_market: int
    bars: int
    exposure: pd.Series | None = None  # 봉별 (포지션 명목 / 자기자본)

    @property
    def avg_exposure(self) -> float:
        """평균 투입 비중. 이 값이 낮으면 수익의 대부분은 예금이자다."""
        return float(self.exposure.mean()) if self.exposure is not None else float("nan")

    @property
    def max_exposure(self) -> float:
        return float(self.exposure.max()) if self.exposure is not None else float("nan")

    # -- 지표 -------------------------------------------------------------
    @property
    def final(self) -> float:
        return float(self.equity.iloc[-1])

    @property
    def cagr(self) -> float:
        yrs = (self.equity.index[-1] - self.equity.index[0]).days / 365.25
        if yrs <= 0 or self.final <= 0:
            return -1.0
        return (self.final / float(self.equity.iloc[0])) ** (1 / yrs) - 1

    @property
    def mdd(self) -> float:
        eq = self.equity
        return float((eq / eq.cummax() - 1).min())

    @property
    def win_rate(self) -> float:
        if self.trades.empty:
            return float("nan")
        return float((self.trades["pnl"] > 0).mean())

    @property
    def pf(self) -> float:
        if self.trades.empty:
            return float("nan")
        win = self.trades.loc[self.trades["pnl"] > 0, "pnl"].sum()
        loss = -self.trades.loc[self.trades["pnl"] < 0, "pnl"].sum()
        if loss <= 0:
            return float("inf") if win > 0 else float("nan")
        return float(win / loss)

    @property
    def worst_trade(self) -> float:
        if self.trades.empty:
            return float("nan")
        return float(self.trades["pnl_pct"].min())

    @property
    def underwater_days(self) -> int:
        eq = self.equity
        peak = eq.cummax()
        under = eq < peak * 0.999
        if not under.any():
            return 0
        best = run = 0
        prev = None
        for ts, flag in under.items():
            if flag:
                run += (ts - prev).days if prev is not None else 1
                best = max(best, run)
            else:
                run = 0
            prev = ts
        return int(best)


# ----------------------------------------------------------------------------- 엔진


def simulate(
    df: pd.DataFrame,
    p: AvgParams,
    equity0: float = 10_000.0,
    allow: np.ndarray | None = None,
) -> Result:
    """물타기 시뮬레이션. df 는 date 인덱스에 open/high/low/close 컬럼.

    allow: 신규 진입을 허용하는 봉 마스크(추세 필터 등). 이미 잡은 포지션은 계속 관리한다.

    한 봉의 처리 순서: ①예약된 진입을 시가에 체결 → ②추가매수 → ③청산 →
    ④손절 → ⑤익절 → ⑥종가로 자산 기록 → ⑦비어 있으면 다음 봉 진입 예약.
    """
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    idx = df.index
    n = len(df)
    gate = np.ones(n, bool) if allow is None else np.asarray(allow, bool)

    cash = equity0
    qty = 0.0
    basis = 0.0  # 매수에 쓴 총액 (비용 제외)
    adds = 0  # 이번 포지션에서 쓴 추가매수 횟수
    next_add = math.inf
    cur_step = p.step
    ladder_equity = equity0  # 포지션 시작 시점 자기자본 = 사다리 크기 기준
    trade_open_equity = equity0
    entry_i = 0
    wait = 0
    pending = False  # 다음 봉 시가에 신규 진입

    eq_curve = np.zeros(n)
    expo = np.zeros(n)
    trades: list[dict] = []
    blown: pd.Timestamp | None = None
    max_adds_used = 0
    in_market = 0

    def buy(price: float, notional: float) -> None:
        nonlocal cash, qty, basis
        qty += notional / price
        basis += notional
        cash -= notional * (1 + p.cost)

    def close_all(price: float, i: int, reason: str) -> None:
        nonlocal cash, qty, basis, adds, next_add, cur_step, wait
        cash += qty * price * (1 - p.cost - p.tax_sell)
        pnl = cash - trade_open_equity
        trades.append(
            {
                "open": idx[entry_i],
                "close": idx[i],
                "bars": i - entry_i,
                "adds": adds,
                "avg": basis / qty,
                "exit": price,
                "pnl": pnl,
                "pnl_pct": pnl / trade_open_equity,
                "reason": reason,
            }
        )
        qty = 0.0
        basis = 0.0
        adds = 0
        next_add = math.inf
        cur_step = p.step
        wait = p.cooldown

    # 달력 기준 경과일. 주식은 주말·휴장이 있으므로 봉 수로 세면 안 된다.
    days = np.diff(idx.to_numpy().astype("datetime64[D]").astype(int), prepend=0)
    days[0] = 0

    for i in range(n):
        # ⓪ 현금 이자 / 차입 이자 / 보유 배당
        if days[i] > 0:
            if cash != 0.0:
                rate = p.cash_yield if cash > 0 else p.borrow_rate
                cash *= (1 + rate) ** (days[i] / 365.25)
            if qty > 0 and p.div_yield:
                cash += qty * c[i - 1] * p.div_yield * days[i] / 365.25

        # ① 예약된 신규 진입 ------------------------------------------------
        if pending:
            pending = False
            ladder_equity = cash
            trade_open_equity = cash
            entry_i = i
            size = p.base * ladder_equity
            if size > 0 and cash > 0:
                buy(o[i], size)
                adds = 0
                cur_step = p.step
                next_add = o[i] * (1 - cur_step)

        if qty > 0:
            in_market += 1
            added_here = False
            # 체결 모드에 따라 판정에 쓰는 가격이 달라진다.
            # intrabar: 지정가 주문이 봉 안에서 닿으면 체결 (현실적이지만 낙관적일 수 있음)
            # close:    종가만 보고 판정·체결 (봉 내부 경로 가정을 완전히 제거)
            conservative = p.fill == "close"
            trig_low = c[i] if conservative else low[i]
            trig_high = c[i] if conservative else h[i]

            # ② 추가매수 — 한 봉에서 여러 단을 통과할 수 있다(갭하락)
            while adds < p.max_adds and trig_low <= next_add:
                fill = c[i] if conservative else min(next_add, o[i])
                eq_now = cash + qty * fill
                size = p.base * ladder_equity * (p.mult ** (adds + 1))
                if qty * fill + size > p.lev * max(eq_now, 1e-9):
                    break  # 레버리지 한도에 막혀 더 못 산다
                if size > cash + (p.lev - 1) * max(eq_now, 1e-9):
                    break  # 동원 가능한 현금 초과
                buy(fill, size)
                adds += 1
                added_here = True
                max_adds_used = max(max_adds_used, adds)
                cur_step *= p.step_scale
                next_add = fill * (1 - cur_step)
                if conservative:
                    break  # 종가 모드에서는 한 봉에 한 단만

            avg = basis / qty

            # ③ 청산 — 무레버리지면 발생하지 않는다.
            liq_px = c[i] if conservative else low[i]
            if p.lev > 1.0 and cash + qty * liq_px <= qty * liq_px * p.mmr:
                trades.append(
                    {
                        "open": idx[entry_i],
                        "close": idx[i],
                        "bars": i - entry_i,
                        "adds": adds,
                        "avg": avg,
                        "exit": liq_px,
                        "pnl": -trade_open_equity,
                        "pnl_pct": -1.0,
                        "reason": "청산",
                    }
                )
                cash = qty = basis = 0.0
                blown = idx[i]
                eq_curve[i:] = 0.0
                break

            # ④ 손절
            if p.hard_stop is not None:
                stop_px = avg * (1 - p.hard_stop)
                if trig_low <= stop_px:
                    close_all(c[i] if conservative else min(stop_px, o[i]), i, "손절")

            # ⑤ 익절 — 같은 봉에서 물탔으면 미룬다(낮아진 평단은 미래참조)
            if qty > 0 and not added_here:
                tp_px = avg * (1 + p.tp)
                if trig_high >= tp_px:
                    close_all(c[i] if conservative else max(tp_px, o[i]), i, "익절")

        # ⑥ 종가 기준 자산·투입비중
        eq_curve[i] = cash + qty * c[i]
        expo[i] = (qty * c[i]) / eq_curve[i] if eq_curve[i] > 0 else 0.0

        # ⑦ 다음 봉 진입 예약
        if qty == 0:
            if wait > 0:
                wait -= 1
            elif i + 1 < n and gate[i]:
                pending = True

    equity = pd.Series(eq_curve, index=idx, name="equity")
    tr = pd.DataFrame(trades)
    return Result(
        equity=equity,
        trades=tr,
        blown=blown,
        max_adds_used=max_adds_used,
        bars_in_market=in_market,
        bars=len(df),
        exposure=pd.Series(expo, index=idx, name='exposure'),
    )


def buy_hold(df: pd.DataFrame, equity0: float = 10_000.0) -> pd.Series:
    c = df["close"]
    return equity0 * c / float(c.iloc[0])


# ----------------------------------------------------------------------------- 데이터


def load_stock(fname: str) -> pd.DataFrame:
    df = pd.read_csv(STOCKS / fname, parse_dates=["date"], index_col="date")
    return df[["open", "high", "low", "close"]].dropna()


def load_crypto(fname: str) -> pd.DataFrame:
    df = pd.read_csv(OHLCV / fname)
    df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("date").sort_index()
    return df[["open", "high", "low", "close"]].dropna()


__all__ = [
    "AvgParams",
    "Result",
    "simulate",
    "buy_hold",
    "load_stock",
    "load_crypto",
    "replace",
]
