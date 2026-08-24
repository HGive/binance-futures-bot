"""
워크포워드 검증.

단일 70/30 분할의 문제: 앞 구간이 어떤 장이었느냐에 결과가 통째로 좌우된다.
실제로 spike_drought 는 개발구간(BTC +374%)에서 +80%, 검증구간(BTC -37%)에서 -42% 였다.

여기서는 이렇게 한다:
    [학습 365일] → [검증 90일] → 90일 밀기 → [학습 365일] → [검증 90일] → ...

각 학습 구간에서만 파라미터를 고르고, 바로 다음 90일에 그대로 적용한다.
검증 구간들을 이어붙인 것이 최종 성적이다. 상승장·하락장이 모두 섞여 들어간다.

자본은 검증 구간들을 가로질러 계속 이어진다(포지션도 유지). 파라미터는 진입 조건만
바뀌고 청산 규칙은 고정이므로, 창이 바뀌어도 보유 포지션 처리에 모순이 없다.
"""
import os, sys, glob, argparse, itertools
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.spike_drought as S  # noqa: E402
from backtest.spike_drought import load_all, WARMUP  # noqa: E402

# 진입/청산 구조 변형. 파라미터가 아니라 '설계'라서 따로 비교한다.
#   add_stop : 절반 진입 → -23.3%에서 추매 → 평단 -23.3%에서 손절   (기존)
#   no_add   : 처음부터 전액 진입 → 진입가 -23.3%에서 손절          (물타기 없음)
#   add_wide : 절반 진입 → 추매 → 평단 -40%에서 손절                (손절선을 멀리)
#   add_hold : 절반 진입 → 추매 → 손절 없음, 익절/상폐까지 보유      (손매매 방식에 가장 가까움)
EXIT_STYLES = {
    "add_stop": dict(add=True,  first=0.5, stop_from_avg=-0.233, adaptive_tp=False),
    "no_add":   dict(add=False, first=1.0, stop_from_avg=-0.233, adaptive_tp=False),
    "add_wide": dict(add=True,  first=0.5, stop_from_avg=-0.40,  adaptive_tp=False),
    "add_hold": dict(add=True,  first=0.5, stop_from_avg=None,   adaptive_tp=False),
    # 익절 목표를 종목별 과거 슈팅 크기에서 뽑는 방식 (물타기 없음)
    "adaptive": dict(add=False, first=1.0, stop_from_avg=None,   adaptive_tp=True),
}
STYLE = EXIT_STYLES["add_stop"]


# ===================================================================== 레짐
def regime_maps(symbols, daily, dates):
    """날짜 → 진입 허용 여부. 전부 그날까지의 과거만 사용."""
    out = {"none": None}
    btc = daily.get("BTC/USDT")
    if btc is not None:
        c = btc["close"]
        for n in (50, 100, 200):
            up = (c > c.rolling(n).mean()).values
            out[f"btc_ma{n}"] = dict(zip(btc["dt"].values, up))
    # 브레드스: 유니버스 중 자기 50일선 위에 있는 종목 비율
    frames = []
    for s in symbols:
        d = daily[s]
        above = (d["close"] > d["close"].rolling(50).mean())
        frames.append(pd.Series(above.values, index=d["dt"].values, name=s))
    breadth = pd.concat(frames, axis=1).mean(axis=1, skipna=True)
    for th in (0.25, 0.40):
        out[f"breadth{int(th*100)}"] = dict(zip(breadth.index.values, (breadth > th).values))
    return out


# ===================================================================== 신호
def base_arrays(symbols, daily):
    return {s: S.precompute(daily[s]) for s in symbols}


