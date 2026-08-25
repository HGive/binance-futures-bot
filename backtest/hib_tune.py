"""
HIBERNATE 미세조정 — 파라미터를 하나씩 흔들어서 워크포워드로 잰다.

주의(STRATEGY_RULES 3.9 / 4): 같은 8년 데이터를 계속 두드리는 것이다.
그래서 아래를 같이 본다.
  ① 전체 24구간 성적
  ② 탐색에 쓰지 않은 2024년 이후 구간 성적   ← 이쪽이 안 따라오면 채택 안 한다
  ③ 이웃값들이 같이 좋아지는가 (한 칸만 좋으면 노이즈)
"""
import os, sys, itertools, argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.hibernate as S
import backtest.walkforward as W

_DATA = None
_PRE = {}


def data():
    global _DATA
    if _DATA is None:
        symbols, daily = W.load_all(market="spot")
        dates = np.array(sorted(set(np.concatenate([daily[s]["dt"].values for s in symbols]))))
        idx = {s: {t: i for i, t in enumerate(daily[s]["dt"].values)} for s in symbols}
        last = {s: daily[s]["dt"].values[-1] for s in symbols}
        _DATA = (symbols, daily, dates, idx, last)
    return _DATA


def pre_for():
    """precompute 는 슈팅 정의(SPIKE_*) 와 MIN_SPIKES 에만 의존한다."""
    key = (S.SPIKE_BASE_DAYS, S.SPIKE_MIN_PCT, S.RETRACE_DAYS, S.RETRACE_TOL,
           S.SPIKE_CLUSTER_DAYS, S.REACH_WINDOW, S.YEAR_WINDOW)
    if key not in _PRE:
        symbols, daily, *_ = data()
        _PRE[key] = W.base_arrays(symbols, daily)
    return _PRE[key]


def regimes():
    symbols, daily, dates, *_ = data()
    if not hasattr(regimes, "_c"):
        regimes._c = W.regime_maps(symbols, daily, dates)
    return regimes._c


def run(train=365, test=90, seed=1000.0, regime="btc_ma100", style="adaptive",
        droughts=(90, 120), poses=(0.35, 0.50)):
    """walkforward.py main() 과 같은 절차.

    구간마다 (공백일수, 가격위치) 조합을 **학습 구간 성적으로만** 고르고 검증 구간에 적용한다.
    미래를 보지 않으므로 정당하지만, 이 선택 자체가 성적의 상당 부분을 만든다.
    """
    W.STYLE = W.EXIT_STYLES[style]
    symbols, daily, dates, idx, last = data()
    pre = pre_for()
    regs = regimes()
    mcache = {}

    def masks_for(dr, pos):
        k = (dr, pos, S.MIN_SPIKES, S.NOT_UP_MAX, S.MIN_DAILY_QUOTE_VOL,
             S.MIN_TGT_RATIO, S.MAX_TGT_RATIO, S.TP_FRACTION, S.TP_MIN, S.TP_MAX,
             S.ATL_TOL, S.MIN_ATL_AGE)
        if k not in mcache:
            mcache[k] = {s: W.signal_mask(pre, daily, s, dr, pos, S.MIN_SPIKES) for s in symbols}
        return mcache[k]

    drought = {s: pre[s]["drought"] for s in symbols}
    grid = list(itertools.product(droughts, poses))
    start = W.WARMUP
    cash, positions = seed, {}
    all_tr, all_eq, segs = [], [], []
    while start + train + test <= len(dates):
        tr_a, tr_b = start, start + train
        te_a, te_b = tr_b, tr_b + test
        best, bp = -9e9, grid[0]
        if len(grid) > 1:
            for dr, pos in grid:
                t, e, _, _ = W.simulate(symbols, daily, pre, dates, tr_a, tr_b, masks_for(dr, pos),
                                        regs[regime], cash=1000.0, positions={}, idx=idx,
                                        last_day=last, drought_arr=drought)
                sc, _ = W.score(t, e, 1000.0)
                if sc > best:
                    best, bp = sc, (dr, pos)
        t, e, positions, cash = W.simulate(symbols, daily, pre, dates, te_a, te_b,
                                           masks_for(*bp), regs[regime], cash=cash,
                                           positions=positions, idx=idx, last_day=last,
                                           drought_arr=drought)
        r = (e["equity"].iloc[-1] / e["equity"].iloc[0] - 1) if len(e) else 0.0
        segs.append((pd.Timestamp(dates[te_a]), r, len(t)))
        all_tr.append(t); all_eq.append(e)
        start += test
    tr = pd.concat(all_tr, ignore_index=True) if all_tr else pd.DataFrame()
    eq = pd.concat(all_eq, ignore_index=True) if all_eq else pd.DataFrame()
    if not len(eq):
        return None
    peak = eq["equity"].cummax()
    mdd = float(((eq["equity"] - peak) / peak).min())
    w = tr[tr.pnl > 0]; lo = tr[tr.pnl <= 0]
    pf = w.pnl.sum() / abs(lo.pnl.sum()) if len(lo) and lo.pnl.sum() != 0 else float("inf")
    cut = pd.Timestamp("2024-01-01")
    clean = [s for s in segs if s[0] >= cut]
    cret = np.prod([1 + s[1] for s in clean]) - 1 if clean else np.nan
    return dict(ret=eq["equity"].iloc[-1] / seed - 1, mdd=mdd, pf=pf, n=len(tr),
                win=(tr.pnl > 0).mean() if len(tr) else np.nan,
                wins=sum(1 for s in segs if s[1] > 0), windows=len(segs),
                clean_ret=cret, clean_wins=sum(1 for s in clean if s[1] > 0),
                clean_n=len(clean), clean_trades=sum(s[2] for s in clean))


