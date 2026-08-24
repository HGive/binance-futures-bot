"""
trailing_atr 워크포워드 검증 (15분봉 선물, 롱/숏).

전략(strategies/trailing_atr.py 그대로):
  추세   가격이 EMA20·EMA120 위 + 두 EMA 기울기(3봉) 모두 양수  → 상승추세
  진입   상승추세인데 RSI < 55  (= 추세 안 눌림 매수).  숏은 반대
  손절   진입가 ± ATR × 2.0
  부분익절 가격 ±5% 도달 → 50% 청산, 손절을 본전으로, 트레일링 시작
  트레일링 최고가 ∓ ATR × 2.5
  레버리지 3배, 티커당 잔고 10%

회계(STRATEGY_RULES 3.3):
  진입 봉에서는 청산을 보지 않는다. 한 봉에서 손절·익절이 겹치면 손절 먼저.
  수수료 시장가 0.05% 왕복, 슬리피지 0.03%, 펀딩 0.01%/8시간.
"""
import os, sys, glob, argparse, itertools
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import data as dataio
import strategies.trailing_atr as T

FEE = 0.0005            # 선물 테이커
SLIP = 0.0003
FUND_PER_BAR = 0.0001 / 32      # 8시간=32봉 당 0.01%
MMR = 0.005
BAR_MS = 900_000
WARM = 200


def load_all(min_bars=20000):
    out = {}
    for p in sorted(glob.glob(os.path.join(dataio.CACHE_DIR, "*_15m.csv"))):
        base = os.path.basename(p)
        if base.startswith("spot_"):
            continue
        try:
            d = pd.read_csv(p)
        except Exception:
            continue
        if len(d) < min_bars:
            continue
        d = d.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        sym = base.replace("_15m.csv", "").replace("_USDT-USDT", "/USDT:USDT")
        out[sym] = d
    return out


def precompute(d, rsi_p=14, ema_f=20, ema_s=120, atr_p=14, slope=3):
    c = d["close"].astype(float)
    h = d["high"].astype(float)
    l = d["low"].astype(float)
    ef = c.ewm(span=ema_f, adjust=False).mean()
    es = c.ewm(span=ema_s, adjust=False).mean()
    sf = (ef - ef.shift(slope)) / ef.shift(slope)
    ss = (es - es.shift(slope)) / es.shift(slope)
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1/rsi_p, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1/rsi_p, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=atr_p, adjust=False).mean()
    return dict(
        c=c.values, h=h.values, l=l.values, o=d["open"].astype(float).values,
        ts=d["timestamp"].values.astype("int64"),
        up=((c > ef) & (c > es) & (sf > 0) & (ss > 0)).values,
        dn=((c < ef) & (c < es) & (sf < 0) & (ss < 0)).values,
        rsi=rsi.fillna(50).values,
        atr=np.where(atr.values > 0, atr.values, c.values * 0.01),
    )


class P:
    __slots__ = ("sym", "side", "qty", "entry", "margin", "sl", "best",
                 "partial", "trail", "open_i", "open_ts")


