"""
"+1% 익절 반복" 전략 검증.

관찰: 아무 때나 사도 98.6% 는 1년 안에 +1% 가 온다 (중앙값 1일).
문제: 못 오는 1.4% 의 1년 낙폭 중앙값이 -81.9% 다. 그 꼬리가 전부를 먹는다.

그래서 이 검증의 핵심은 수익률이 아니라 '꼬리를 어떻게 자르느냐' 다.
  - 시간 손절: N일 안에 목표에 못 가면 정리
  - 가격 손절: -X% 면 정리
  - 진입 선별: 죽어가는 종목을 애초에 안 산다
셋을 조합해서 잰다.
"""
import os, sys, glob, argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.spike_drought import load_all  # noqa: E402

# 지정가로 사고 지정가로 판다 → 양쪽 메이커
FEE_MAKER = 0.0002        # 선물 메이커 0.02%
FEE_TAKER = 0.0005        # 손절은 시장가
SLIP = 0.0005


class Pos:
    __slots__ = ("sym", "qty", "entry", "cost", "open_i", "open_ts", "tp")
    def __init__(self, sym, qty, entry, cost, open_i, open_ts, tp):
        self.sym, self.qty, self.entry, self.cost = sym, qty, entry, cost
        self.open_i, self.open_ts, self.tp = open_i, open_ts, tp


def run(symbols, daily, dates, a, b, seed=1000.0, target=0.01, slots=8,
        time_stop=None, price_stop=None, entry_mode="random", rng=None,
        min_vol=1e6, max_pos=None, trend_filter=False):
    idx = {s: {t: i for i, t in enumerate(daily[s]["dt"].values)} for s in symbols}
    last_day = {s: daily[s]["dt"].values[-1] for s in symbols}
    arr = {s: {k: daily[s][k].values for k in ("open", "high", "low", "close")} for s in symbols}
    qv = {s: daily[s]["qv"].rolling(30).median().values for s in symbols}
    # 1년 범위 내 위치 / 1년 전 대비
    pos1y, ret1y = {}, {}
    for s in symbols:
        d = daily[s]
        lo = d["low"].rolling(365).min().values
        hi = d["high"].rolling(365).max().values
        with np.errstate(invalid="ignore", divide="ignore"):
            pos1y[s] = (d["close"].values - lo) / (hi - lo)
        r = np.full(len(d), np.nan)
        r[365:] = d["close"].values[365:] / d["close"].values[:-365] - 1
        ret1y[s] = r

    cash, positions, trades, curve = seed, {}, [], []
    for di in range(a, b):
        today = dates[di]
        # ---- 보유 ----
        for sym in list(positions):
            p = positions[sym]
            i = idx[sym].get(today)
            if i is None:
                if today > last_day[sym]:                 # 상장폐지
                    px = float(arr[sym]["close"][-1]) * (1 - SLIP)
                    pnl = p.qty * px * (1 - FEE_TAKER) - p.cost
                    cash += p.cost + pnl
                    trades.append({"symbol": sym, "reason": "DELISTED", "pnl": pnl,
                                   "roi": pnl/p.cost, "days": int((today-p.open_ts)/np.timedelta64(1,"D"))})
                    del positions[sym]
                continue
            if i <= p.open_i:
                continue
            a_ = arr[sym]
            held = int((today - p.open_ts) / np.timedelta64(1, "D"))
            reason = px = None
            if price_stop is not None and a_["low"][i] <= p.entry * (1 + price_stop):
                reason, px = "STOP", min(p.entry*(1+price_stop), a_["open"][i]) * (1 - SLIP)
                fee = FEE_TAKER
            elif a_["high"][i] >= p.tp:
                reason, px, fee = "TP", p.tp, FEE_MAKER   # 지정가 익절
            elif time_stop is not None and held >= time_stop:
                reason, px, fee = "TIME", a_["close"][i] * (1 - SLIP), FEE_TAKER
            if reason:
                pnl = p.qty * px * (1 - fee) - p.cost
                cash += p.cost + pnl
                trades.append({"symbol": sym, "reason": reason, "pnl": pnl,
                               "roi": pnl/p.cost, "days": held})
                del positions[sym]

        # ---- 신규 ----
        if len(positions) < slots:
            eq = cash + sum(pp.cost for pp in positions.values())
            budget = max(eq, 0) / slots
            cands = []
            for sym in symbols:
                if sym in positions:
                    continue
                i = idx[sym].get(today)
                if i is None or i < 400 or i+1 >= len(arr[sym]["open"]):
                    continue
                if int((last_day[sym]-today)/np.timedelta64(1,"D")) < 5:
                    continue
                q = qv[sym][i]
                if np.isnan(q) or q < min_vol:
                    continue
                pv = pos1y[sym][i]
                if max_pos is not None and (np.isnan(pv) or pv > max_pos):
                    continue
                if trend_filter:
                    r = ret1y[sym][i]
                    if np.isnan(r) or r < -0.80:      # 1년새 -80% 넘게 빠진 건 제외
                        continue
                key = rng.random() if entry_mode == "random" else -(pv if not np.isnan(pv) else 1)
                cands.append((key, sym, i))
            cands.sort()
            for _, sym, i in cands:
                if len(positions) >= slots or cash < budget or budget <= 0:
                    break
                fill = arr[sym]["open"][i+1] * (1 + SLIP)
                if fill <= 0:
                    continue
                qty = budget / (fill * (1 + FEE_MAKER))
                cash -= budget
                positions[sym] = Pos(sym, qty, fill, budget, i+1,
                                     np.datetime64(daily[sym]["dt"].values[i+1]),
                                     fill * (1 + target))
        eq = cash
        for s, pp in positions.items():
            i = idx[s].get(today)
            eq += pp.cost if i is None else max(pp.qty * arr[s]["close"][i], 0)
        curve.append((today, eq))
    return pd.DataFrame(trades), pd.DataFrame(curve, columns=["dt", "equity"])


