"""
shooting_reversion 포트폴리오 백테스트.

STRATEGY_RULES.md 3.1 — 상수/신호 함수는 전략 파일에서 import 한다. 재정의 금지.
STRATEGY_RULES.md 4.1 — 수수료/슬리피지/펀딩 반영.
STRATEGY_RULES.md 4.5 — 앞 70% 개발용 / 뒤 30% 검증용 분리.
"""
import os, sys, glob, argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 상수/신호 함수는 전략 모듈이 유일한 출처. 모듈 참조(S.X)로 쓰는 이유는
# 파라미터 그리드 탐색 시 상수를 한 곳에서만 바꾸기 위해서다.
import strategies.shooting_reversion as S  # noqa: E402
from strategies.shooting_reversion import (  # noqa: E402
    SPIKE_WINDOW_DAYS, BASE_LOOKBACK_DAYS, to_daily, precompute,
)
from backtest import data as dataio  # noqa: E402

WARMUP = SPIKE_WINDOW_DAYS + BASE_LOOKBACK_DAYS  # 지표가 유효해지는 최소 이력


# ---------------------------------------------------------------- 데이터
def load_all(timeframe="4h", min_days=400):
    syms, out = [], {}
    for path in sorted(glob.glob(os.path.join(dataio.CACHE_DIR, f"*_{timeframe}.csv"))):
        sym = os.path.basename(path).replace(f"_{timeframe}.csv", "").replace("_", "/").replace("-", ":")
        df = pd.read_csv(path)
        if len(df) < min_days * (24 // int(timeframe.replace("h", ""))):
            continue
        df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        d = to_daily(df)
        if len(d) < min_days:
            continue
        out[sym] = d
        syms.append(sym)
    return syms, out


# ---------------------------------------------------------------- 엔진
class Position:
    __slots__ = ("sym", "qty", "avg", "margin", "added", "entry_day", "entry_price", "budget")

    def __init__(self, sym, qty, avg, margin, entry_day, budget):
        self.sym, self.qty, self.avg, self.margin = sym, qty, avg, margin
        self.added = False
        self.entry_day, self.entry_price, self.budget = entry_day, avg, budget


def run(symbols, daily, pre, dates, start_i, end_i, seed=1000.0,
        random_entry=False, rng=None, btc_up=None, verbose=False):
    """
    하루 단위 포트폴리오 시뮬레이션.
    random_entry=True 면 진입 신호를 무작위로 대체 (STRATEGY_RULES.md 4.3 베이스라인).
    """
    cash = seed
    positions: dict[str, Position] = {}
    trades, equity_curve = [], []
    # 심볼별 날짜→인덱스 매핑
    idx = {s: {d: i for i, d in enumerate(daily[s]["dt"].values)} for s in symbols}

    for di in range(start_i, end_i):
        today = dates[di]

        # ---------------- 보유 포지션 처리 ----------------
        for sym in list(positions.keys()):
            p = positions[sym]
            i = idx[sym].get(today)
            if i is None:
                continue
            row = daily[sym].iloc[i]
            hi, lo, cl = row["high"], row["low"], row["close"]

            # 펀딩 (명목가 기준, 하루치)
            cash -= p.qty * cl * S.FUNDING_PER_DAY

            closed = None
            # 1) 추매 — 딱 한 번
            trig = S.add_trigger_price(p.entry_price)
            if not p.added and lo <= trig:
                add_margin = p.budget * (1 - S.FIRST_ENTRY_FRAC)
                if cash >= add_margin:
                    fill = trig * (1 + S.SLIPPAGE)
                    add_qty = add_margin * S.LEVERAGE / fill
                    cash -= add_margin + add_qty * fill * S.FEE_RATE
                    p.avg = (p.qty * p.avg + add_qty * fill) / (p.qty + add_qty)
                    p.qty += add_qty
                    p.margin += add_margin
                p.added = True   # 여력이 없어도 추매 기회는 소진

            # 2) 청산 (가장 불리한 것부터)
            # 손절은 거래소에 STOP_MARKET 으로 걸려 있으므로 청산보다 먼저 체결된다.
            # 시가가 이미 청산가 밑으로 갭하락한 경우에만 청산으로 처리한다.
            lq = S.liq_price(p.avg)
            stop = S.hard_stop_price(p.entry_price)
            op = row["open"]
            if op <= lq:
                closed, exit_px = "LIQUIDATION", min(op, lq)
            elif lo <= stop:
                closed, exit_px = "STOP", min(stop, op)   # 갭하락 시 시가 체결
            elif hi >= S.take_profit_price(p.avg):
                closed, exit_px = "TP", S.take_profit_price(p.avg)

            if closed:
                if closed == "LIQUIDATION":
                    pnl = -p.margin
                else:
                    fill = exit_px * (1 - S.SLIPPAGE)
                    pnl = p.qty * (fill - p.avg) - p.qty * fill * S.FEE_RATE
                    pnl = max(pnl, -p.margin)
                    cash += p.margin + pnl
                trades.append({
                    "symbol": sym, "entry_day": p.entry_day, "exit_day": today,
                    "hold_days": int((today - p.entry_day) / np.timedelta64(1, "D")),
                    "avg": p.avg, "exit": exit_px, "reason": closed,
                    "margin": p.margin, "pnl": pnl, "roi": pnl / p.margin, "added": p.added,
                })
                del positions[sym]

        # ---------------- 신규 진입 ----------------
        if len(positions) < S.MAX_CONCURRENT:
            equity_now = cash + sum(
                pp.margin + pp.qty * (daily[s].iloc[idx[s][today]]["close"] - pp.avg)
                for s, pp in positions.items() if today in idx[s]
            )
            budget = max(equity_now, 0) * S.TICKER_MARGIN_PCT
            first_margin = budget * S.FIRST_ENTRY_FRAC
            mkt_up = bool(btc_up.get(today, False)) if btc_up else True
            cands = []
            for sym in symbols:
                if sym in positions:
                    continue
                i = idx[sym].get(today)
                if i is None or i < WARMUP or i + 1 >= len(daily[sym]):
                    continue
                P = pre[sym]
                if not S.scan_ok(P, i):
                    continue
                cl = daily[sym].iloc[i]["close"]
                sig = (rng.random() < 0.004 and mkt_up) if random_entry else S.entry_signal(P, cl, i, mkt_up)
                if sig:
                    cands.append((P["spike_cnt"][i], sym, i))
            cands.sort(reverse=True)
            for _, sym, i in cands:
                if len(positions) >= S.MAX_CONCURRENT or cash < budget:
                    break
                nxt = daily[sym].iloc[i + 1]
                fill = nxt["open"] * (1 + S.SLIPPAGE)
                if fill <= 0:
                    continue
                qty = first_margin * S.LEVERAGE / fill
                cash -= first_margin + qty * fill * S.FEE_RATE
                positions[sym] = Position(sym, qty, fill, first_margin, np.datetime64(nxt["dt"].to_datetime64()), budget)

        # ---------------- 자산 평가 ----------------
        eq = cash
        for s, pp in positions.items():
            i = idx[s].get(today)
            if i is None:
                eq += pp.margin
                continue
            cl = daily[s].iloc[i]["close"]
            eq += max(pp.margin + pp.qty * (cl - pp.avg), 0)
        equity_curve.append((today, eq))

    return pd.DataFrame(trades), pd.DataFrame(equity_curve, columns=["dt", "equity"])


# ---------------------------------------------------------------- 리포트
def summarize(trades, eq, seed, label=""):
    if len(eq) == 0:
        return {}
    final = eq["equity"].iloc[-1]
    peak = eq["equity"].cummax()
    mdd = float(((eq["equity"] - peak) / peak).min()) if len(eq) else 0.0
    wins = trades[trades["pnl"] > 0] if len(trades) else trades
    loss = trades[trades["pnl"] <= 0] if len(trades) else trades
    gp = float(wins["pnl"].sum()) if len(wins) else 0.0
    gl = float(-loss["pnl"].sum()) if len(loss) else 0.0
    return {
        "label": label,
        "trades": len(trades),
        "return_pct": (final / seed - 1) * 100,
        "final": final,
        "mdd_pct": mdd * 100,
        "win_rate": (len(wins) / len(trades) * 100) if len(trades) else 0.0,
        "profit_factor": (gp / gl) if gl > 0 else float("inf"),
        "avg_hold": float(trades["hold_days"].mean()) if len(trades) else 0.0,
        "symbols_profitable": int((trades.groupby("symbol")["pnl"].sum() > 0).sum()) if len(trades) else 0,
        "symbols_traded": int(trades["symbol"].nunique()) if len(trades) else 0,
    }


def print_report(s):
    if not s:
        print("  (결과 없음)")
        return
    print(f"  트레이드      : {s['trades']}")
    print(f"  총수익        : {s['return_pct']:+.2f}%   (최종 {s['final']:,.1f} USDT)")
    print(f"  MDD           : {s['mdd_pct']:.2f}%")
    print(f"  승률          : {s['win_rate']:.1f}%")
    print(f"  Profit Factor : {s['profit_factor']:.2f}")
    print(f"  평균 보유     : {s['avg_hold']:.0f}일")
    print(f"  수익 심볼     : {s['symbols_profitable']}/{s['symbols_traded']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-usdt", type=float, default=1000.0)
    ap.add_argument("--split", type=float, default=0.7, help="앞 N 비율 = 개발용")
    ap.add_argument("--part", choices=["in", "out", "all"], default="in")
    ap.add_argument("--baseline", action="store_true", help="random entry 베이스라인도 실행")
    ap.add_argument("--dump", default="", help="트레이드 CSV 저장 경로")
    args = ap.parse_args()

    print("데이터 로딩...")
    symbols, daily = load_all()
    print(f"  심볼 {len(symbols)}종")
    pre = {s: precompute(daily[s]) for s in symbols}

    all_dates = sorted(set(np.concatenate([daily[s]["dt"].values for s in symbols])))
    dates = np.array(all_dates)
    n = len(dates)
    cut = int(n * args.split)
    ranges = {"in": (0, cut), "out": (cut, n), "all": (0, n)}
    a, b = ranges[args.part]
    print(f"  기간 {pd.Timestamp(dates[a]).date()} ~ {pd.Timestamp(dates[b-1]).date()}  ({b-a}일)\n")

    btc_up = S.btc_regime(daily["BTC/USDT:USDT"])
    rng = np.random.default_rng(42)
    tr, eq = run(symbols, daily, pre, dates, a, b, seed=args.seed_usdt, rng=rng, btc_up=btc_up)
    print(f"=== shooting_reversion [{args.part}] ===")
    s = summarize(tr, eq, args.seed_usdt, "strategy")
    print_report(s)
    if args.dump and len(tr):
        tr.to_csv(args.dump, index=False)
        print(f"  → {args.dump}")

    if len(tr):
        print("\n  청산 사유별:")
        for r, g in tr.groupby("reason"):
            print(f"    {r:12s} {len(g):4d}건  합계 {g['pnl'].sum():+9.1f}  평균ROI {g['roi'].mean()*100:+.1f}%")

    if args.baseline:
        rng2 = np.random.default_rng(7)
        tr_b, eq_b = run(symbols, daily, pre, dates, a, b, seed=args.seed_usdt,
                         random_entry=True, rng=rng2, btc_up=btc_up)
        print(f"\n=== random entry 베이스라인 [{args.part}] ===")
        print_report(summarize(tr_b, eq_b, args.seed_usdt, "baseline"))
