"""
COIL — "급등 직전에 들어가서, 손절은 코앞에, 오르면 트레일링으로 따라간다".

가설
    변동성이 극도로 줄어든 뒤(에너지 응축) 처음으로 위로 뚫는 자리가 급등의 시작이다.
    그 자리에서 사면 손절선을 진입가 바로 아래에 둘 수 있다 — 틀렸다는 게 즉시 드러나므로.
    맞으면 트레일링이 끝까지 따라간다.

    승률은 낮게 나올 수밖에 없다(20~35%). 대신 지는 건 -3%, 이기는 건 +50% 여야 한다.
    그래서 승률이 아니라 **손익비 × 승률** 로만 판단한다.

규칙 준수
    3.3 진입한 봉에서 청산하지 않는다 / 손절이 우선(동시 터치 시)
    4.1 수수료 편도 0.05%, 슬리피지 0.03%
    4.3 같은 자리 무작위 진입 대조군
"""
import os, sys, glob, argparse, itertools
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import data as dataio  # noqa: E402

FEE = 0.0005
SLIP = 0.0003
WARMUP = 200
RET_CAP = 3.0

DEFAULT = dict(
    squeeze_win=90,      # ATR 비율이 최근 N일 중 최저권인가
    squeeze_pct=0.20,    # 하위 몇 % 안에 들어야 "응축"인가
    break_win=20,        # 최근 N일 고점을 뚫으면 트리거
    vol_mult=1.5,        # 뚫는 날 거래대금이 20일 평균의 몇 배
    stop_pct=0.04,       # 진입가 대비 손절 (코앞)
    trail_mult=3.0,      # 트레일링 = 진입 후 최고가 - ATR × N
    trail_start=0.05,    # 이만큼 오른 뒤부터 트레일링 시작
    max_hold=60,         # 이 일수 넘으면 정리
    ticker_pct=0.05,
    max_conc=10,
    min_qv=1_000_000,
)


def load_daily(min_days=400):
    out = {}
    for path in sorted(glob.glob(os.path.join(dataio.CACHE_DIR, "spot_*_1d.csv"))):
        df = pd.read_csv(path)
        if len(df) < min_days:
            continue
        df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        sym = os.path.basename(path).replace("spot_", "").replace("_1d.csv", "").replace("_", "/")
        out[sym] = df
    return out


def atr(df, n=14):
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def precompute(df, cfg):
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    qv = df["volume"].astype(float) * c
    a = atr(df)
    ratio = (a / c)
    # 응축: ATR비율이 최근 squeeze_win 중 하위 squeeze_pct 분위 이하
    rank = ratio.rolling(cfg["squeeze_win"]).rank(pct=True)
    # 돌파: 오늘 종가가 어제까지의 break_win 고점보다 위
    prior_high = h.shift(1).rolling(cfg["break_win"]).max()
    qv_ma = qv.rolling(20).mean()
    ef = c.ewm(span=20, adjust=False).mean()
    es = c.ewm(span=120, adjust=False).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    rlo, rhi = rsi.rolling(14).min(), rsi.rolling(14).max()
    k = (100 * (rsi - rlo) / (rhi - rlo).replace(0, np.nan)).rolling(3).mean()
    return dict(
        ef=ef.values, es=es.values, k=k.values,
        c=c.values, h=h.values, l=l.values, o=df["open"].astype(float).values,
        atr=a.values, rank=rank.values, ph=prior_high.values,
        qv=qv.values, qv_ma=qv_ma.values, dt=df["dt"].values,
    )


def sig_surfer(P, i, cfg):
    """SURFER 진입: 상승추세(EMA20>EMA120, 둘 다 상승) + StochRSI K 눌림."""
    if not (np.isfinite(P["ef"][i]) and np.isfinite(P["es"][i]) and np.isfinite(P["k"][i])):
        return False
    if not (P["ef"][i] > P["es"][i] and P["ef"][i] > P["ef"][i - 3] and P["es"][i] > P["es"][i - 3]):
        return False
    if P["k"][i] > 25:
        return False
    ma = P["qv_ma"][i]
    return bool(np.isfinite(ma) and ma >= cfg["min_qv"])


def sig_any(P, i, cfg):
    """진입 신호 없음 — 유동성만 본다. 청산 규칙만의 성적."""
    ma = P["qv_ma"][i]
    return bool(np.isfinite(ma) and ma >= cfg["min_qv"])


def signal(P, i, cfg):
    e = cfg.get("entry", "coil")
    if e == "surfer":
        return sig_surfer(P, i, cfg)
    if e == "any":
        return sig_any(P, i, cfg)
    if not np.isfinite(P["rank"][i]) or P["rank"][i] > cfg["squeeze_pct"]:
        return False
    if not np.isfinite(P["ph"][i]) or P["c"][i] <= P["ph"][i]:
        return False
    ma = P["qv_ma"][i]
    if not np.isfinite(ma) or ma < cfg["min_qv"]:
        return False
    return bool(P["qv"][i] >= ma * cfg["vol_mult"])


