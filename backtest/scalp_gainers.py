"""
오늘 급등 상위 N종목 → 다음날 롱 스캘핑 (5분봉, 보수 회계).

STRATEGY_RULES 3.3: 체결된 봉에서는 청산을 보지 않는다.
한 봉 안에서 TP·SL 이 둘 다 닿으면 SL 로 친다 (순서를 모르므로 나쁜 쪽).
"""
import os, sys, glob, itertools, argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.gainer_scalp import load_daily, build_panel

M5 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scalp5m")
DAY = 86_400_000
BAR = 300_000

# 수수료: 선물 지정가 메이커 0.02%, 손절은 시장가 테이커 0.05%
FEE_MAKER, FEE_TAKER, SLIP = 0.0002, 0.0005, 0.0003


def load_5m():
    out = {}
    for p in glob.glob(os.path.join(M5, "spot_*_5m.csv")):
        sym = os.path.basename(p)[len("spot_"):-len("_5m.csv")]
        try:
            d = pd.read_csv(p)
        except Exception:
            continue
        if len(d) < 50:
            continue
        d = d.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        out[sym] = d
    return out


def gainer_days(months, topn, minqv=3e6, rank_lo=1):
    daily = load_daily()
    p = build_panel(daily)
    p = p[p["qv20"] >= minqv]
    p["rk"] = p.groupby("timestamp")["chg"].rank(ascending=False, method="first")
    sel = p[(p["rk"] >= rank_lo) & (p["rk"] <= topn)]
    cut = p["timestamp"].max() - months * 30 * DAY
    sel = sel[sel["timestamp"] >= cut]
    return [(r.sym, int(r.timestamp) + DAY, r.chg) for r in sel.itertuples()]


def one_trade(bars, entry_mode, tp, sl, wait_bars, hold_bars):
    """bars: 그 날 5분봉 DataFrame. 반환 (수익률, 사유) 또는 None."""
    o = bars["open"].values; h = bars["high"].values
    l = bars["low"].values;  c = bars["close"].values
    n = len(bars)
    if n < 20:
        return None
    day_open = o[0]

    # --- 진입 ---
    if entry_mode == "open":
        ei, px, maker = 0, day_open, False
    elif entry_mode.startswith("dip"):                 # 시가 대비 -x% 지정가
        x = float(entry_mode[3:]) / 100.0
        px = day_open * (1 - x)
        ei = None
        for i in range(min(wait_bars, n)):
            if l[i] <= px:
                ei = i; break
        if ei is None:
            return None
        maker = True
    elif entry_mode.startswith("brk"):                 # 첫 k봉 고가 돌파 추격
        k = int(entry_mode[3:])
        if n <= k:
            return None
        px = h[:k].max()
        ei = None
        for i in range(k, min(k + wait_bars, n)):
            if h[i] >= px:
                ei = i; break
        if ei is None:
            return None
        maker = False
    else:
        raise ValueError(entry_mode)

    fee_in = (FEE_MAKER if maker else FEE_TAKER + SLIP)
    tp_px, sl_px = px * (1 + tp), px * (1 - sl)
    last = min(n - 1, ei + hold_bars)
    for i in range(ei + 1, last + 1):                  # 체결 봉은 제외
        if l[i] <= sl_px:                              # 같은 봉이면 SL 우선
            return (sl_px / px - 1) - fee_in - FEE_TAKER - SLIP, "SL"
        if h[i] >= tp_px:
            return (tp_px / px - 1) - fee_in - FEE_MAKER, "TP"
    return (c[last] / px - 1) - fee_in - FEE_TAKER - SLIP, "TIME"


def run(days, m5, entry_mode, tp, sl, wait_bars, hold_bars, offset=0, span=1):
    rets, why = [], []
    for sym, d0, _chg in days:
        d = m5.get(sym)
        if d is None:
            continue
        s0 = d0 + offset * DAY
        b = d[(d["timestamp"] >= s0) & (d["timestamp"] < s0 + span * DAY)]
        if len(b) < 20:
            continue
        r = one_trade(b.reset_index(drop=True), entry_mode, tp, sl, wait_bars, hold_bars)
        if r is None:
            continue
        rets.append(r[0]); why.append(r[1])
    if not rets:
        return None
    a = np.array(rets); w = np.array(why)
    wins, losses = a[a > 0], a[a <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else float("inf")
    return dict(n=len(a), win=(a > 0).mean(), mean=a.mean(), total=a.sum(), pf=pf,
                tp=(w == "TP").mean(), slr=(w == "SL").mean(), tm=(w == "TIME").mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--topn", type=int, default=10)
    ap.add_argument("--rank-lo", type=int, default=1)
    ap.add_argument("--hold", type=int, default=288, help="최대 보유 5분봉 수")
    ap.add_argument("--wait", type=int, default=48, help="진입 대기 봉 수")
    ap.add_argument("--offset", type=int, default=0, help="신호 다음날 기준 며칠 뒤에 거래 (대조군용)")
    ap.add_argument("--span", type=int, default=1, help="사용할 일수")
    ap.add_argument("--modes", default="open,dip1,dip2,dip3,dip5,dip8")
    ap.add_argument("--tps", default="0.01,0.015,0.02,0.03")
    ap.add_argument("--sls", default="0.02,0.03,0.05")
    args = ap.parse_args()

    m5 = load_5m()
    days = gainer_days(args.months, args.topn, rank_lo=args.rank_lo)
    have = sum(1 for s, _, _ in days if s in m5)
    print(f"5분봉 {len(m5)}종 / 대상 심볼-일 {len(days):,}건 (데이터 있는 것 {have:,}건)\n")

    modes = args.modes.split(",")
    TPS = [float(x) for x in args.tps.split(",")]
    SLS = [float(x) for x in args.sls.split(",")]
    print(f"{'진입':<7}{'TP/SL':<12}{'건수':>6}{'승률':>7}{'평균':>8}{'PF':>7}   TP/SL/시간초과")
    print("-" * 78)
    best = []
    for mode in modes:
        for tp, sl in itertools.product(TPS, SLS):
            r = run(days, m5, mode, tp, sl, args.wait, args.hold, args.offset, args.span)
            if not r or r["n"] < 100:
                continue
            best.append((r["mean"], mode, tp, sl, r))
            print(f"{mode:<7}{f'+{tp*100:.1f}/-{sl*100:.0f}':<12}{r['n']:>6}{r['win']*100:>6.1f}%"
                  f"{r['mean']*100:>+7.2f}%{r['pf']:>7.2f}   "
                  f"{r['tp']*100:.0f}/{r['slr']*100:.0f}/{r['tm']*100:.0f}")
    best.sort(reverse=True)
    print("\n=== 상위 5 ===")
    for m, mode, tp, sl, r in best[:5]:
        print(f"  {mode} TP+{tp*100:.1f}% SL-{sl*100:.0f}%  평균 {m*100:+.3f}%  "
              f"승률 {r['win']*100:.1f}%  누적 {r['total']*100:+.0f}%  n={r['n']}")


if __name__ == "__main__":
    main()