def simulate(pre, order, a_ms, b_ms, cash, params, allow_short=True, max_conc=7):
    """order: (ts, sym, i) 전체 타임라인. a_ms~b_ms 구간만 거래."""
    rsi_long, rsi_short = params["rsi_long"], params["rsi_short"]
    sl_mult, tr_mult, ptp = params["sl_mult"], params["tr_mult"], params["ptp"]
    lev, size_pct = params["lev"], params["size_pct"]
    pos, trades, curve = {}, [], []
    last_eq_ts = 0

    for ts, sym, i in order:
        if ts < a_ms or ts >= b_ms:
            continue
        P_ = pre[sym]
        c, h, l = P_["c"][i], P_["h"][i], P_["l"][i]
        atr = P_["atr"][i]

        p = pos.get(sym)
        if p is not None:
            if i <= p.open_i:                       # 진입 봉에서는 청산 안 봄
                continue
            sign = 1 if p.side == "long" else -1
            p.margin -= p.margin * FUND_PER_BAR * lev
            if p.trail:
                p.best = max(p.best, h) if sign > 0 else min(p.best, l)

            hit = px = None
            # 1) 손절 (한 봉에 겹치면 손절 먼저)
            if (sign > 0 and l <= p.sl) or (sign < 0 and h >= p.sl):
                hit, px = ("STOP" if not p.partial else "BREAKEVEN"), p.sl
            # 2) 부분 익절
            elif not p.partial:
                tgt = p.entry * (1 + ptp * sign)
                if (sign > 0 and h >= tgt) or (sign < 0 and l <= tgt):
                    q = p.qty * 0.5
                    fill = tgt * (1 - SLIP * sign)
                    pnl = q * (fill - p.entry) * sign - q * fill * FEE
                    cash += p.margin * 0.5 + pnl
                    trades.append(dict(sym=sym, side=p.side, reason="PARTIAL", ts=ts,
                                       margin=p.margin * 0.5, pnl=pnl,
                                       roi=pnl / (p.margin * 0.5),
                                       bars=i - p.open_i))
                    p.qty -= q
                    p.margin *= 0.5
                    p.partial = True
                    p.trail = True
                    p.sl = p.entry                  # 본전으로
                    p.best = h if sign > 0 else l
                    continue
            # 3) 트레일링
            if hit is None and p.trail:
                t = p.best - atr * tr_mult if sign > 0 else p.best + atr * tr_mult
                if (sign > 0 and l <= t) or (sign < 0 and h >= t):
                    hit, px = "TRAIL", t
            if hit:
                fill = px * (1 - SLIP * sign)
                pnl = max((fill - p.entry) * sign * p.qty - p.qty * fill * FEE, -p.margin)
                cash += p.margin + pnl
                trades.append(dict(sym=sym, side=p.side, reason=hit, ts=ts,
                                   margin=p.margin, pnl=pnl, roi=pnl / p.margin,
                                   bars=i - p.open_i))
                del pos[sym]
            continue

        # --- 신규 진입 ---
        if len(pos) >= max_conc or i < WARM:
            continue
        long_ok = P_["up"][i] and P_["rsi"][i] < rsi_long
        short_ok = allow_short and P_["dn"][i] and P_["rsi"][i] > rsi_short
        if not (long_ok or short_ok):
            continue
        eq = cash + sum(q.margin for q in pos.values())
        m = eq * size_pct
        if cash < m or m <= 0:
            continue
        side = "long" if long_ok else "short"
        sign = 1 if side == "long" else -1
        fill = c * (1 + SLIP * sign)
        q = m * lev / fill
        cash -= m + q * fill * FEE
        n = P()
        n.sym, n.side, n.qty, n.entry, n.margin = sym, side, q, fill, m
        n.sl = fill - atr * sl_mult * sign
        # 청산가보다 먼 손절은 의미가 없다
        liq = fill * (1 - sign * (1 / lev - MMR))
        n.sl = max(n.sl, liq) if sign > 0 else min(n.sl, liq)
        n.best, n.partial, n.trail = fill, False, False
        n.open_i, n.open_ts = i, ts
        pos[sym] = n

        if ts - last_eq_ts > 86_400_000:
            curve.append((ts, cash + sum(x.margin for x in pos.values())))
            last_eq_ts = ts

    eq = cash + sum(x.margin for x in pos.values())
    return pd.DataFrame(trades), pd.DataFrame(curve, columns=["ts", "equity"]), eq