class Pos:
    __slots__ = ("sym", "qty", "entry", "cost", "peak", "stop", "i0", "trailing")


def simulate(pre, dates, cfg, seed=1000.0, random_entry=False, rng=None):
    cash = seed
    pos, trades, curve = {}, [], []
    idx = {s: {t: i for i, t in enumerate(P["dt"])} for s, P in pre.items()}
    syms = list(pre)

    for today in dates:
        # ── 보유분 처리 (진입한 봉은 건드리지 않는다) ──
        for sym in list(pos):
            p = pos[sym]
            P = pre[sym]
            i = idx[sym].get(today)
            if i is None or i <= p.i0:
                continue
            lo, hi, cl = P["l"][i], P["h"][i], P["c"][i]
            exit_px = None
            reason = ""
            # 손절 우선
            if lo <= p.stop:
                exit_px, reason = p.stop, "STOP" if not p.trailing else "TRAIL"
            else:
                p.peak = max(p.peak, hi)
                if not p.trailing and p.peak >= p.entry * (1 + cfg["trail_start"]):
                    p.trailing = True
                if p.trailing:
                    newstop = p.peak - P["atr"][i] * cfg["trail_mult"]
                    p.stop = max(p.stop, newstop)
                if i - p.i0 >= cfg["max_hold"]:
                    exit_px, reason = cl, "TIME"
            if exit_px is not None:
                fill = exit_px * (1 - SLIP)
                proceeds = p.qty * fill * (1 - FEE)
                cash += proceeds
                trades.append(dict(sym=sym, ret=min(proceeds / p.cost - 1, RET_CAP), reason=reason,
                                   days=i - p.i0, dt=today))
                del pos[sym]

        # ── 신규 진입 ──
        if len(pos) < cfg["max_conc"]:
            eq = cash + sum(p.qty * pre[s]["c"][idx[s].get(today, p.i0)] for s, p in pos.items())
            cands = []
            for sym in syms:
                if sym in pos:
                    continue
                i = idx[sym].get(today)
                if i is None or i < WARMUP or i + 1 >= len(pre[sym]["c"]):
                    continue
                if signal(pre[sym], i, cfg):
                    cands.append((sym, i))
            if random_entry:
                # 대조군: 같은 날 실제 신호가 난 개수만큼 아무 종목이나
                n = len(cands)
                cands = []
                pool = [(s, idx[s][today]) for s in syms
                        if s not in pos and today in idx[s]
                        and idx[s][today] >= WARMUP and idx[s][today] + 1 < len(pre[s]["c"])
                        and np.isfinite(pre[s]["qv_ma"][idx[s][today]])
                        and pre[s]["qv_ma"][idx[s][today]] >= cfg["min_qv"]]
                if pool and n:
                    pick = rng.choice(len(pool), size=min(n, len(pool)), replace=False)
                    cands = [pool[k] for k in pick]
            for sym, i in cands:
                if len(pos) >= cfg["max_conc"]:
                    break
                budget = eq * cfg["ticker_pct"]
                if budget > cash or budget < 5:
                    break
                P = pre[sym]
                px = P["o"][i + 1] * (1 + SLIP)   # 다음 봉 시가에 산다
                if not np.isfinite(px) or px <= 0:
                    continue
                qty = budget * (1 - FEE) / px
                cash -= budget
                p = Pos()
                p.sym, p.qty, p.entry, p.cost = sym, qty, px, budget
                p.peak, p.stop = px, px * (1 - cfg["stop_pct"])
                p.i0, p.trailing = i + 1, False
                pos[sym] = p
        eq = cash + sum(p.qty * pre[s]["c"][idx[s].get(today, p.i0)] for s, p in pos.items())
        curve.append(eq)

    # 남은 포지션 시가평가 청산
    for sym, p in pos.items():
        P = pre[sym]
        cash += p.qty * P["c"][-1] * (1 - SLIP) * (1 - FEE)
        trades.append(dict(sym=sym, ret=P["c"][-1] / p.entry - 1, reason="OPEN_END",
                           days=0, dt=dates[-1]))
    return trades, np.array(curve), seed


