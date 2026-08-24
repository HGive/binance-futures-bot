"""
BTC 시차 스캘핑 — BTC 는 올랐는데 아직 안 따라온 알트를 롱.

사람은 500종을 동시에 못 본다. 프로그램은 본다. 이게 프로그램 매매의 고유 이점이다.

신호(5분봉 t 마감 시점):
  최근 K봉 동안  BTC 상승률 >= BTC_UP
  같은 구간      알트 상승률 <= ALT_MAX        (= 아직 안 따라옴)
  거래대금 하한 통과
진입: t+1 봉 시가 (신호 봉에서는 절대 체결하지 않는다)
청산: TP / SL / N봉 시간초과.  한 봉에 TP·SL 둘 다면 SL.
"""
import os, sys, glob, itertools, argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

M5 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scalp5m")
FEE_MAKER, FEE_TAKER, SLIP = 0.0002, 0.0005, 0.0003     # 선물 지정가/시장가 기준


def load_all_5m(exclude=("BTC_USDT", "ETH_USDT")):
    out = {}
    for p in sorted(glob.glob(os.path.join(M5, "spot_*_5m.csv"))):
        sym = os.path.basename(p)[len("spot_"):-len("_5m.csv")]
        if sym in exclude:
            continue
        try:
            d = pd.read_csv(p)
        except Exception:
            continue
        if len(d) < 200:
            continue
        out[sym] = d.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return out


def btc_series():
    d = pd.read_csv(os.path.join(M5, "spot_BTC_USDT_5m.csv"))
    d = d.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return d


def run(alts, btc, K, btc_up, alt_max, tp, sl, hold, min_qv5=20_000, taker_in=True):
    bt = btc.set_index("timestamp")["close"]
    btc_ret = (bt / bt.shift(K) - 1.0)
    bmap = btc_ret.to_dict()

    rets, whys, syms = [], [], []
    for sym, d in alts.items():
        ts = d["timestamp"].values
        c, o, h, l = d["close"].values, d["open"].values, d["high"].values, d["low"].values
        v = (d["volume"].values * c)
        n = len(d)
        if n < K + hold + 3:
            continue
        # 봉이 끊긴 구간(수집이 조각조각임)을 건너뛰기 위해 연속성 확인
        cont = np.zeros(n, dtype=bool)
        cont[K:] = (ts[K:] - ts[:-K]) == K * 300_000
        i = K
        while i < n - hold - 2:
            if not cont[i]:
                i += 1; continue
            br = bmap.get(int(ts[i]))
            if br is None or br < btc_up:
                i += 1; continue
            ar = c[i] / c[i - K] - 1.0
            if ar > alt_max:
                i += 1; continue
            if v[i - K:i + 1].mean() < min_qv5:
                i += 1; continue
            # 진입: 다음 봉 시가
            e = i + 1
            px = o[e] * (1 + SLIP) if taker_in else o[e]
            fee_in = FEE_TAKER + SLIP if taker_in else FEE_MAKER
            tp_px, sl_px = px * (1 + tp), px * (1 - sl)
            last = min(n - 1, e + hold)
            out = None
            for j in range(e + 1, last + 1):        # 체결 봉 제외
                if l[j] <= sl_px:
                    out = ((sl_px / px - 1) - fee_in - FEE_TAKER - SLIP, "SL"); break
                if h[j] >= tp_px:
                    out = ((tp_px / px - 1) - fee_in - FEE_MAKER, "TP"); break
            if out is None:
                out = ((c[last] / px - 1) - fee_in - FEE_TAKER - SLIP, "TIME")
            rets.append(out[0]); whys.append(out[1]); syms.append(sym)
            i = last + 1                             # 겹치는 관측 금지 (규칙)
    if len(rets) < 50:
        return None
    a = np.array(rets); w = np.array(whys)
    wins, loss = a[a > 0], a[a <= 0]
    pf = wins.sum() / abs(loss.sum()) if len(loss) and loss.sum() != 0 else float("inf")
    return dict(n=len(a), win=(a > 0).mean(), mean=a.mean(), total=a.sum(), pf=pf,
                nsym=len(set(syms)), tp=(w == "TP").mean(), sl=(w == "SL").mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ks", default="6,12,24")            # 30분 / 1시간 / 2시간
    ap.add_argument("--btc-ups", default="0.005,0.01,0.015")
    ap.add_argument("--alt-maxs", default="0.0,0.003")
    ap.add_argument("--tps", default="0.01,0.015")
    ap.add_argument("--sls", default="0.01,0.02")
    ap.add_argument("--hold", type=int, default=24)
    args = ap.parse_args()

    alts = load_all_5m(); btc = btc_series()
    print(f"알트 {len(alts)}종 / BTC {len(btc):,}봉\n")
    print(f"{'K':>4}{'BTC↑':>8}{'알트≤':>8}{'TP/SL':>11}{'건수':>7}{'심볼':>6}{'승률':>7}{'평균':>9}{'PF':>7}{'손익분기':>9}")
    print("-" * 82)
    rows = []
    for K, bu, am, tp, sl in itertools.product(
            [int(x) for x in args.ks.split(",")],
            [float(x) for x in args.btc_ups.split(",")],
            [float(x) for x in args.alt_maxs.split(",")],
            [float(x) for x in args.tps.split(",")],
            [float(x) for x in args.sls.split(",")]):
        r = run(alts, btc, K, bu, am, tp, sl, args.hold)
        if not r:
            continue
        be = sl / (tp + sl)
        rows.append((r["mean"], K, bu, am, tp, sl, r, be))
        print(f"{K:>4}{bu*100:>7.1f}%{am*100:>7.1f}%{f'+{tp*100:.1f}/-{sl*100:.0f}':>11}"
              f"{r['n']:>7}{r['nsym']:>6}{r['win']*100:>6.1f}%{r['mean']*100:>+8.3f}%{r['pf']:>7.2f}{be*100:>8.1f}%")
    rows.sort(reverse=True)
    print("\n=== 상위 5 ===")
    for m, K, bu, am, tp, sl, r, be in rows[:5]:
        print(f"  K={K}봉 BTC≥{bu*100:.1f}% 알트≤{am*100:.1f}% TP+{tp*100:.1f}/SL-{sl*100:.0f}  "
              f"평균 {m*100:+.3f}%  승률 {r['win']*100:.1f}% (손익분기 {be*100:.1f}%)  n={r['n']} {r['nsym']}종")


if __name__ == "__main__":
    main()