def summarize(tr, eq_end, seed, label):
    if not len(tr):
        print(f"{label}: 거래 없음")
        return None
    # 부분익절은 한 포지션을 두 줄로 나눈다. 그대로 세면 승률이 부풀려진다.
    # 포지션 단위 = 최종 청산(STOP/TRAIL/BREAKEVEN) 줄의 수.
    fin = tr[tr.reason.isin(["STOP", "TRAIL", "BREAKEVEN"])]
    n_pos = len(fin)
    n_part = int((tr.reason == "PARTIAL").sum())
    if n_pos:
        print(f"    포지션 {n_pos}건 중 익절 도달 {n_part}건 = 포지션 승률 {n_part/n_pos*100:.1f}%")
    w = tr[tr.pnl > 0]; lo = tr[tr.pnl <= 0]
    pf = w.pnl.sum() / abs(lo.pnl.sum()) if len(lo) and lo.pnl.sum() != 0 else float("inf")
    print(f"{label}: 거래 {len(tr)}건  총수익 {(eq_end/seed-1)*100:+.1f}%  "
          f"승률 {(tr.pnl>0).mean()*100:.1f}%  PF {pf:.2f}  평균ROI {tr.roi.mean()*100:+.1f}%")
    return dict(n=len(tr), ret=eq_end/seed - 1, win=(tr.pnl > 0).mean(), pf=pf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=int, default=365)
    ap.add_argument("--test", type=int, default=90)
    ap.add_argument("--seed", type=float, default=1000.0)
    ap.add_argument("--max-conc", type=int, default=7)
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--fixed", action="store_true", help="학습 없이 현재 상수 그대로")
    args = ap.parse_args()

    daily = load_all()
    print(f"15분봉 {len(daily)}종 로드")
    pre = {s: precompute(d) for s, d in daily.items()}
    order = []
    for s, P_ in pre.items():
        order.extend(zip(P_["ts"].tolist(), [s] * len(P_["ts"]), range(len(P_["ts"]))))
    order.sort()
    t0, t1 = order[0][0], order[-1][0]
    print(f"기간 {pd.to_datetime(t0,unit='ms').date()} ~ {pd.to_datetime(t1,unit='ms').date()}")

    base = dict(rsi_long=T.RSI_LONG_THRESHOLD, rsi_short=T.RSI_SHORT_THRESHOLD,
                sl_mult=T.INITIAL_SL_ATR_MULT, tr_mult=T.TRAILING_STOP_ATR_MULT,
                ptp=T.PARTIAL_TP_PCT, lev=T.LEVERAGE, size_pct=T.POSITION_SIZE_PCT)
    grid = [base] if args.fixed else [
        dict(base, sl_mult=a, tr_mult=b, ptp=c)
        for a in (1.5, 2.0, 3.0) for b in (2.0, 2.5, 3.5) for c in (0.03, 0.05, 0.08)]

    D = 86_400_000
    cash = args.seed
    all_tr, wins, cur = [], 0, 0
    print(f"\n{'구간':<26}{'선택':<22}{'거래':>6}{'수익':>9}{'누적':>10}")
    print("-" * 76)
    a = t0 + args.train * D
    while a + args.test * D <= t1:
        best, bp = None, grid[0]
        if len(grid) > 1:
            for g in grid:
                tr, _, e = simulate(pre, order, a - args.train * D, a, args.seed, g,
                                    not args.long_only, args.max_conc)
                sc = e / args.seed - 1
                if best is None or sc > best:
                    best, bp = sc, g
        tr, _, e = simulate(pre, order, a, a + args.test * D, cash, bp,
                            not args.long_only, args.max_conc)
        ret = e / cash - 1
        cash = e
        cur += 1
        wins += ret > 0
        all_tr.append(tr)
        tag = f"SL{bp['sl_mult']} TR{bp['tr_mult']} TP{bp['ptp']*100:.0f}%"
        print(f"{str(pd.to_datetime(a,unit='ms').date())} ~ "
              f"{str(pd.to_datetime(a+args.test*D,unit='ms').date()):<12}{tag:<22}"
              f"{len(tr):>6}{ret*100:>+8.1f}%{(cash/args.seed-1)*100:>+9.1f}%")
        a += args.test * D

    tr = pd.concat(all_tr, ignore_index=True) if all_tr else pd.DataFrame()
    print()
    r = summarize(tr, cash, args.seed, "=== 워크포워드 종합")
    if r:
        print(f"    수익 구간 {wins}/{cur}")
        print("    청산 사유별:")
        for why, g in tr.groupby("reason"):
            print(f"      {why:<10} {len(g):>5}건  평균ROI {g.roi.mean()*100:>+6.1f}%  합계 {g.pnl.sum():>+9.1f}")
        for side, g in tr.groupby("side"):
            print(f"      {side:<10} {len(g):>5}건  승률 {(g.pnl>0).mean()*100:.1f}%  합계 {g.pnl.sum():>+9.1f}")


if __name__ == "__main__":
    main()