def summarize(tr, eq, seed, days):
    if not len(eq):
        return {}
    final = eq["equity"].iloc[-1]; peak = eq["equity"].cummax()
    w = tr[tr.pnl > 0] if len(tr) else tr
    l = tr[tr.pnl <= 0] if len(tr) else tr
    gp = float(w.pnl.sum()) if len(w) else 0.0
    gl = float(-l.pnl.sum()) if len(l) else 0.0
    return {"trades": len(tr), "ret": (final/seed-1)*100,
            "cagr": ((final/seed)**(365/days)-1)*100 if final > 0 else -100,
            "mdd": float(((eq.equity-peak)/peak).min())*100,
            "win": (len(w)/len(tr)*100) if len(tr) else 0,
            "pf": (gp/gl) if gl > 0 else float("inf"),
            "days": float(tr.days.mean()) if len(tr) else 0}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=0.01)
    ap.add_argument("--slots", type=int, default=8)
    args = ap.parse_args()
    symbols, daily = load_all(market="spot", min_days=500)
    dates = np.array(sorted(set(np.concatenate([daily[s]["dt"].values for s in symbols]))))
    a, b = 400, len(dates)
    print(f"심볼 {len(symbols)}종 | {pd.Timestamp(dates[a]).date()} ~ {pd.Timestamp(dates[b-1]).date()}")
    print(f"목표 +{args.target*100:.1f}%, 칸 {args.slots}개, 지정가 익절(수수료 0.02%)\n")
    print(f"{'꼬리 자르기':<28}{'거래':>7}{'총수익':>10}{'연환산':>9}{'MDD':>9}{'승률':>7}{'PF':>7}{'평균일':>7}")
    cfgs = [
        ("없음 (그냥 기다림)",          dict()),
        ("시간손절 30일",              dict(time_stop=30)),
        ("시간손절 7일",               dict(time_stop=7)),
        ("가격손절 -20%",              dict(price_stop=-0.20)),
        ("가격손절 -10%",              dict(price_stop=-0.10)),
        ("시간30일 + 가격-20%",        dict(time_stop=30, price_stop=-0.20)),
        ("시간7일 + 가격-10%",         dict(time_stop=7, price_stop=-0.10)),
        ("+ 급락종목 제외",            dict(time_stop=30, price_stop=-0.20, trend_filter=True)),
    ]
    for nm, kw in cfgs:
        tr, eq = run(symbols, daily, dates, a, b, 1000.0, target=args.target,
                     slots=args.slots, rng=np.random.default_rng(42), **kw)
        s = summarize(tr, eq, 1000.0, b-a)
        print(f"{nm:<28}{s['trades']:>7}{s['ret']:>9.1f}%{s['cagr']:>8.1f}%"
              f"{s['mdd']:>8.1f}%{s['win']:>6.0f}%{s['pf']:>7.2f}{s['days']:>7.1f}", flush=True)