def uptrend_dip_mask(pre, daily, sym, pos_max=0.12, min_ret=0.15, min_vol=0.05):
    """
    두 번째 자리 — '상승 추세인데 깊게 눌린 것'.

    첫 번째 전략(spike_drought)은 1년 전 대비 오르지 않은 종목만 본다.
    여기는 정확히 그 반대편 — 1년으로 보면 올랐는데 지금은 1년 범위 바닥에 있는 자리다.
    두 전략은 조건상 겹치지 않는다.

    차트 모양 지도(docs/pattern_map)에서 기대값 +5.9~6.6% 로 가장 높았던 칸이다.
    """
    P = pre[sym]
    d = daily[sym]
    c = d["close"].values
    n = len(c)
    ret1y = np.full(n, np.nan)
    ret1y[365:] = c[365:] / c[:-365] - 1
    vol90 = d["close"].pct_change().rolling(90).std().values
    with np.errstate(invalid="ignore", divide="ignore"):
        tgt = np.clip(P["tp_base"] * S.TP_FRACTION, S.TP_MIN, S.TP_MAX)
        ratio = tgt / P["reach"]
        m = ((ret1y >= min_ret) &
             (P["pos"] <= pos_max) &
             (vol90 >= min_vol) &
             (P["qv30"] >= S.MIN_DAILY_QUOTE_VOL) &
             (ratio >= S.MIN_TGT_RATIO) & (ratio <= S.MAX_TGT_RATIO))
    return np.nan_to_num(m, nan=0).astype(bool)


def signal_mask(pre, daily, sym, drought, pos_max, min_spikes):
    """파라미터에 따른 진입 가능일 불리언 배열 (레짐 제외)."""
    P = pre[sym]
    c = daily[sym]["close"].values
    with np.errstate(invalid="ignore", divide="ignore"):
        tgt = np.clip(P["tp_base"] * S.TP_FRACTION, S.TP_MIN, S.TP_MAX)
        ratio = tgt / P["reach"]
        atl_ok = (np.ones(len(c), dtype=bool) if S.ATL_TOL is None
                  else ((c <= P["atl"] * (1 + S.ATL_TOL)) &
                        (P["atl_age"] >= S.MIN_ATL_AGE)))
        m = (atl_ok &
             (P["spike_cnt"] >= min_spikes) &
             (c <= P["year_ago"] * S.NOT_UP_MAX) &
             (P["qv30"] >= S.MIN_DAILY_QUOTE_VOL) &
             (P["drought"] >= drought) &
             (P["pos"] <= pos_max) &
             (ratio >= S.MIN_TGT_RATIO) & (ratio <= S.MAX_TGT_RATIO))
    return np.nan_to_num(m, nan=0).astype(bool)


class Pos:
    __slots__ = ("sym", "qty", "avg", "entry", "margin", "budget", "added",
                 "open_i", "open_ts", "tp_px", "sl_px", "runner")
    def __init__(self, sym, qty, entry, margin, budget, open_i, open_ts, tp_px=None, sl_px=None):
        self.sym, self.qty, self.avg, self.entry = sym, qty, entry, entry
        self.margin, self.budget, self.added = margin, budget, False
        self.open_i, self.open_ts = open_i, open_ts
        self.tp_px, self.sl_px = tp_px, sl_px      # 진입 시점에 확정 (종목별 목표)
        self.runner = False                        # 1차 목표에서 일부만 팔고 남은 물량인가


