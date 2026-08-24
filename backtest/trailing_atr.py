"""
trailing_atr 계열 탐색 — 진입 조건까지 포함해서 넓게.

"어떤 전략이든 진입 타이밍·손절·익절을 어떻게 잡느냐에 따라 결과가 천차만별이다."
그래서 아래를 전부 설정으로 뺐다.

  tf        15분봉의 배수 (1=15m, 4=1h, 16=4h)  — 회전율이 수수료를 결정한다
  ema_f/s   추세 판정 EMA 쌍
  slope     EMA 기울기 조건 봉 수 (0이면 조건 끔)
  entry     rsi_dip  상승추세 + RSI 낮을 때 (눌림 매수)   ← 원본
            rsi_mom  상승추세 + RSI 높을 때 (돌파 추종)
            stoch_dip / stoch_mom   StochRSI K 기준       ← 문서판(v3)
            none     추세만 보고 진입
  sl_mult   손절 = 진입가 ∓ ATR × 이 값
  ptp       부분 익절 도달 가격 변화율 (50% 청산 후 트레일링)
  tr_mult   트레일링 = 최고가 ∓ ATR × 이 값
  lev       레버리지,  side  both|long

회계(STRATEGY_RULES 3.3): 진입 봉에서 청산 안 봄. 한 봉에 손절·익절 겹치면 손절 먼저.
"""
import os, sys, glob, argparse, itertools, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import data as dataio

FEE = 0.0005            # 선물 테이커
SLIP = 0.0003
FUND_PER_DAY = 0.0003
MMR = 0.005
BAR_MS = 900_000
WARM = 250

DEFAULT = dict(tf=1, ema_f=20, ema_s=120, slope=3, entry="rsi_dip",
               thr_long=55, thr_short=45, sl_mult=2.0, tr_mult=2.5, ptp=0.05,
               lev=3, size_pct=0.10, side="both", max_conc=7,
               src="fut15m",       # fut15m: 선물 15분봉(+리샘플) | spot1d: 현물 일봉 8년
               bar_days=None,      # None 이면 src/tf 에서 자동
               regime=0,           # BTC N일선 위에서만 신규 진입 (0이면 끔)
               stop_cap=None,      # 손절 최대 폭 (예: 0.20 → -20% 보다 더 내려가진 않는다)
               breadth=0)          # 전체 종목 중 100일선 위 비율이 이 값 이상인 날만 (0이면 끔)


# ────────────────────────── 데이터 ──────────────────────────
_RAW = None


def load_all(min_bars=20000):
    global _RAW
    if _RAW is not None:
        return _RAW
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
        out[base.replace("_15m.csv", "").replace("_USDT-USDT", "/USDT:USDT")] = d
    _RAW = out
    return out


_SPOT = None


def load_spot_daily(min_bars=500):
    """현물 일봉 8년 (HIBERNATE 가 쓰는 것과 같은 데이터).

    선물 15분봉은 4년/95종뿐이라 일봉 전략을 검증하기엔 얇다.
    같은 전략을 8년/733종에서 다시 돌려보면, 탐색에 쓰지 않은 데이터로 확인하는 셈이 된다.
    """
    global _SPOT
    if _SPOT is not None:
        return _SPOT
    out = {}
    for p in sorted(glob.glob(os.path.join(dataio.CACHE_DIR, "spot_*_1d.csv"))):
        try:
            d = pd.read_csv(p)
        except Exception:
            continue
        if len(d) < min_bars:
            continue
        d = d.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        sym = os.path.basename(p)[len("spot_"):-len("_1d.csv")].replace("_", "/")
        out[sym] = d
    _SPOT = out
    return out


