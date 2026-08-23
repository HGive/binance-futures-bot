"""
spike_drought 포트폴리오 백테스트 (일봉, 롱 전용).

STRATEGY_RULES.md 3.1 상수 단일 출처 / 3.3 체결봉 청산 금지 /
4.1 비용 반영 / 4.3 베이스라인 / 4.5 in-out 분리.
"""
import os, sys, glob, argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.spike_drought as S  # noqa: E402
from backtest import data as dataio  # noqa: E402

WARMUP = S.YEAR_WINDOW + 15


def load_all(min_days=430):
    syms, out = [], {}
    for path in sorted(glob.glob(os.path.join(dataio.CACHE_DIR, "*_4h.csv"))):
        df = pd.read_csv(path)
        if len(df) < min_days * 6:
            continue
        d = S.to_daily(df)
        if len(d) < min_days:
            continue
        sym = os.path.basename(path).replace("_4h.csv", "").replace("_", "/").replace("-", ":")
        out[sym] = d
        syms.append(sym)
    return syms, out


class Pos:
    __slots__ = ("sym", "qty", "avg", "entry", "margin", "budget", "added", "open_i", "open_ts")
    def __init__(self, sym, qty, entry, margin, budget, open_i, open_ts):
        self.sym, self.qty, self.avg, self.entry = sym, qty, entry, entry
        self.margin, self.budget, self.added = margin, budget, False
        self.open_i, self.open_ts = open_i, open_ts


def run(symbols, daily, pre, dates, a, b, seed=1000.0, random_entry=False, rng=None):
    cash, positions, trades, curve = seed, {}, [], []
    idx = {s: {t: i for i, t in enumerate(daily[s]["dt"].values)} for s in symbols}

    for di in range(a, b):
        today = dates[di]
        # ---------------- 보유 포지션 ----------------
        for sym in list(positions):
            p = positions[sym]
            i = idx[sym].get(today)
            if i is None or i <= p.open_i:      # 체결봉은 청산 판정에서 제외 (규칙 3.3)
                continue
            row = daily[sym].iloc[i]
            hi, lo, op, cl = row["high"], row["low"], row["open"], row["close"]
            cash -= p.qty * cl * S.FUNDING_PER_DAY

            # 추매 (딱 한 번)
            trig = S.add_trigger_price(p.entry)
            if not p.added and lo <= trig:
                add_margin = p.budget * (1 - S.FIRST_ENTRY_FRAC)
                if cash >= add_margin:
                    fill = min(trig, op) * (1 + S.SLIPPAGE)
                    aq = add_margin * S.LEVERAGE / fill
                    cash -= add_margin + aq * fill * S.FEE_RATE
                    p.avg = (p.qty * p.avg + aq * fill) / (p.qty + aq)
                    p.qty += aq
                    p.margin += add_margin
                p.added = True

            reason = px = None
            if op <= S.liq_price(p.avg):
                reason, px = "LIQUIDATION", S.liq_price(p.avg)
            elif p.added and lo <= S.stop_price(p.avg):
                reason, px = "STOP", min(S.stop_price(p.avg), op)
            elif hi >= S.take_profit_price(p.avg):
                reason, px = "TP", S.take_profit_price(p.avg)
            if reason:
                if reason == "LIQUIDATION":
                    pnl = -p.margin
                else:
                    fill = px * (1 - S.SLIPPAGE)
                    pnl = max(p.qty * (fill - p.avg) - p.qty * fill * S.FEE_RATE, -p.margin)
                    cash += p.margin + pnl
                trades.append({"symbol": sym, "entry_ts": p.open_ts, "exit_ts": today,
                               "hold_days": int((today - p.open_ts)/np.timedelta64(1, "D")),
                               "avg": p.avg, "exit": px, "reason": reason, "added": p.added,
                               "margin": p.margin, "pnl": pnl, "roi": pnl/p.margin})
                del positions[sym]

        # ---------------- 신규 진입 ----------------
        if len(positions) < S.MAX_CONCURRENT:
            eq = cash + sum(pp.margin + pp.qty*(daily[s].iloc[idx[s][today]]["close"] - pp.avg)
                            for s, pp in positions.items() if today in idx[s])
            budget = max(eq, 0) * S.TICKER_MARGIN_PCT
            first = budget * S.FIRST_ENTRY_FRAC
            cands = []
            for sym in symbols:
                if sym in positions:
                    continue
                i = idx[sym].get(today)
                if i is None or i < WARMUP or i + 1 >= len(daily[sym]):
                    continue
                P = pre[sym]
                cl = daily[sym].iloc[i]["close"]
                if not S.scan_ok(P, cl, i):
                    continue
                sig = (rng.random() < 0.01) if random_entry else S.entry_signal(P, i)
                if sig:
                    cands.append((P["drought"][i], sym, i))
            cands.sort(reverse=True)          # 가장 오래 조용했던 것부터
            for _, sym, i in cands:
                if len(positions) >= S.MAX_CONCURRENT or cash < budget:
                    break
                nxt = daily[sym].iloc[i + 1]
                fill = nxt["open"] * (1 + S.SLIPPAGE)
                if fill <= 0:
                    continue
                qty = first * S.LEVERAGE / fill
                cash -= first + qty * fill * S.FEE_RATE
                positions[sym] = Pos(sym, qty, fill, first, budget, i + 1,
                                     np.datetime64(nxt["dt"].to_datetime64()))

        eq = cash
        for s, pp in positions.items():
            i = idx[s].get(today)
            eq += pp.margin if i is None else max(pp.margin + pp.qty*(daily[s].iloc[i]["close"] - pp.avg), 0)
        curve.append((today, eq))

    return pd.DataFrame(trades), pd.DataFrame(curve, columns=["dt", "equity"])


