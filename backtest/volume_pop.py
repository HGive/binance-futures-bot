"""
volume_pop 포트폴리오 백테스트 (15분봉, 롱 전용).

STRATEGY_RULES.md 3.1 — 상수/신호는 전략 모듈에서만 (S.X 참조).
STRATEGY_RULES.md 4.1 — 지정가/시장가를 구분해 수수료·슬리피지 반영, 펀딩 차감.
STRATEGY_RULES.md 4.5 — 앞 70% 개발 / 뒤 30% 검증.
"""
import os, sys, glob, argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.volume_pop as S  # noqa: E402
from backtest import data as dataio  # noqa: E402

MIN_BARS = 20000        # 약 7개월 이상 이력


def load_all(min_bars=MIN_BARS):
    syms, out = [], {}
    for path in sorted(glob.glob(os.path.join(dataio.CACHE_DIR, "*_15m.csv"))):
        df = pd.read_csv(path)
        if len(df) < min_bars:
            continue
        sym = os.path.basename(path).replace("_15m.csv", "").replace("_", "/").replace("-", ":")
        out[sym] = df
        syms.append(sym)
    return syms, out


def build_events(symbols, data, pre):
    """(timestamp, symbol, bar_index) 형태의 급등 신호 목록."""
    ev = []
    for s in symbols:
        n = len(data[s])
        ts = data[s]["timestamp"].values
        last = -10**9
        start = S.BARS_PER_DAY * 30
        for i in range(start, n - 1):
            if i - last < S.COOLDOWN_BARS:
                continue
            if S.pop_signal(pre[s], i):
                ev.append((ts[i], s, i))
                last = i
    ev.sort()
    return ev


class Pending:
    __slots__ = ("sym", "limit", "expire_i", "i")
    def __init__(self, sym, limit, i, expire_i):
        self.sym, self.limit, self.i, self.expire_i = sym, limit, i, expire_i


class Position:
    __slots__ = ("sym", "qty", "entry", "margin", "open_i", "open_ts")
    def __init__(self, sym, qty, entry, margin, open_i, open_ts):
        self.sym, self.qty, self.entry, self.margin = sym, qty, entry, margin
        self.open_i, self.open_ts = open_i, open_ts


def run(symbols, data, pre, events, t0, t1, seed=1000.0, random_entry=False, rng=None):
    arr = {s: {k: data[s][k].values for k in ("timestamp", "open", "high", "low", "close")}
           for s in symbols}
    tsidx = {s: {t: i for i, t in enumerate(arr[s]["timestamp"])} for s in symbols}
    timeline = np.array(sorted({t for s in symbols for t in arr[s]["timestamp"]}))
    timeline = timeline[(timeline >= t0) & (timeline <= t1)]

    ev_in = [e for e in events if t0 <= e[0] <= t1]
    ep = 0
    cash, pendings, positions, trades, curve = seed, {}, {}, [], []
    funding_bar = S.FUNDING_PER_DAY / S.BARS_PER_DAY

    for t in timeline:
        # ---------- 1. 보유 포지션 ----------
        for sym in list(positions):
            p = positions[sym]
            i = tsidx[sym].get(t)
            if i is None:
                continue
            a = arr[sym]
            hi, lo, cl = a["high"][i], a["low"][i], a["close"][i]
            cash -= p.qty * cl * funding_bar
            tp, sl, lq = S.take_profit_price(p.entry), S.stop_price(p.entry), S.liq_price(p.entry)
            reason = px = None
            if a["open"][i] <= lq:
                reason, px = "LIQUIDATION", lq
            elif lo <= sl:                                  # 불리한 쪽 먼저 (보수적)
                reason, px = "STOP", min(sl, a["open"][i])
            elif hi >= tp:
                reason, px = "TP", tp
            elif i - p.open_i >= S.MAX_HOLD_BARS:
                reason, px = "TIME", cl
            if reason:
                if reason == "LIQUIDATION":
                    pnl = -p.margin
                else:
                    if reason == "TP":                      # 지정가 → 메이커, 슬리피지 없음
                        fill, fee = px, S.MAKER_FEE
                    else:                                   # 시장가
                        fill, fee = px * (1 - S.SLIPPAGE), S.TAKER_FEE
                    pnl = p.qty * (fill - p.entry) - p.qty * fill * fee
                    pnl = max(pnl, -p.margin)
                    cash += p.margin + pnl
                trades.append({"symbol": sym, "entry_ts": p.open_ts, "exit_ts": t,
                               "hold_bars": i - p.open_i, "entry": p.entry, "exit": px,
                               "reason": reason, "margin": p.margin, "pnl": pnl,
                               "roi": pnl / p.margin})
                del positions[sym]

        # ---------- 2. 대기 중인 지정가 ----------
        for sym in list(pendings):
            q = pendings[sym]
            i = tsidx[sym].get(t)
            if i is None:
                continue
            if i > q.expire_i or sym in positions:
                del pendings[sym]; continue
            if i <= q.i:
                continue
            if arr[sym]["low"][i] <= q.limit:
                if len(positions) < S.MAX_CONCURRENT:
                    eq = cash + sum(pp.margin for pp in positions.values())
                    margin = max(eq, 0) * S.TICKER_MARGIN_PCT
                    if cash >= margin > 0:
                        entry = q.limit
                        qty = margin * S.LEVERAGE / entry
                        cash -= margin + qty * entry * S.MAKER_FEE
                        positions[sym] = Position(sym, qty, entry, margin, i, t)
                del pendings[sym]

        # ---------- 3. 새 급등 신호 → 지정가 예약 ----------
        while ep < len(ev_in) and ev_in[ep][0] == t:
            _, sym, i = ev_in[ep]; ep += 1
            if sym in positions or sym in pendings or len(pendings) >= S.MAX_CONCURRENT:
                continue
            if random_entry and rng.random() > 0.5:
                continue
            pc = arr[sym]["close"][i]
            pendings[sym] = Pending(sym, S.limit_price(pc), i, i + S.FILL_WINDOW_BARS)
        while ep < len(ev_in) and ev_in[ep][0] < t:
            ep += 1

        # ---------- 4. 평가 ----------
        eq = cash
        for s, pp in positions.items():
            i = tsidx[s].get(t)
            eq += pp.margin if i is None else max(pp.margin + pp.qty * (arr[s]["close"][i] - pp.entry), 0)
        curve.append((t, eq))

    return pd.DataFrame(trades), pd.DataFrame(curve, columns=["ts", "equity"])


