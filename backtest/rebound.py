"""
REBOUND 검증 — 1분봉 선물, 거래량 급등 + 급락 → 지정가로 받기.

여기서만 확인하는 것
  1. 지정가가 실제로 닿아야 산다 (체결률)
  2. 지연(latency) 을 넣으면 얼마나 깎이는가
  3. 기간을 쪼개도 살아있는가
  4. 같은 시각 무작위 진입 대조군보다 나은가

STRATEGY_RULES 3.1 상수는 strategies/rebound.py 가 유일한 출처.
3.3 신호봉에서 사고팔지 않는다 / 손절 우선.
"""
import os, sys, glob, argparse, itertools
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.rebound as S  # noqa: E402
from backtest import data as dataio  # noqa: E402


def load_1m(min_bars=20000, market="futures"):
    out = {}
    pat = "*_1m.csv"
    for path in sorted(glob.glob(os.path.join(dataio.CACHE_DIR, pat))):
        base = os.path.basename(path)
        if base.startswith("spot_"):
            continue
        df = pd.read_csv(path)
        if len(df) < min_bars:
            continue
        sym = base.replace("_1m.csv", "").replace("_", "/").replace("-", ":")
        c = df["close"].astype(float)
        qv = df["volume"].astype(float) * c
        out[sym] = dict(
            o=df["open"].astype(float).values, h=df["high"].astype(float).values,
            l=df["low"].astype(float).values, c=c.values,
            qv=qv.values, qv_ma=qv.rolling(S.VOL_MA_BARS).mean().values,
            ts=df["timestamp"].values,
        )
        for k in (3, 5, 10, 15, 20, 30):
            out[sym][f"d{k}"] = (c / c.shift(k) - 1).fillna(0).values
    return out


def simulate(data, drop_bars, drop_pct, vol_mult, tp, sl, hold,
             wait=3, offset=0.0, latency=0, min_qv=None, random_at=None, rng=None,
             post_only=True):
    """latency: 신호봉 종가 이후 이 봉 수만큼 지나서야 주문이 걸린다."""
    min_qv = S.MIN_QV_MA if min_qv is None else min_qv
    rows = []
    for sym, I in data.items():
        n, dk = len(I["c"]), I[f"d{drop_bars}"]
        i = 100
        while i < n - hold - wait - latency - 3:
            ma = I["qv_ma"][i]
            if not np.isfinite(ma) or ma < min_qv:
                i += 1; continue
            hit = (I["qv"][i] >= ma * vol_mult) and (dk[i] <= drop_pct)
            if random_at is not None:
                hit = random_at(sym, i)
            if not hit:
                i += 1; continue

            limit = I["c"][i] * (1 - offset)
            start = i + 1 + latency
            # 실전에서는 postOnly 로 건다. 주문을 낼 때 이미 값이 지정가 아래면
            # 거래소가 거부한다(-5022). 데모에서 신호의 31%가 여기서 잘려나갔다.
            # 그게 곧 **가장 빠르게 무너지는 급락**이라, 이걸 빼먹으면
            # 손절을 맞았어야 할 거래가 표본에 들어온다.
            if post_only and I["o"][start] <= limit:
                rows.append(dict(sym=sym, ts=I["ts"][i], filled=0, ret=0.0, why="POSTONLY"))
                i += wait + 1
                continue
            fill_j = None
            for j in range(start, min(n - 1, start + wait)):
                if I["l"][j] <= limit:
                    fill_j = j; break
            if fill_j is None:
                rows.append(dict(sym=sym, ts=I["ts"][i], filled=0, ret=0.0, why="NOFILL"))
                i += wait + 1; continue

            px = limit
            tpx, spx = px * (1 + tp), px * (1 - sl)
            last = min(n - 1, fill_j + hold)
            r, why = None, ""
            # 체결된 그 봉에서 손절선을 이미 뚫었으면 그 자리에서 끝난 것이다.
            # 급락 중에 지정가로 받는 전략이라 이 일이 실제로 자주 난다
            # (데모에서 -2021 "Order would immediately trigger" 가 13건 났다).
            # 이걸 빼먹으면 손절을 맞았어야 할 거래가 살아남아 회복할 시간을 얻고,
            # 익절을 멀리 둘수록 그 이득이 커져서 격자가 끝까지 단조로워진다.
            if I["l"][fill_j] <= spx:
                rows.append(dict(sym=sym, ts=I["ts"][fill_j], filled=1,
                                 ret=(spx / px - 1) - S.FEE_MAKER - S.FEE_TAKER - S.SLIPPAGE,
                                 why="SL"))
                i = fill_j + 1
                continue
            for j in range(fill_j + 1, last + 1):        # 익절은 체결 봉에서 안 본다
                if I["l"][j] <= spx:                     # 손절 우선
                    r, why = (spx / px - 1) - S.FEE_MAKER - S.FEE_TAKER - S.SLIPPAGE, "SL"; break
                if I["h"][j] >= tpx:
                    r, why = (tpx / px - 1) - S.FEE_MAKER - S.FEE_MAKER, "TP"; break
            if r is None:
                r, why = (I["c"][last] / px - 1) - S.FEE_MAKER - S.FEE_TAKER - S.SLIPPAGE, "TIME"
            rows.append(dict(sym=sym, ts=I["ts"][fill_j], filled=1, ret=r, why=why))
            i = last + 1
    return pd.DataFrame(rows)


