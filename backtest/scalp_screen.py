"""
스캘핑 신호 일괄 탐색.

전략을 하나씩 찍어보는 대신, 후보 신호 여러 개를 한 번에 재서
'무작위 진입 대비 승률을 몇 %p 올리는가' 만 본다.

기준선: TP·SL 을 대칭(+1%/-1%)으로 둔다.
  비대칭이면 구조적 불리함(측정치 -5%p)이 섞여서 신호를 못 본다.
  대칭이면 무작위 진입 승률이 이론 50% / 실측 49% 라 신호만 남는다.

주의(STRATEGY_RULES): 신호 후보 12개 × 구간 10 = 120칸을 두드리는 것이다.
그중 몇 개는 우연히 좋아 보인다. 그래서 통과 조건을 셋 다 요구한다.
  ① 구간별로 단조로울 것        (우연이면 들쭉날쭉하다)
  ② 앞·뒤 절반에서 모두 나올 것  (한쪽에만 있으면 그 시기 특성이다)
  ③ BTC 상승·하락장 모두에서 나올 것
"""
import os, sys, glob, argparse
import numpy as np
import pandas as pd

M5 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scalp5m")
STEP_MS = 300_000


def btc_feats():
    d = pd.read_csv(os.path.join(M5, "spot_BTC_USDT_5m.csv"))
    d = d.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    c = d["close"]
    return pd.DataFrame({
        "timestamp": d["timestamp"],
        "b12": c / c.shift(12) - 1,
        "b288": c / c.shift(288) - 1,
        "bma": c / c.rolling(288 * 7).mean() - 1,
    }).set_index("timestamp")


def build(tp=0.01, sl=0.01, hold=48, step=12, min_qv5=20_000):
    B = btc_feats()
    rows = []
    for p in sorted(glob.glob(os.path.join(M5, "spot_*_5m.csv"))):
        sym = os.path.basename(p)[len("spot_"):-len("_5m.csv")]
        if sym in ("BTC_USDT", "ETH_USDT"):
            continue
        d = pd.read_csv(p).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        n = len(d)
        if n < 300 + hold:
            continue
        ts = d["timestamp"].values.astype("int64")
        o, h, l, c, v = (d[k].values.astype(float) for k in ("open", "high", "low", "close", "volume"))
        qv = v * c
        s = pd.Series(c)
        ret = {k: (s / s.shift(k) - 1).values for k in (6, 12, 24, 72, 288)}
        vmean = pd.Series(qv).rolling(24).mean().values
        tr = pd.Series(np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1)))))
        atr = (tr.rolling(24).mean().values) / c
        mx = pd.Series(h).rolling(288).max().values
        mn = pd.Series(l).rolling(288).min().values
        with np.errstate(invalid="ignore", divide="ignore"):
            pos = (c - mn) / (mx - mn)
        ma288 = pd.Series(c).rolling(288).mean().values
        body = np.where(h > l, (c - o) / (h - l), 0.0)
        upd = np.sign(np.diff(c, prepend=c[0]))
        consec = np.zeros(n)
        run = 0
        for k in range(1, n):
            run = run + upd[k] if np.sign(run) == upd[k] or run == 0 else upd[k]
            consec[k] = run
        hour = pd.to_datetime(ts, unit="ms").hour.values

        for i in range(300, n - hold - 1, step):
            if ts[i] - ts[i - 288] != 288 * STEP_MS:        # 봉이 끊긴 구간 제외
                continue
            if vmean[i] < min_qv5:
                continue
            px = o[i + 1]
            if not np.isfinite(px) or px <= 0:
                continue
            tpx, spx = px * (1 + tp), px * (1 - sl)
            r = None
            for j in range(i + 2, min(n - 1, i + 1 + hold) + 1):
                if l[j] <= spx:
                    r = 0; break
                if h[j] >= tpx:
                    r = 1; break
            if r is None:
                continue        # 시간 안에 결판이 안 났으면 그 관측은 버린다.
                                # 종가로 승패를 매기면 '안 움직이는 종목'이 이긴 것처럼 보인다
                                # — 실제로 그 착시 때문에 저변동성이 +4.3%p 로 잘못 나왔었다
            b = B.loc[ts[i]] if ts[i] in B.index else None
            rows.append((
                ts[i], sym, r,
                ret[6][i], ret[12][i], ret[24][i], ret[72][i], ret[288][i],
                qv[i] / vmean[i] if vmean[i] > 0 else np.nan,
                atr[i], pos[i], c[i] / ma288[i] - 1 if ma288[i] > 0 else np.nan,
                body[i], consec[i], hour[i],
                (b["b12"] if b is not None else np.nan),
                (b["b288"] if b is not None else np.nan),
                (b["bma"] if b is not None else np.nan),
            ))
    cols = ["ts", "sym", "win", "ret30m", "ret1h", "ret2h", "ret6h", "ret24h", "volratio",
            "atr", "daypos", "vs_ma", "body", "consec", "hour", "btc1h", "btc24h", "btc_ma"]
    df = pd.DataFrame(rows, columns=cols)
    df["rel1h"] = df["ret1h"] - df["btc1h"]        # BTC 대비 초과 (시차)
    df["rel24h"] = df["ret24h"] - df["btc24h"]
    return df