def simulate(symbols, daily, pre, dates, a, b, masks, regime, cash=1000.0,
             positions=None, idx=None, last_day=None, drought_arr=None):
    """a~b 구간 시뮬레이션. positions 를 넘기면 이어서 돌린다."""
    positions = positions if positions is not None else {}
    trades, curve = [], []
    for di in range(a, b):
        today = dates[di]
        # --- 보유 포지션 ---
        for sym in list(positions):
            p = positions[sym]
            i = idx[sym].get(today)
            if i is None:
                if today > last_day[sym]:                     # 상장폐지
                    px = float(daily[sym]["close"].iloc[-1])
                    fill = px * (1 - S.SLIPPAGE)
                    pnl = max(p.qty*(fill-p.avg) - p.qty*fill*S.FEE_RATE, -p.margin)
                    cash += p.margin + pnl
                    trades.append({"symbol": sym, "exit_ts": today, "reason": "DELISTED",
                                   "hold_days": int((today-p.open_ts)/np.timedelta64(1,"D")),
                                   "margin": p.margin, "pnl": pnl, "roi": pnl/p.margin})
                    del positions[sym]
                continue
            if i <= p.open_i:
                continue
            row = daily[sym].iloc[i]
            hi, lo, op = row["high"], row["low"], row["open"]
            trig = S.add_trigger_price(p.entry)
            if STYLE["add"] and not p.added and lo <= trig:
                add_m = p.budget * (1 - S.FIRST_ENTRY_FRAC)
                if cash >= add_m:
                    fill = min(trig, op) * (1 + S.SLIPPAGE)
                    aq = add_m * S.LEVERAGE / fill
                    cash -= add_m + aq*fill*S.FEE_RATE
                    p.avg = (p.qty*p.avg + aq*fill)/(p.qty+aq); p.qty += aq; p.margin += add_m
                p.added = True
            reason = px = None
            if STYLE["adaptive_tp"]:
                stop_px, tp_px = p.sl_px, p.tp_px
            else:
                sf = STYLE["stop_from_avg"]
                stop_px = p.avg * (1 + sf) if sf is not None else None
                tp_px = S.take_profit_price(p.avg)
            held = int((today - p.open_ts) / np.timedelta64(1, "D"))
            if stop_px is not None and lo <= stop_px and (p.added or not STYLE["add"]):
                reason, px = "STOP", min(stop_px, op)
            elif S.MAX_HOLD_DAYS and held >= S.MAX_HOLD_DAYS:
                reason, px = "TIME", row["close"]      # 오래 묶인 자리는 비운다
            elif hi >= tp_px:
                # 1차 목표: 일부만 팔고 나머지는 2배까지 끌고 간다 (SPLIT_AT_FIRST < 1 일 때)
                if STYLE["adaptive_tp"] and not p.runner and S.SPLIT_AT_FIRST < 1.0:
                    sell_q = p.qty * S.SPLIT_AT_FIRST
                    if sell_q > 0:
                        fill = tp_px * (1 - S.SLIPPAGE)
                        part = sell_q * (fill - p.avg) - sell_q * fill * S.FEE_RATE
                        cash += p.margin * S.SPLIT_AT_FIRST + part
                        trades.append({"symbol": sym, "exit_ts": today, "reason": "TP1",
                                       "hold_days": int((today-p.open_ts)/np.timedelta64(1,"D")),
                                       "margin": p.margin*S.SPLIT_AT_FIRST, "pnl": part,
                                       "roi": part/(p.margin*S.SPLIT_AT_FIRST)})
                        p.qty -= sell_q
                        p.margin *= (1 - S.SPLIT_AT_FIRST)
                    p.runner = True
                    p.tp_px = p.entry * (1 + S.DOUBLE_TP)      # 남은 물량은 2배 목표
                    continue
                reason, px = ("TP2" if p.runner else "TP"), tp_px
            if reason:
                fill = px * (1 - S.SLIPPAGE)
                pnl = max(p.qty*(fill-p.avg) - p.qty*fill*S.FEE_RATE, -p.margin)
                cash += p.margin + pnl
                trades.append({"symbol": sym, "exit_ts": today, "reason": reason,
                               "hold_days": int((today-p.open_ts)/np.timedelta64(1,"D")),
                               "margin": p.margin, "pnl": pnl, "roi": pnl/p.margin})
                del positions[sym]

        # --- 신규 진입 ---
        allow = True if regime is None else bool(regime.get(today, False))
        if allow and len(positions) < S.MAX_CONCURRENT:
            eqn = cash + sum(pp.margin + pp.qty*(daily[s].iloc[idx[s][today]]["close"] - pp.avg)
                             for s, pp in positions.items() if today in idx[s])
            budget = max(eqn, 0) * S.TICKER_MARGIN_PCT
            first = budget * STYLE["first"]
            cands = []
            for sym in symbols:
                if sym in positions:
                    continue
                i = idx[sym].get(today)
                if i is None or i < WARMUP or i+1 >= len(daily[sym]):
                    continue
                if int((last_day[sym]-today)/np.timedelta64(1,"D")) < 5:
                    continue
                if masks[sym][i]:
                    cands.append((drought_arr[sym][i], sym, i))
            cands.sort(reverse=True)
            for _, sym, i in cands:
                if len(positions) >= S.MAX_CONCURRENT or cash < budget:
                    break
                nxt = daily[sym].iloc[i+1]
                fill = nxt["open"] * (1 + S.SLIPPAGE)
                if fill <= 0:
                    continue
                qty = first * S.LEVERAGE / fill
                cash -= first + qty*fill*S.FEE_RATE
                tp_px = sl_px = None
                if STYLE["adaptive_tp"]:
                    tp_px = fill * (1 + S.target_gain(pre[sym], i))
                    sl_px = fill * (1 + S.STOP_PCT)
                positions[sym] = Pos(sym, qty, fill, first, budget, i+1,
                                     np.datetime64(nxt["dt"].to_datetime64()), tp_px, sl_px)

        eq = cash
        for s, pp in positions.items():
            i = idx[s].get(today)
            eq += pp.margin if i is None else max(pp.margin + pp.qty*(daily[s].iloc[i]["close"]-pp.avg), 0)
        curve.append((today, eq))
    return pd.DataFrame(trades), pd.DataFrame(curve, columns=["dt","equity"]), positions, cash