def stats(df):
    if df is None or df.empty:
        return None
    f = df[df.filled == 1]
    if len(f) < 30:
        return None
    r = f["ret"].values
    gp, gl = r[r > 0].sum(), -r[r < 0].sum()
    res = f["why"] != "TIME"
    return dict(sig=len(df), n=len(f), fill=f.filled.sum() / len(df),
                mean=r.mean(), total=r.sum(), pf=gp / gl if gl > 0 else float("inf"),
                win=(r > 0).mean(), nsym=f["sym"].nunique(),
                tp_share=(f.loc[res, "why"] == "TP").mean() if res.any() else np.nan,
                resolved=res.mean())


def line(tag, s):
    if s is None:
        print(f"{tag:<34} 관측 부족"); return
    print(f"{tag:<34}{s['n']:>6}{s['fill']*100:>7.0f}%{s['mean']*100:>+9.3f}%"
          f"{s['total']*100:>+9.1f}%{s['pf']:>7.2f}{s['win']*100:>7.0f}%"
          f"{s['tp_share']*100:>8.0f}%{s['nsym']:>6}", flush=True)


def header():
    print(f"{'설정':<34}{'거래':>6}{'체결':>7}{'평균':>9}{'누적':>9}{'PF':>7}{'승률':>7}{'익절도달':>8}{'종목':>6}")
    print("-" * 92)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="base",
                    choices=["base", "grid", "latency", "period", "control", "oos", "tune"])
    ap.add_argument("--min-bars", type=int, default=20000)
    args = ap.parse_args()

    data = load_1m(args.min_bars)
    span = [(pd.Timestamp(v["ts"][0], unit="ms"), pd.Timestamp(v["ts"][-1], unit="ms"))
            for v in data.values()]
    print(f"1분봉 {len(data)}종 | {min(s[0] for s in span).date()} ~ {max(s[1] for s in span).date()}\n")

    D = dict(drop_bars=S.DROP_BARS, drop_pct=S.DROP_PCT, vol_mult=S.VOL_MULT,
             tp=S.TAKE_PROFIT, sl=S.STOP_LOSS, hold=S.MAX_HOLD_BARS,
             wait=S.ENTRY_WAIT_BARS, offset=S.ENTRY_LIMIT_OFFSET)

    if args.mode == "base":
        header()
        line("기준 v0.1", stats(simulate(data, **D)))

    elif args.mode == "grid":
        header()
        best = []
        for db, dp, vm in itertools.product([3, 5, 10, 15], [-0.015, -0.02, -0.03, -0.04],
                                            [5.0, 8.0, 12.0]):
            s = stats(simulate(data, **{**D, "drop_bars": db, "drop_pct": dp, "vol_mult": vm}))
            line(f"{db}분 {dp:+.1%} 거래량x{vm:.0f}", s)
            if s:
                best.append((s["pf"], s["mean"], db, dp, vm, s))
        best.sort(reverse=True)
        print("\n=== PF 상위 5 ===")
        for pf, mn, db, dp, vm, s in best[:5]:
            print(f"  {db}분 {dp:+.1%} x{vm:.0f} → PF {pf:.2f} 평균 {mn:+.3%} 거래 {s['n']}")
        print(f"\nPF>1.1: {sum(1 for b in best if b[0] > 1.1)} / {len(best)}")

    elif args.mode == "latency":
        header()
        for lat in [0, 1, 2, 3]:
            line(f"지연 {lat}분", stats(simulate(data, **{**D, "latency": lat})))
        print("\n※ 지연 1분 = 신호를 보고 1분 뒤에야 주문이 걸린다는 뜻")
        header()
        for off in [0.0, 0.001, 0.003]:
            line(f"지정가 -{off:.1%}", stats(simulate(data, **{**D, "offset": off})))

    elif args.mode == "period":
        df = simulate(data, **D)
        f = df[df.filled == 1].copy()
        f["m"] = pd.to_datetime(f["ts"], unit="ms").dt.to_period("M")
        print(f"{'월':<10}{'거래':>6}{'평균':>10}{'누적':>10}{'PF':>7}{'승률':>7}")
        print("-" * 52)
        for m, g in f.groupby("m"):
            r = g["ret"].values
            gp, gl = r[r > 0].sum(), -r[r < 0].sum()
            print(f"{str(m):<10}{len(r):>6}{r.mean()*100:>+9.3f}%{r.sum()*100:>+9.1f}%"
                  f"{(gp/gl if gl>0 else 99):>7.2f}{(r>0).mean()*100:>6.0f}%")
        print("\n종목별 상위/하위 5")
        by = f.groupby("sym")["ret"].agg(["count", "sum", "mean"]).sort_values("sum")
        print(by.tail(5).to_string()); print(by.head(5).to_string())

    elif args.mode == "tune":
        # PF 가 아니라 **연 환산 수익**으로 줄 세운다.
        # PF 2.34 짜리가 거래가 8배 드물면 총수익은 1/4 이다. 이걸로 한 번 속았다.
        SYMS, YEARS, LEV, LIVE_SYMS = len(data), 1.8, S.LEVERAGE, 120
        print(f"{'설정':<38}{'거래':>6}{'평균':>9}{'PF':>6}{'익절%':>7}{'결판%':>7}{'연환산':>13}")
        print("-" * 88)
        rows = []
        cand = []
        # 체결봉 손절을 반영하고 나서 처음부터 다시 찾는다.
        # 손절 -0.5%는 지정가로 받은 그 1분 안에 뚫리는 일이 많다. 넓히는 쪽부터 본다.
        import itertools as _it
        for sl, tp in _it.product([0.005, 0.010, 0.015, 0.020, 0.030],
                                  [0.010, 0.020, 0.030]):
            cand.append(dict(sl=sl, tp=tp))
        seen = set()
        for c in [{}] + cand:
            key = tuple(sorted(c.items()))
            if key in seen:
                continue
            seen.add(key)
            st = stats(simulate(data, **{**D, **c}))
            if not st:
                print(f"{str(c) or '현행':<32} 관측 부족"); continue
            per_sym_year = st["n"] / YEARS / SYMS
            yr = per_sym_year * LIVE_SYMS * st["mean"] * LEV
            nm = "현행" if not c else ", ".join(f"{k}={v}" for k, v in c.items())
            print(f"{nm:<38}{st['n']:>6}{st['mean']*100:>+8.3f}%{st['pf']:>6.2f}"
                  f"{st['tp_share']*100:>6.0f}%{st['resolved']*100:>7.0f}%{yr*100:>13.0f}%", flush=True)
            rows.append((yr, nm, st))
        rows.sort(reverse=True)
        print("\n=== 연환산 상위 5 ===")
        for yr, nm, st in rows[:5]:
            print(f"  {nm:<32} 연 {yr*100:>6.0f}%  (거래 {st['n']}, PF {st['pf']:.2f})")

    elif args.mode == "oos":
        # 파라미터는 2026-04~08 자료로 골랐다. 그 앞 구간은 손대지 않은 자료다.
        CUT = pd.Timestamp("2026-04-01", tz="UTC").value // 10**6
        df = simulate(data, **D)
        f = df[df.filled == 1].copy()
        header()
        for tag, g in [("파라미터 고를 때 안 쓴 구간 (~2026-03)", f[f.ts < CUT]),
                       ("파라미터 고른 구간 (2026-04~)", f[f.ts >= CUT])]:
            line(tag, stats(g))
        print("\n※ 앞 구간이 뒤 구간과 비슷하면 과최적화가 아니다.")

    elif args.mode == "control":
        header()
        base = stats(simulate(data, **D))
        line("실제 신호", base)
        rng = np.random.default_rng(0)
        # 같은 종목·같은 개수만큼 무작위 시각에 진입
        cnt = {}
        df = simulate(data, **D)
        for s_, g in df[df.filled == 1].groupby("sym"):
            cnt[s_] = len(g)
        for k in range(3):
            picks = {}
            for s_, c in cnt.items():
                n = len(data[s_]["c"])
                picks[s_] = set(rng.integers(200, n - 200, size=c * 3).tolist())
            line(f"무작위 대조군 #{k+1}",
                 stats(simulate(data, **D, random_at=lambda s_, i: i in picks.get(s_, ()))))


if __name__ == "__main__":
    main()