def summarize(tr, eq, seed):
    if not len(eq):
        return {}
    final = eq["equity"].iloc[-1]
    peak = eq["equity"].cummax()
    wins = tr[tr["pnl"] > 0] if len(tr) else tr
    loss = tr[tr["pnl"] <= 0] if len(tr) else tr
    gp = float(wins["pnl"].sum()) if len(wins) else 0.0
    gl = float(-loss["pnl"].sum()) if len(loss) else 0.0
    by = tr.groupby("symbol")["pnl"].sum() if len(tr) else pd.Series(dtype=float)
    return {"trades": len(tr), "return_pct": (final/seed-1)*100, "final": final,
            "mdd_pct": float(((eq["equity"]-peak)/peak).min())*100,
            "win_rate": (len(wins)/len(tr)*100) if len(tr) else 0,
            "profit_factor": (gp/gl) if gl > 0 else float("inf"),
            "avg_hold_h": float(tr["hold_bars"].mean())/4 if len(tr) else 0,
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
    print(f"  평균 보유     : {s['avg_hold_h']:.1f}시간")
    print(f"  수익 심볼     : {s['sym_win']}/{s['sym_n']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["in", "out", "all"], default="in")
    ap.add_argument("--split", type=float, default=0.7)
    ap.add_argument("--seed-usdt", type=float, default=1000.0)
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    symbols, data = load_all()
    pre = {s: S.precompute(data[s]) for s in symbols}
    print(f"심볼 {len(symbols)}종")
    events = build_events(symbols, data, pre)
    print(f"급등 신호 {len(events)}건")

    all_ts = np.array(sorted({t for s in symbols for t in data[s]["timestamp"].values}))
    cut = all_ts[int(len(all_ts)*args.split)]
    t0, t1 = {"in": (all_ts[0], cut), "out": (cut, all_ts[-1]), "all": (all_ts[0], all_ts[-1])}[args.part]
    print(f"{args.part}: {pd.Timestamp(t0, unit='ms').date()} ~ {pd.Timestamp(t1, unit='ms').date()}")

    tr, eq = run(symbols, data, pre, events, t0, t1, args.seed_usdt)
    report(summarize(tr, eq, args.seed_usdt), f"volume_pop [{args.part}]")
    if len(tr):
        print("\n  청산 사유별:")
        for r, g in tr.groupby("reason"):
            print(f"    {r:12s} {len(g):5d}건  합계 {g['pnl'].sum():+9.1f}  평균ROI {g['roi'].mean()*100:+6.2f}%")
        if args.dump:
            tr.to_csv(args.dump, index=False); print(f"  → {args.dump}")
    if args.baseline:
        tb, eb = run(symbols, data, pre, events, t0, t1, args.seed_usdt,
                     random_entry=True, rng=np.random.default_rng(7))
        report(summarize(tb, eb, args.seed_usdt), f"신호 절반 무작위 제거 [{args.part}]")