FEATS = ["ret30m", "ret1h", "ret2h", "ret6h", "ret24h", "volratio", "atr",
         "daypos", "vs_ma", "body", "consec", "rel1h", "rel24h", "hour"]


def report(df, nq=8):
    base = df["win"].mean()
    print(f"기준선(무작위 진입) 승률 {base*100:.2f}%   관측 {len(df):,}건  심볼 {df.sym.nunique()}종")
    half = df.ts.median()
    up = df.btc_ma > 0
    print(f"\n{'신호':<10}{'구간':<18}{'n':>8}{'승률':>8}{'기준대비':>9}{'앞절반':>9}{'뒤절반':>9}{'BTC↑':>8}{'BTC↓':>8}")
    print("-" * 90)
    hits = []
    for f in FEATS:
        x = df[f]
        if f == "hour":
            groups = [(f"{hh:02d}시", df.hour == hh) for hh in range(0, 24, 3)]
            groups = [(f"{hh:02d}~{hh+2}시", (df.hour >= hh) & (df.hour < hh + 3)) for hh in range(0, 24, 3)]
        else:
            try:
                q = pd.qcut(x, nq, labels=False, duplicates="drop")
            except Exception:
                continue
            groups = [(f"{int(k)+1}/{int(q.max())+1}분위", q == k) for k in range(int(q.max()) + 1)]
        for name, m in groups:
            g = df[m]
            if len(g) < 2000:
                continue
            w = g["win"].mean()
            lift = (w - base) * 100
            if abs(lift) < 1.0:
                continue
            a = g[g.ts <= half]["win"].mean() * 100
            b = g[g.ts > half]["win"].mean() * 100
            u = g[up[m.values] if len(up) == len(m) else up.reindex(g.index)]["win"].mean() * 100
            dn = g[~(up.reindex(g.index).fillna(False))]["win"].mean() * 100
            print(f"{f:<10}{name:<18}{len(g):>8,}{w*100:>7.1f}%{lift:>+8.1f}%p{a:>8.1f}%{b:>8.1f}%{u:>7.1f}%{dn:>7.1f}%")
            hits.append((lift, f, name, len(g), a, b, u, dn, base * 100))
    hits.sort(key=lambda r: -r[0])
    print("\n=== 승률을 가장 많이 올린 신호 (통과 조건 3개 전부 확인) ===")
    for lift, f, name, n, a, b, u, dn, bs in hits[:8]:
        ok = (a > bs and b > bs and u > bs and dn > bs)
        print(f"  {'통과' if ok else '탈락'}  {f} {name}  {lift:+.1f}%p  "
              f"(앞 {a:.1f} / 뒤 {b:.1f} / BTC↑ {u:.1f} / BTC↓ {dn:.1f}  기준 {bs:.1f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tp", type=float, default=0.01)
    ap.add_argument("--sl", type=float, default=0.01)
    ap.add_argument("--hold", type=int, default=48)
    ap.add_argument("--step", type=int, default=12)
    a = ap.parse_args()
    df = build(a.tp, a.sl, a.hold, a.step)
    out = "/tmp/scalp_screen.csv"
    df.to_csv(out, index=False)
    report(df)
    print(f"\n원자료: {out}")