def score(tr, eq, seed):
    if not len(eq):
        return -9e9, {}
    final = eq["equity"].iloc[-1]; peak = eq["equity"].cummax()
    mdd = float(((eq["equity"]-peak)/peak).min())
    ret = final/seed - 1
    # MDD 를 벌점으로 반영해 '수익만 큰' 조합을 피한다
    return ret + 2.0*mdd, {"ret": ret*100, "mdd": mdd*100, "n": len(tr)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=int, default=365)
    ap.add_argument("--test", type=int, default=90)
    ap.add_argument("--seed-usdt", type=float, default=1000.0)
    ap.add_argument("--regime", default="auto",
                    help="auto=학습구간 성적으로 선택, 그 외에는 해당 레짐으로 고정")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--style", default="add_stop", choices=list(EXIT_STYLES))
    ap.add_argument("--tp-frac", type=float, default=None, help="과거 슈팅 크기의 몇 배를 목표로")
    ap.add_argument("--stop", type=float, default=None, help="손절 (예: -0.20)")
    ap.add_argument("--ratio-min", type=float, default=None)
    ap.add_argument("--ratio-max", type=float, default=None)
    ap.add_argument("--droughts", default="90,120")
    ap.add_argument("--poses", default="0.35,0.50")
    ap.add_argument("--ticker-pct", type=float, default=None)
    ap.add_argument("--max-concurrent", type=int, default=None)
    ap.add_argument("--min-vol", type=float, default=None)
    ap.add_argument("--atl-tol", type=float, default=None, help="상장 이후 최저가 대비 +N 이내")
    ap.add_argument("--split-first", type=float, default=None, help="1차 목표에서 파는 비중")
    ap.add_argument("--atl-age", type=int, default=None, help="최저가가 며칠 전 것이어야 하는가")
    ap.add_argument("--max-hold", type=int, default=None, help="이 일수 넘게 안 끝나면 정리")
    ap.add_argument("--strategy", default="drought",
                    choices=["drought", "uptrend_dip", "both"])
    args = ap.parse_args()

    globals()["STYLE"] = EXIT_STYLES[args.style]
    if args.tp_frac is not None:
        S.TP_FRACTION = args.tp_frac
    if args.stop is not None:
        S.STOP_PCT = args.stop
    if args.ratio_min is not None:
        S.MIN_TGT_RATIO = args.ratio_min
    if args.ratio_max is not None:
        S.MAX_TGT_RATIO = args.ratio_max
    if args.ticker_pct is not None:
        S.TICKER_MARGIN_PCT = args.ticker_pct
    if args.max_concurrent is not None:
        S.MAX_CONCURRENT = args.max_concurrent
    if args.min_vol is not None:
        S.MIN_DAILY_QUOTE_VOL = args.min_vol
    if args.atl_tol is not None:
        S.ATL_TOL = args.atl_tol
    if args.atl_age is not None:
        S.MIN_ATL_AGE = args.atl_age
    if args.max_hold is not None:
        S.MAX_HOLD_DAYS = args.max_hold
    if args.split_first is not None:
        S.SPLIT_AT_FIRST = args.split_first
    symbols, daily = load_all(market="spot")
    pre = base_arrays(symbols, daily)
    dates = np.array(sorted(set(np.concatenate([daily[s]["dt"].values for s in symbols]))))
    idx = {s: {t: i for i, t in enumerate(daily[s]["dt"].values)} for s in symbols}
    last_day = {s: daily[s]["dt"].values[-1] for s in symbols}
    drought_arr = {s: pre[s]["drought"] for s in symbols}
    regs = regime_maps(symbols, daily, dates)
    print(f"[자리 {S.MIN_TGT_RATIO}~{S.MAX_TGT_RATIO} / 역사적저점 {S.ATL_TOL} / 1차매도 {S.SPLIT_AT_FIRST:.0%}] 심볼 {len(symbols)}종 | 전체 {pd.Timestamp(dates[0]).date()} ~ {pd.Timestamp(dates[-1]).date()}")

    REGS = (["none", "btc_ma100", "btc_ma200", "breadth25", "breadth40"]
            if args.regime == "auto" else [args.regime])
    DROUGHTS = [int(x) for x in args.droughts.split(",")]
    POSES = [float(x) for x in args.poses.split(",")]
    GRID = list(itertools.product(DROUGHTS, POSES, [2], REGS))
    mask_cache = {}
    def get_masks(dr, pos, sp):
        key = (dr, pos, sp, args.strategy)
        if key not in mask_cache:
            if args.strategy == "drought":
                m = {s: signal_mask(pre, daily, s, dr, pos, sp) for s in symbols}
            elif args.strategy == "uptrend_dip":
                m = {s: uptrend_dip_mask(pre, daily, s) for s in symbols}
            else:                                    # both — 두 자리를 함께 본다
                m = {s: (signal_mask(pre, daily, s, dr, pos, sp) |
                         uptrend_dip_mask(pre, daily, s)) for s in symbols}
            mask_cache[key] = m
        return mask_cache[key]

    start = WARMUP
    cash, positions = args.seed_usdt, {}
    all_tr, all_eq, log = [], [], []
    w = 0
    while start + args.train + args.test <= len(dates):
        tr_a, tr_b = start, start + args.train
        te_a, te_b = tr_b, tr_b + args.test
        best, best_p = -9e9, None
        for dr, pos, sp, rg in GRID:
            m = get_masks(dr, pos, sp)
            t, e, _, _ = simulate(symbols, daily, pre, dates, tr_a, tr_b, m, regs[rg],
                                  cash=1000.0, positions={}, idx=idx,
                                  last_day=last_day, drought_arr=drought_arr)
            sc, _ = score(t, e, 1000.0)
            if sc > best:
                best, best_p = sc, (dr, pos, sp, rg)
        dr, pos, sp, rg = best_p
        m = get_masks(dr, pos, sp)
        t, e, positions, cash = simulate(symbols, daily, pre, dates, te_a, te_b, m, regs[rg],
                                         cash=cash, positions=positions, idx=idx,
                                         last_day=last_day, drought_arr=drought_arr)
        w += 1
        seg_ret = (e["equity"].iloc[-1]/e["equity"].iloc[0]-1)*100 if len(e) else 0
        log.append({"win": w, "test_from": pd.Timestamp(dates[te_a]).date(),
                    "to": pd.Timestamp(dates[te_b-1]).date(), "params": f"{dr}/{pos:.2f}/{rg}",
                    "trades": len(t), "ret": seg_ret, "equity": e["equity"].iloc[-1] if len(e) else cash})
        if not args.quiet:
            print(f"  [{w}] {log[-1]['test_from']}~{log[-1]['to']}  "
                  f"공백{dr}일/하위{pos:.0%}/{rg:10s} | 거래{len(t):3d}건 {seg_ret:+7.1f}% "
                  f"→ 자산 {log[-1]['equity']:,.0f}", flush=True)
        all_tr.append(t); all_eq.append(e)
        start += args.test

    tr = pd.concat(all_tr, ignore_index=True) if all_tr else pd.DataFrame()
    eq = pd.concat(all_eq, ignore_index=True) if all_eq else pd.DataFrame()
    if len(eq):
        peak = eq["equity"].cummax(); mdd = float(((eq["equity"]-peak)/peak).min())*100
        wins = tr[tr.pnl > 0]; loss = tr[tr.pnl <= 0]
        gp = float(wins.pnl.sum()); gl = float(-loss.pnl.sum())
        days = len(eq)
        print(f"\n=== 워크포워드 종합 ({days}일, 검증 구간만 이어붙임) ===")
        print(f"  트레이드      : {len(tr)}")
        print(f"  총수익        : {(eq['equity'].iloc[-1]/args.seed_usdt-1)*100:+.2f}%  "
              f"(연환산 {((eq['equity'].iloc[-1]/args.seed_usdt)**(365/days)-1)*100:+.1f}%)")
        print(f"  MDD           : {mdd:.2f}%")
        print(f"  승률          : {len(wins)/len(tr)*100:.1f}%")
        print(f"  Profit Factor : {gp/gl if gl>0 else float('inf'):.2f}")
        print(f"  수익 구간     : {sum(1 for x in log if x['ret']>0)}/{len(log)}")
        if len(tr):
            print(f"  평균 보유     : {tr['hold_days'].mean():.0f}일  "
                  f"→ 평균 동시보유 {len(tr)*tr['hold_days'].mean()/len(eq):.2f}종목 "
                  f"(칸 {S.MAX_CONCURRENT}개 중)")
        # 차트 모양 탐색은 2024년 이전 데이터로만 했다. 그 이후는 오염되지 않은 구간이다.
        CLEAN = np.datetime64("2024-01-01")
        pre_log = [x for x in log if np.datetime64(str(x["test_from"])) < CLEAN]
        post_log = [x for x in log if np.datetime64(str(x["test_from"])) >= CLEAN]
        for nm, lg in (("2024년 이전(탐색에 사용)", pre_log), ("2024년 이후(미사용·청정)", post_log)):
            if not lg:
                continue
            mult = 1.0
            for x in lg:
                mult *= (1 + x["ret"]/100)
            print(f"  {nm:<22}: {len(lg):2d}구간  누적 {(mult-1)*100:+7.1f}%  "
                  f"수익구간 {sum(1 for x in lg if x['ret']>0)}/{len(lg)}  "
                  f"거래 {sum(x['trades'] for x in lg)}건")
        vc = tr.groupby("symbol")["pnl"].agg(["count", "sum"])
        prof = vc[vc["sum"] > 0]
        print(f"\n  거래된 심볼 {tr['symbol'].nunique()}종 / 유니버스 {len(symbols)}종")
        print(f"  수익 심볼 {len(prof)} / 손실 심볼 {int((vc['sum'] <= 0).sum())}")
        top = prof.nlargest(5, "sum")
        print(f"  수익 상위 5종이 전체 이익에서 차지: {top['sum'].sum()/prof['sum'].sum()*100:.0f}%  "
              f"({', '.join(top.index)})")
        print("\n  청산 사유별:")
        for r, g in tr.groupby("reason"):
            print(f"    {r:10s} {len(g):4d}건  합계 {g.pnl.sum():+9.1f}  "
                  f"평균ROI {g.roi.mean()*100:+7.1f}%  최악 {g.roi.min()*100:+7.1f}%")
        print(f"\n  손실 상위 5건: " + ", ".join(
            f"{x.symbol}({x.roi*100:.0f}%,{x.reason})" for x in tr.nsmallest(5, "pnl").itertuples()))