def summarize(tr, eq, seed):
    if not len(eq):
        return {}
    final = eq["equity"].iloc[-1]; peak = eq["equity"].cummax()
    wins = tr[tr["pnl"] > 0] if len(tr) else tr
    loss = tr[tr["pnl"] <= 0] if len(tr) else tr
    gp = float(wins["pnl"].sum()) if len(wins) else 0.0
    gl = float(-loss["pnl"].sum()) if len(loss) else 0.0
    by = tr.groupby("symbol")["pnl"].sum() if len(tr) else pd.Series(dtype=float)
    return {"trades": len(tr), "return_pct": (final/seed-1)*100, "final": final,
            "mdd_pct": float(((eq["equity"]-peak)/peak).min())*100,
            "win_rate": (len(wins)/len(tr)*100) if len(tr) else 0,
            "profit_factor": (gp/gl) if gl > 0 else float("inf"),
            "avg_hold": float(tr["hold_days"].mean()) if len(tr) else 0,
            "sym_win": int((by > 0).sum()), "sym_n": int(len(by))}


def report(s, title):
    print(f"\n=== {title} ===")
    if not s or not s["trades"]:
        print("  트레이드 없음"); return
    print(f"  트레이드      : {s['trades']}")
    print(f"  총수익        : {s['return_pct']:+.2f}%   (최종 {s['final']:,.1f} USDT)")
    print(f"  MDD           : {s['mdd_pct']:.2f}%")
    print(f"  승률          : {s['win_rate']:.1f}%")
    print(f"  Profit Factor : {s['profit_factor']:.2f}")
    print(f"  평균 보유     : {s['avg_hold']:.0f}일")
    print(f"  수익 심볼     : {s['sym_win']}/{s['sym_n']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["in", "out", "all"], default="in")
    ap.add_argument("--split", type=float, default=0.7)
    ap.add_argument("--seed-usdt", type=float, default=1000.0)
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    symbols, daily = load_all()
    pre = {s: S.precompute(daily[s]) for s in symbols}
    dates = np.array(sorted(set(np.concatenate([daily[s]["dt"].values for s in symbols]))))
    # 워밍업(1년)을 뺀 실제 신호 구간을 기준으로 분할한다
    usable = dates[WARMUP:]
    cut = int(len(usable) * args.split)
    lo0 = int(np.searchsorted(dates, usable[0]))
    rng_map = {"in": (lo0, lo0+cut), "out": (lo0+cut, len(dates)), "all": (lo0, len(dates))}
    a, b = rng_map[args.part]
    print(f"심볼 {len(symbols)}종 | {args.part}: "
          f"{pd.Timestamp(dates[a]).date()} ~ {pd.Timestamp(dates[b-1]).date()} ({b-a}일)")

    tr, eq = run(symbols, daily, pre, dates, a, b, args.seed_usdt)
    report(summarize(tr, eq, args.seed_usdt), f"spike_drought [{args.part}]")
    if len(tr):
        print("\n  청산 사유별:")
        for r, g in tr.groupby("reason"):
            print(f"    {r:12s} {len(g):4d}건  합계 {g['pnl'].sum():+9.1f}  평균ROI {g['roi'].mean()*100:+6.1f}%")
        if args.dump:
            tr.to_csv(args.dump, index=False); print(f"  → {args.dump}")
    if args.baseline:
        tb, eb = run(symbols, daily, pre, dates, a, b, args.seed_usdt,
                     random_entry=True, rng=np.random.default_rng(7))
        report(summarize(tb, eb, args.seed_usdt), f"random entry 베이스라인 [{args.part}]")
