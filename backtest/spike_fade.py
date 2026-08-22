"""
spike_fade 포트폴리오 백테스트 (숏).

STRATEGY_RULES.md 3.1 — 상수/신호는 전략 모듈에서만. 모듈 참조로 쓴다.
STRATEGY_RULES.md 4.1 — 수수료/슬리피지 반영 (숏 펀딩은 0으로 보수 처리).
STRATEGY_RULES.md 4.5 — 앞 70% 개발 / 뒤 30% 검증.
"""
import os, sys, glob, argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.spike_fade as S  # noqa: E402
from backtest import data as dataio  # noqa: E402

WARMUP = 60


def load_all(timeframe="4h", min_days=400):
    syms, out = [], {}
    for path in sorted(glob.glob(os.path.join(dataio.CACHE_DIR, f"*_{timeframe}.csv"))):
        sym = os.path.basename(path).replace(f"_{timeframe}.csv", "").replace("_", "/").replace("-", ":")
        df = pd.read_csv(path)
        if len(df) < min_days * 6:
            continue
        df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        d = S.to_daily(df)
        if len(d) < min_days:
            continue
        out[sym], _ = d, syms.append(sym)
    return syms, out


class Pos:
    __slots__ = ("sym", "qty", "entry", "margin", "entry_day")

    def __init__(self, sym, qty, entry, margin, entry_day):
        self.sym, self.qty, self.entry, self.margin, self.entry_day = sym, qty, entry, margin, entry_day


def run(symbols, daily, pre, dates, a, b, seed=1000.0, btc_up=None,
        random_entry=False, rng=None):
    cash, positions, trades, curve = seed, {}, [], []
    idx = {s: {d: i for i, d in enumerate(daily[s]["dt"].values)} for s in symbols}
    last_exit = {}

    for di in range(a, b):
        today = dates[di]

        # ---------- 보유 포지션 ----------
        for sym in list(positions):
            p = positions[sym]
            i = idx[sym].get(today)
            if i is None:
                continue
            row = daily[sym].iloc[i]
            hi, lo, op = row["high"], row["low"], row["open"]
            cash += p.qty * row["close"] * S.FUNDING_PER_DAY   # 숏은 받는 쪽 (기본 0)

            lq, stop, tp = S.liq_price(p.entry), S.hard_stop_price(p.entry), S.take_profit_price(p.entry)
            held = int((today - p.entry_day) / np.timedelta64(1, "D"))
            closed = exit_px = None
            if op >= lq:
                closed, exit_px = "LIQUIDATION", max(op, lq)
            elif hi >= stop:
                closed, exit_px = "STOP", max(stop, op)        # 갭상승 시 시가 체결
            elif lo <= tp:
                closed, exit_px = "TP", min(tp, op)
            elif held >= S.MAX_HOLD_DAYS:
                closed, exit_px = "TIME", row["close"]

            if closed:
                if closed == "LIQUIDATION":
                    pnl = -p.margin
                else:
                    fill = exit_px * (1 + S.SLIPPAGE)
                    pnl = p.qty * (p.entry - fill) - p.qty * fill * S.FEE_RATE
                    pnl = max(pnl, -p.margin)
                    cash += p.margin + pnl
                trades.append({"symbol": sym, "entry_day": p.entry_day, "exit_day": today,
                               "hold_days": held, "entry": p.entry, "exit": exit_px,
                               "reason": closed, "margin": p.margin, "pnl": pnl,
                               "roi": pnl / p.margin})
                last_exit[sym] = today
                del positions[sym]

        # ---------- 신규 진입 ----------
        if len(positions) < S.MAX_CONCURRENT:
            eq_now = cash + sum(
                pp.margin + pp.qty * (pp.entry - daily[s].iloc[idx[s][today]]["close"])
                for s, pp in positions.items() if today in idx[s])
            margin = max(eq_now, 0) * S.TICKER_MARGIN_PCT
            mkt_up = bool(btc_up.get(today, False)) if btc_up else True
            cands = []
            for sym in symbols:
                if sym in positions:
                    continue
                le = last_exit.get(sym)
                if le is not None and int((today - le) / np.timedelta64(1, "D")) < S.COOLDOWN_DAYS:
                    continue
                i = idx[sym].get(today)
                if i is None or i < WARMUP or i + 1 >= len(daily[sym]):
                    continue
                P = pre[sym]
                sig = (rng.random() < 0.0015 and mkt_up) if random_entry else S.entry_signal(P, i, mkt_up)
                if sig:
                    cands.append((P["runup"][i], sym, i))
            cands.sort(reverse=True)          # 가장 크게 튄 것부터
            for _, sym, i in cands:
                if len(positions) >= S.MAX_CONCURRENT or cash < margin:
                    break
                nxt = daily[sym].iloc[i + 1]
                fill = nxt["open"] * (1 - S.SLIPPAGE)
                if fill <= 0:
                    continue
                qty = margin * S.LEVERAGE / fill
                cash -= margin + qty * fill * S.FEE_RATE
                positions[sym] = Pos(sym, qty, fill, margin,
                                     np.datetime64(nxt["dt"].to_datetime64()))

        eq = cash
        for s, pp in positions.items():
            i = idx[s].get(today)
            eq += pp.margin if i is None else max(pp.margin + pp.qty * (pp.entry - daily[s].iloc[i]["close"]), 0)
        curve.append((today, eq))

    return pd.DataFrame(trades), pd.DataFrame(curve, columns=["dt", "equity"])