BASE = {}


def snapshot():
    return {k: getattr(S, k) for k in
            ("SPIKE_MIN_PCT", "MIN_SPIKES", "NOT_UP_MAX", "MIN_DAILY_QUOTE_VOL",
             "MIN_DROUGHT_DAYS", "MAX_PRICE_POS", "TP_FRACTION", "TP_MIN", "TP_MAX",
             "STOP_PCT", "MIN_TGT_RATIO", "MAX_TGT_RATIO", "SPLIT_AT_FIRST",
             "DOUBLE_TP", "TICKER_MARGIN_PCT", "MAX_CONCURRENT", "REACH_WINDOW")}


def restore(snap):
    for k, v in snap.items():
        setattr(S, k, v)


def line(tag, r, base=None):
    if r is None:
        print(f"{tag:<32} 거래 없음"); return
    d = ""
    if base:
        d = f"  ({(r['ret']-base['ret'])*100:+.0f}%p / 청정 {(r['clean_ret']-base['clean_ret'])*100:+.0f}%p)"
    print(f"{tag:<32}{r['ret']*100:>+9.1f}%{r['pf']:>7.2f}{r['mdd']*100:>7.1f}%{r['n']:>6}"
          f"{r['wins']}/{r['windows']:<4}"
          f"{r['clean_ret']*100:>+9.1f}%{r['clean_wins']}/{r['clean_n']:<4}{r['clean_trades']:>5}{d}", flush=True)


if __name__ == "__main__":
    print("HIBERNATE 파라미터 민감도 — 워크포워드 24구간\n")
    print(f"{'설정':<32}{'전체수익':>10}{'PF':>7}{'MDD':>7}{'거래':>6}{'구간':<6}"
          f"{'2024+':>10}{'구간':<6}{'거래':>5}")
    print("-" * 96)
    snap = snapshot()
    base = run()
    line("기준 (v1.0)", base)
    print()

    SWEEPS = [
        ("SPIKE_MIN_PCT", [0.25, 0.30, 0.40, 0.50]),
        ("MIN_SPIKES", [1, 3]),
        ("NOT_UP_MAX", [1.00, 1.30, 2.00]),
        ("TP_FRACTION", [0.35, 0.40, 0.60, 0.75]),
        ("STOP_PCT", [-0.12, -0.15, -0.25, -0.30]),
        ("MIN_TGT_RATIO", [0.15, 0.20, 0.30, 0.35]),
        ("MAX_TGT_RATIO", [0.45, 0.50, 0.70, 0.90]),
        ("SPLIT_AT_FIRST", [0.4, 0.5, 0.6, 0.8, 1.0]),
        ("DOUBLE_TP", [0.60, 0.75, 1.50, 2.00]),
        ("TP_MIN", [0.05, 0.12, 0.15]),
        ("TP_MAX", [0.50, 0.60, 1.00]),
        ("MIN_DAILY_QUOTE_VOL", [3e5, 3e6, 1e7]),
        ("MAX_CONCURRENT", [6, 10, 12]),
        ("TICKER_MARGIN_PCT", [0.06, 0.08, 0.12]),
    ]
    # 구간마다 고르는 격자 자체를 흔들어본다
    for dro, pos in [((60, 90, 120), (0.35, 0.50)), ((90, 120, 180), (0.35, 0.50)),
                     ((90, 120), (0.25, 0.35, 0.50)), ((90, 120), (0.30, 0.40)),
                     ((120,), (0.35,)), ((90,), (0.35,)), ((90, 120, 180), (0.25, 0.35, 0.50))]:
        line(f"  격자 공백{list(dro)} 위치{list(pos)}", run(droughts=dro, poses=pos), base)
    print()

    for name, vals in SWEEPS:
        cur = getattr(S, name)
        for v in vals:
            setattr(S, name, v)
            try:
                r = run()
            except Exception as e:
                r = None
                print(f"  {name}={v} 실패: {e}")
            line(f"  {name} {cur} → {v}", r, base)
        setattr(S, name, cur)
        print()
    restore(snap)