def stats(trades, curve, seed):
    if not trades:
        return dict(n=0, ret=0.0, pf=0.0, win=0.0, mdd=0.0, avg=0.0, best=0.0)
    r = np.array([t["ret"] for t in trades])
    gp, gl = r[r > 0].sum(), -r[r < 0].sum()
    peak = np.maximum.accumulate(curve) if len(curve) else np.array([seed])
    mdd = float((curve / peak - 1).min()) if len(curve) else 0.0
    return dict(n=len(r), ret=float(curve[-1] / seed - 1) if len(curve) else 0.0,
                pf=float(gp / gl) if gl > 0 else float("inf"),
                win=float((r > 0).mean()), mdd=mdd, avg=float(r.mean()),
                best=float(r.max()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--n-control", type=int, default=20)
    ap.add_argument("--entry", default="coil", choices=["coil", "surfer", "any"])
    ap.add_argument("--cap", type=float, default=3.0, help="거래당 수익 상한 (데이터 이상치 차단)")
    for k, v in DEFAULT.items():
        ap.add_argument(f"--{k.replace('_','-')}", type=type(v), default=None)
    args = ap.parse_args()

    cfg = dict(DEFAULT, entry="coil")
    for k in DEFAULT:
        v = getattr(args, k)
        if v is not None:
            cfg[k] = v
    cfg["entry"] = args.entry
    globals()["RET_CAP"] = args.cap

    daily = load_daily()
    print(f"심볼 {len(daily)}종")
    dates = np.array(sorted(set(np.concatenate([d["dt"].values for d in daily.values()]))))

    def run(c, random_entry=False, seed_rng=0):
        pre = {s: precompute(d, c) for s, d in daily.items()}
        return stats(*simulate(pre, dates, c, random_entry=random_entry,
                               rng=np.random.default_rng(seed_rng)))

    if args.grid:
        print(f"{'응축':>5}{'분위':>6}{'돌파':>5}{'거래량':>7}{'손절':>6}{'트레일':>7}"
              f"{'거래':>6}{'수익':>9}{'PF':>7}{'승률':>7}{'MDD':>8}{'평균':>8}")
        rows = []
        for sq, pc, bw, vm, sp, tm in itertools.product(
                [60, 90], [0.15, 0.25], [20, 40], [1.5, 2.5],
                [0.03, 0.05, 0.08], [2.0, 3.5]):
            c = dict(cfg, squeeze_win=sq, squeeze_pct=pc, break_win=bw,
                     vol_mult=vm, stop_pct=sp, trail_mult=tm)
            st = run(c)
            rows.append((st["ret"], sq, pc, bw, vm, sp, tm, st))
            print(f"{sq:>5}{pc:>6.2f}{bw:>5}{vm:>7.1f}{sp:>6.2f}{tm:>7.1f}"
                  f"{st['n']:>6}{st['ret']:>8.1%}{st['pf']:>7.2f}{st['win']:>7.1%}"
                  f"{st['mdd']:>8.1%}{st['avg']:>8.2%}", flush=True)
        rows.sort(reverse=True, key=lambda x: x[0])
        print("\n=== 상위 5 ===")
        for ret, sq, pc, bw, vm, sp, tm, st in rows[:5]:
            print(f"  응축{sq}/{pc} 돌파{bw} 거래량x{vm} 손절{sp:.0%} 트레일{tm} "
                  f"→ {ret:.1%} PF{st['pf']:.2f} 승률{st['win']:.0%} MDD{st['mdd']:.1%}")
        print(f"\n플러스: {sum(1 for r in rows if r[0] > 0)} / {len(rows)}")
        return

    st0 = st = run(cfg)
    print(f"\n[COIL] {cfg}")
    print(f"거래 {st['n']} | 수익 {st['ret']:.1%} | PF {st['pf']:.2f} | "
          f"승률 {st['win']:.1%} | MDD {st['mdd']:.1%} | 평균 {st['avg']:.2%} | 최고 {st['best']:.1%}")
    # 연도별 쪼개기 — 옛날 알트장 덕인지 본다
    pre = {s_: precompute(d, cfg) for s_, d in daily.items()}
    tr, cv, sd = simulate(pre, dates, cfg)
    tdf = pd.DataFrame(tr)
    tdf["year"] = pd.to_datetime(tdf["dt"], utc=True).dt.year
    print("\n  연도   거래   합산수익   승률   평균")
    for y, g in tdf.groupby("year"):
        r = g["ret"].values
        print(f"  {y}  {len(r):>5}  {r.sum():>8.1%}  {(r>0).mean():>6.0%}  {r.mean():>7.2%}")

    if args.baseline:
        # 복리 수익은 꼬리 하나에 좌우된다. 거래당 평균/PF 로 비교한다.
        print("\n=== 같은 날 같은 개수, 아무 종목이나 (같은 청산 규칙) ===")
        print(f"{'회차':<8}{'거래':>7}{'거래당평균':>11}{'PF':>7}{'승률':>7}{'복리수익':>11}")
        outs = []
        for k in range(args.n_control):
            st = run(cfg, random_entry=True, seed_rng=k)
            outs.append(st)
            print(f"#{k+1:<7}{st['n']:>7}{st['avg']*100:>10.2f}%{st['pf']:>7.2f}"
                  f"{st['win']*100:>6.0f}%{st['ret']*100:>10.1f}%", flush=True)
        A = np.array([o["avg"] for o in outs]); P = np.array([o["pf"] for o in outs])
        print(f"{'대조군중앙값':<8}{'':>7}{np.median(A)*100:>10.2f}%{np.median(P):>7.2f}")
        print(f"{'COIL':<8}{st0['n']:>7}{st0['avg']*100:>10.2f}%{st0['pf']:>7.2f}"
              f"{st0['win']*100:>6.0f}%{st0['ret']*100:>10.1f}%")
        pct = (A < st0["avg"]).mean()
        print(f"\nCOIL 거래당평균이 대조군 {pct:.0%} 보다 위 "
              f"({'유의미' if pct >= 0.95 else '구분 안 됨'})")


if __name__ == "__main__":
    main()