def resample(d, tf):
    if tf == 1:
        return d
    # 하루(96봉) 이상으로 묶을 때는 UTC 00:00 에 맞춘다.
    # 안 맞추면 레짐 필터(일 단위 키)와 어긋나서 신호가 통째로 사라진다.
    if tf >= 96:
        ts0 = int(d["timestamp"].iloc[0])
        off = (-(ts0 // 900_000)) % tf
        d = d.iloc[off:].reset_index(drop=True)
    n = len(d) // tf * tf
    d = d.iloc[:n]
    g = np.arange(n) // tf
    return pd.DataFrame({
        "timestamp": d["timestamp"].values[::tf],
        "open": d["open"].values[::tf],
        "high": pd.Series(d["high"].values).groupby(g).max().values,
        "low": pd.Series(d["low"].values).groupby(g).min().values,
        "close": pd.Series(d["close"].values).groupby(g).last().values,
        "volume": pd.Series(d["volume"].values).groupby(g).sum().values,
    })


def _stoch_rsi(c, p=14, k=3, dd=3):
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    lo = rsi.rolling(p).min(); hi = rsi.rolling(p).max()
    st = (rsi - lo) / (hi - lo).replace(0, np.nan) * 100
    return rsi, st.rolling(k).mean()


_PRE = {}


def precompute(sym, raw, cfg):
    key = (sym, cfg["src"], cfg["tf"], cfg["ema_f"], cfg["ema_s"], cfg["slope"])
    if key in _PRE:
        return _PRE[key]
    d = raw if cfg["src"] == "spot1d" else resample(raw, cfg["tf"])
    c = d["close"].astype(float); h = d["high"].astype(float); l = d["low"].astype(float)
    ef = c.ewm(span=cfg["ema_f"], adjust=False).mean()
    es = c.ewm(span=cfg["ema_s"], adjust=False).mean()
    if cfg["slope"]:
        sp = cfg["slope"]
        sf = ef - ef.shift(sp); ss = es - es.shift(sp)
        up_t = (c > ef) & (c > es) & (sf > 0) & (ss > 0)
        dn_t = (c < ef) & (c < es) & (sf < 0) & (ss < 0)
    else:
        up_t = (c > ef) & (c > es)
        dn_t = (c < ef) & (c < es)
    rsi, stk = _stoch_rsi(c)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()
    v = dict(c=c.values, h=h.values, l=l.values, ts=d["timestamp"].values.astype("int64"),
             up=up_t.values, dn=dn_t.values,
             rsi=rsi.fillna(50).values, stk=stk.fillna(50).values,
             atr=np.where(atr.values > 0, atr.values, c.values * 0.01))
    _PRE[key] = v
    return v


_REG = {}


_BR = {}


def breadth_regime(pct, src, days=100):
    """전체 종목 중 100일선 위인 비율이 pct 이상인 날만 신규 진입.

    BTC 는 100일선 위인데 알트는 죽어 있던 시기(2024~2025)가 실제로 있었다.
    추세 전략은 알트가 살아 있어야 되므로, BTC 하나가 아니라 시장 폭을 본다.
    """
    key = (pct, src, days)
    if key in _BR:
        return _BR[key]
    raw = load_spot_daily() if src == "spot1d" else load_all()
    acc = {}
    for sym, d0 in raw.items():
        d = d0 if src == "spot1d" else resample(d0, 96)
        if len(d) < days + 5:
            continue
        c = d["close"].astype(float)
        up = (c > c.rolling(days).mean()).values
        ts = d["timestamp"].values.astype("int64")
        for k in range(days, len(d)):
            day = int(ts[k]) - (int(ts[k]) % 86_400_000)
            a = acc.setdefault(day, [0, 0])
            a[0] += int(up[k]); a[1] += 1
    out = {day: (a[0] / a[1] >= pct and a[1] >= 20) for day, a in acc.items()}
    _BR[key] = out
    return out


def btc_regime(days, src):
    """BTC 가 N일선 위인가. 신규 진입만 막고 보유는 그대로 둔다.

    HIBERNATE 에서 이 필터가 결정적이었다. 여기서도 되는지 본다.
    성적을 보고 고르는 게 아니라 켜고/끄고 두 경우를 다 보고한다 (규칙).
    """
    key = (days, src)
    if key in _REG:
        return _REG[key]
    if src == "spot1d":
        raw = load_spot_daily().get("BTC/USDT")
        d = raw
    else:
        raw = load_all().get("BTC/USDT:USDT")
        d = resample(raw, 96) if raw is not None else None
    if d is None:
        _REG[key] = {}
        return {}
    c = d["close"].astype(float)
    ma = c.rolling(days).mean()
    _REG[key] = dict(zip(d["timestamp"].values.astype("int64"), (c > ma).values))
    return _REG[key]


def build(cfg, syms=None):
    raw = load_spot_daily() if cfg.get("src") == "spot1d" else load_all()
    syms = syms or list(raw)
    pre = {s: precompute(s, raw[s], cfg) for s in syms}
    order = []
    for s, P in pre.items():
        order.extend(zip(P["ts"].tolist(), [s] * len(P["ts"]), range(len(P["ts"]))))
    order.sort()
    return pre, order


# ────────────────────────── 시뮬 ──────────────────────────
class Pos:
    __slots__ = ("side", "qty", "entry", "margin", "sl", "best", "partial", "trail", "open_i")


def _signal(P, i, cfg):
    e = cfg["entry"]
    if e == "none":
        return P["up"][i], P["dn"][i]
    if e.startswith("rsi"):
        x = P["rsi"][i]
    else:
        x = P["stk"][i]
    lo_, hi_ = cfg["thr_long"], cfg["thr_short"]
    if e.endswith("dip"):
        return P["up"][i] and x < lo_, P["dn"][i] and x > hi_
    return P["up"][i] and x > lo_, P["dn"][i] and x < hi_


def simulate(pre, order, a_ms, b_ms, cash, cfg):
    lev, spct = cfg["lev"], cfg["size_pct"]
    sl_m, tr_m, ptp = cfg["sl_mult"], cfg["tr_mult"], cfg["ptp"]
    allow_short = cfg["side"] == "both"
    maxc = cfg["max_conc"]
    if cfg.get("bar_days"):
        bar_days = cfg["bar_days"]
    elif cfg.get("src") == "spot1d":
        bar_days = 1.0
    else:
        bar_days = cfg["tf"] * 15 / 1440.0
    # 현물은 펀딩비가 없고 레버리지도 못 쓴다
    fund_bar = 0.0 if cfg.get("src") == "spot1d" else FUND_PER_DAY * bar_days
    if cfg.get("breadth"):
        reg = breadth_regime(cfg["breadth"], cfg.get("src", "fut15m"))
    elif cfg.get("regime"):
        reg = btc_regime(cfg["regime"], cfg.get("src", "fut15m"))
    else:
        reg = None
    reg_day = None
    pos, trades = {}, []
    last_i = {}
    peak = cash
    mdd = 0.0

    for ts, sym, i in order:
        if ts < a_ms or ts >= b_ms:
            continue
        P = pre[sym]
        last_i[sym] = i
        h, l, c, atr = P["h"][i], P["l"][i], P["c"][i], P["atr"][i]
        p = pos.get(sym)
        if p is not None:
            if i <= p.open_i:
                continue
            sign = 1 if p.side == "long" else -1
            p.margin -= p.margin * fund_bar * lev
            if p.trail:
                p.best = max(p.best, h) if sign > 0 else min(p.best, l)
            hit = px = None
            if (sign > 0 and l <= p.sl) or (sign < 0 and h >= p.sl):
                hit, px = ("STOP" if not p.partial else "BREAKEVEN"), p.sl
            elif not p.partial:
                tgt = p.entry * (1 + ptp * sign)
                if (sign > 0 and h >= tgt) or (sign < 0 and l <= tgt):
                    q = p.qty * 0.5
                    fill = tgt * (1 - SLIP * sign)
                    pnl = q * (fill - p.entry) * sign - q * fill * FEE
                    cash += p.margin * 0.5 + pnl
                    trades.append(dict(sym=sym, side=p.side, reason="PARTIAL", ts=ts,
                                       margin=p.margin * 0.5, pnl=pnl, roi=pnl / (p.margin * 0.5)))
                    p.qty -= q; p.margin *= 0.5
                    p.partial = True; p.trail = True; p.sl = p.entry
                    p.best = h if sign > 0 else l
                    continue
            if hit is None and p.trail:
                t = p.best - atr * tr_m if sign > 0 else p.best + atr * tr_m
                if (sign > 0 and l <= t) or (sign < 0 and h >= t):
                    hit, px = "TRAIL", t
            if hit:
                fill = px * (1 - SLIP * sign)
                pnl = max((fill - p.entry) * sign * p.qty - p.qty * fill * FEE, -p.margin)
                cash += p.margin + pnl
                trades.append(dict(sym=sym, side=p.side, reason=hit, ts=ts,
                                   margin=p.margin, pnl=pnl, roi=pnl / p.margin))
                del pos[sym]
                eq = cash + sum(x.margin for x in pos.values())
                peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
            continue

        if len(pos) >= maxc or i < WARM:
            continue
        if reg is not None:
            day = ts - (ts % 86_400_000)
            if not reg.get(day, False):
                continue
        lo_ok, sh_ok = _signal(P, i, cfg)
        if not (lo_ok or (allow_short and sh_ok)):
            continue
        eq = cash + sum(q.margin for q in pos.values())
        m = eq * spct
        if cash < m or m <= 0:
            continue
        side = "long" if lo_ok else "short"
        sign = 1 if side == "long" else -1
        fill = c * (1 + SLIP * sign)
        q = m * lev / fill
        cash -= m + q * fill * FEE
        n = Pos()
        n.side, n.qty, n.entry, n.margin = side, q, fill, m
        n.sl = fill - atr * sl_m * sign
        # ATR 비례 손절은 조용한 종목에서 너무 멀어진다. 최대 폭을 씌운다.
        # (HIBERNATE 에서 확인된 것: 목표는 종목별로, 손절은 고정이 맞다)
        cap = cfg.get("stop_cap")
        if cap:
            hard = fill * (1 - cap * sign)
            n.sl = max(n.sl, hard) if sign > 0 else min(n.sl, hard)
        liq = fill * (1 - sign * (1 / lev - MMR))
        n.sl = max(n.sl, liq) if sign > 0 else min(n.sl, liq)
        n.best, n.partial, n.trail, n.open_i = fill, False, False, i
        pos[sym] = n

    # 구간 끝에 남은 포지션을 원가로 계산하면 손실이 숨는다.
    # 마지막 가격으로 평가해서 청산한 것으로 친다.
    for sym, p in pos.items():
        P = pre[sym]
        j = last_i.get(sym)
        if j is None:
            cash += p.margin
            continue
        sign = 1 if p.side == "long" else -1
        fill = P["c"][j] * (1 - SLIP * sign)
        pnl = max((fill - p.entry) * sign * p.qty - p.qty * fill * FEE, -p.margin)
        cash += p.margin + pnl
        trades.append(dict(sym=sym, side=p.side, reason="OPEN_END", ts=int(P["ts"][j]),
                           margin=p.margin, pnl=pnl, roi=pnl / p.margin))
    eq = cash
    peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
    return pd.DataFrame(trades), eq, mdd


def stats(tr, eq_end, seed, mdd):
    if not len(tr):
        return dict(n=0, ret=eq_end / seed - 1, win=np.nan, pf=np.nan, mdd=mdd)
    fin = tr[tr.reason.isin(["STOP", "TRAIL", "BREAKEVEN", "OPEN_END"])]
    npos = max(len(fin), 1)
    w = tr[tr.pnl > 0]; lo = tr[tr.pnl <= 0]
    pf = w.pnl.sum() / abs(lo.pnl.sum()) if len(lo) and lo.pnl.sum() != 0 else float("inf")
    return dict(n=npos, ret=eq_end / seed - 1, win=(tr.reason == "PARTIAL").sum() / npos,
                pf=pf, mdd=mdd)


# ────────────────────────── 연속 검증 ──────────────────────────
def continuous(cfg, train=365, test=90, seed=1000.0, quiet=True):
    """설정이 고정이면 학습 구간이 필요 없다. 검증 시작일부터 끝까지 한 번에 돌린다.

    구간을 잘라 이어붙이면 90일마다 보유 포지션이 강제 청산돼서
    오래 끌고 가는 자리(트레일링)가 불리하게 잡힌다. 그 왜곡을 없앤다.
    구간별 성적은 자산곡선을 90일로 잘라서 따로 낸다.
    """
    pre, order = build(cfg)
    t0, t1 = order[0][0], order[-1][0]
    D = 86_400_000
    start = t0 + train * D
    tr, eq, mdd = simulate(pre, order, start, t1 + 1, seed, cfg)
    s = stats(tr, eq, seed, mdd)
    # 90일 구간별 손익
    wins = cur = 0
    if len(tr):
        tr = tr.sort_values("ts")
        a = start
        while a < t1:
            g = tr[(tr.ts >= a) & (tr.ts < a + test * D)]
            if len(g):
                cur += 1
                wins += g.pnl.sum() > 0
            a += test * D
    s.update(win_windows=wins, windows=cur, trades=tr)
    return s


# ────────────────────────── 워크포워드 ──────────────────────────
def walkforward(cfg, grid, train=365, test=90, seed=1000.0, quiet=True):
    pre, order = build(cfg)
    t0, t1 = order[0][0], order[-1][0]
    D = 86_400_000
    cash, wins, cur, alltr = seed, 0, 0, []
    peak, mdd = seed, 0.0
    a = t0 + train * D
    picks = []
    while a + test * D <= t1:
        best, bp = None, (grid[0] if grid else cfg)
        if grid and len(grid) > 1:
            for g in grid:
                _, e, _ = simulate(pre, order, a - train * D, a, seed, g)
                if best is None or e > best:
                    best, bp = e, g
        tr, e, _ = simulate(pre, order, a, a + test * D, cash, bp)
        ret = e / cash - 1
        cash = e
        peak = max(peak, cash); mdd = min(mdd, cash / peak - 1)
        wins += ret > 0; cur += 1
        alltr.append(tr)
        picks.append(bp)
        if not quiet:
            tag = f"{bp['entry']} tf{bp['tf']} SL{bp['sl_mult']} TR{bp['tr_mult']} TP{bp['ptp']*100:.0f}%"
            print(f"  {pd.to_datetime(a,unit='ms').date()}  {tag:<38}{len(tr):>6}{ret*100:>+8.1f}%"
                  f"{(cash/seed-1)*100:>+9.1f}%")
        a += test * D
    tr = pd.concat(alltr, ignore_index=True) if alltr else pd.DataFrame()
    s = stats(tr, cash, seed, mdd)
    s.update(win_windows=wins, windows=cur, picks=picks, trades=tr)
    return s