def summarize(tr, eq, seed):
    if not len(eq):
        return {}
    final = eq["equity"].iloc[-1]
    peak = eq["equity"].cummax()
    wins = tr[tr["pnl"] > 0] if len(tr) else tr
    loss = tr[tr["pnl"] <= 0] if len(tr) else tr
    gp = float(wins["pnl"].sum()) if len(wins) else 0.0
    gl = float(-loss["pnl"].sum()) if len(loss) else 0.0
    by_sym = tr.groupby("symbol")["pnl"].sum() if len(tr) else pd.Series(dtype=float)
    return {"trades": len(tr), "return_pct": (final/seed-1)*100, "final": final,
            "mdd_pct": float(((eq["equity"]-peak)/peak).min())*100,
            "win_rate": (len(wins)/len(tr)*100) if len(tr) else 0,
            "profit_factor": (gp/gl) if gl > 0 else float("inf"),
            "avg_hold": float(tr["hold_days"].mean()) if len(tr) else 0,
            "sym_win": int((by_sym > 0).sum()), "sym_n": int(len(by_sym))}


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
    cut = int(len(dates) * args.split)
    a, b = {"in": (0, cut), "out": (cut, len(dates)), "all": (0, len(dates))}[args.part]
    btc_up = S.btc_regime(daily["BTC/USDT:USDT"])
    print(f"심볼 {len(symbols)}종 | {args.part}: {pd.Timestamp(dates[a]).date()} ~ {pd.Timestamp(dates[b-1]).date()} ({b-a}일)")

    tr, eq = run(symbols, daily, pre, dates, a, b, args.seed_usdt, btc_up)
    report(summarize(tr, eq, args.seed_usdt), f"spike_fade [{args.part}]")
    if len(tr):
        print("\n  청산 사유별:")
        for r, g in tr.groupby("reason"):
            print(f"    {r:12s} {len(g):4d}건  합계 {g['pnl'].sum():+9.1f}  평균ROI {g['roi'].mean()*100:+6.1f}%")
        if args.dump:
            tr.to_csv(args.dump, index=False); print(f"  → {args.dump}")
    if args.baseline:
        tb, eb = run(symbols, daily, pre, dates, a, b, args.seed_usdt, btc_up,
                     random_entry=True, rng=np.random.default_rng(7))
        report(summarize(tb, eb, args.seed_usdt), f"random entry 베이스라인 [{args.part}]")
