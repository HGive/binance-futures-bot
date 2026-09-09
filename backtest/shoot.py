"""SHOOT 탐색 — 급등 초입을 타서 트레일링으로 끌고 간다. 1분봉 선물, 롱 전용.

────────────────────────────────────────────────────────────
왜 이걸 다시 보는가 (예전에 진 실험과 무엇이 다른가)
────────────────────────────────────────────────────────────
strategies/rebound.py 에 "급등 추종은 이미 시험해서 졌다" 고 적혀 있다.
그 시험은 **고정 익절**을 걸고 쟀다. 그게 결함이었다.

2026-09-07 측정 (docs/):
  일봉 8년 629종, 종목×월 21,474 관측
    전체 평균                -0.08%
    상위 5% 를 빼면          -6.96%
  → 알트 수익의 전부가 우측 꼬리에 있다. 고정 익절은 그 꼬리를 잘라낸다.

같은 데이터에서 REBOUND(급락 받기)의 익절을 떼자 PF 0.78 → 1.32 로 뒤집혔다.
그러므로 "급등 추종 + 고정 익절" 이 졌다는 것은
"급등 추종 + 익절 없음 + 트레일링" 에 대해 아무것도 말해주지 않는다.

이 파일은 그 미시험 조합을 잰다.

────────────────────────────────────────────────────────────
설계 — 사용자가 말한 그대로
────────────────────────────────────────────────────────────
  "급등하기 직전 신호를 포착해서 들어간다.
   손절라인은 진입 시점 살짝 아래로만 잡고,
   급등하면 트레일링이니까 따라가다가 반대로 가면 나온다."

진입 (셋 중 하나)
  chase     거래량 급증 + 이미 +X% 오르기 시작함        → 초입 추종
  pre       거래량 급증인데 가격은 아직 조용함          → 선행 감지
  breakout  N분 최고가 돌파 + 거래량 급증               → 돌파 추종

청산 (고정 익절 없음)
  초기 손절   진입가 -S%
  트레일링    최고가 대비 -T%. 단 +A% 이상 오른 뒤부터 작동
  시간 상한   H분 지나면 종가 청산

────────────────────────────────────────────────────────────
정직하게 지킨 것
────────────────────────────────────────────────────────────
1. **추격은 테이커다.** 급등을 쫓아 들어가면서 지정가가 체결되길 기다릴 수 없다.
   진입 0.05% + 청산 0.05% = 왕복 0.10%. REBOUND(메이커 진입 0.02%)보다 두 배 비싸다.
   이걸 메이커로 모델링하면 거짓말이 된다.
2. 규칙 3.3 — 신호봉에서 사고팔지 않는다. 신호는 i 봉 종가로 판단하고 i+1 봉 시가에 산다.
3. 규칙 3.10 — 진입한 봉에서도 손절은 검사한다. 손절은 거래소에 걸린 주문이다.
4. 갭 반영 — 손절/트레일 체결가는 방아쇠가 아니라 min(방아쇠, 그 봉 시가) 다.
5. 대조군 — 같은 빈도로 무작위 시점에 진입해 같은 출구를 쓴다 (규칙 5).

    .venv/bin/python backtest/shoot.py --mode grid
"""
import os
import sys
import glob
import argparse
import itertools

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import data as dataio  # noqa: E402

FEE_TAKER = 0.0005          # 선물 테이커 0.05%
SLIPPAGE = 0.0002           # 급등 추격이라 넉넉히 잡는다
VOL_MA_BARS = 60            # 거래량 평균 구간 (분)
MIN_QV_MA = 30_000.0        # 이 정도는 돌아야 실제로 살 수 있다 (1분 거래대금)
RET_CAP = 3.0               # 데이터 이상치 상한 (+300%)


def load_1m(min_bars=20000, lookbacks=(1, 3, 5, 10, 15, 30, 60)):
    out = {}
    for path in sorted(glob.glob(os.path.join(dataio.CACHE_DIR, "*_1m.csv"))):
        base = os.path.basename(path)
        if base.startswith("spot_"):
            continue
        df = pd.read_csv(path)
        if len(df) < min_bars:
            continue
        sym = base.replace("_1m.csv", "").replace("_", "/").replace("-", ":")
        c = df["close"].astype(float)
        qv = df["volume"].astype(float) * c
        d = dict(
            o=df["open"].astype(float).values,
            h=df["high"].astype(float).values,
            l=df["low"].astype(float).values,
            c=c.values,
            qv=qv.values,
            qv_ma=qv.rolling(VOL_MA_BARS).mean().values,
            ts=df["timestamp"].values,
        )
        for k in lookbacks:
            d[f"r{k}"] = (c / c.shift(k) - 1).fillna(0).values
            d[f"hh{k}"] = c.rolling(k).max().shift(1).values   # 직전 k봉 최고 종가
        out[sym] = d
    return out


def simulate(data, entry="chase", vol_mult=8.0, mom_bars=5, mom_pct=0.015,
             quiet_pct=0.003, brk_bars=30, stop=0.01, trail=0.02, arm=0.01,
             hold=120, min_qv=MIN_QV_MA, random_at=None):
    """고정 익절 없음. 손절 + 트레일링 + 시간 상한만."""
    rows = []
    for sym, I in data.items():
        n = len(I["c"])
        o, h, l, c = I["o"], I["h"], I["l"], I["c"]
        qv, ma = I["qv"], I["qv_ma"]
        rm = I[f"r{mom_bars}"]
        hh = I[f"hh{brk_bars}"]
        i = 100
        while i < n - hold - 3:
            m = ma[i]
            if not np.isfinite(m) or m < min_qv:
                i += 1
                continue

            vol_hit = qv[i] >= m * vol_mult
            if entry == "chase":
                hit = vol_hit and rm[i] >= mom_pct
            elif entry == "pre":
                # 거래량은 터졌는데 가격이 아직 안 움직였다 = 오르기 직전일 수 있다
                hit = vol_hit and abs(rm[i]) < quiet_pct
            elif entry == "breakout":
                hit = vol_hit and np.isfinite(hh[i]) and c[i] > hh[i]
            else:
                raise ValueError(entry)
            if random_at is not None:
                hit = random_at(sym, i)
            if not hit:
                i += 1
                continue

            # 규칙 3.3 — 신호봉에서 사지 않는다. 다음 봉 시가에 시장가로 산다.
            j0 = i + 1
            px = o[j0] * (1 + SLIPPAGE)
            if not np.isfinite(px) or px <= 0:
                i += 1
                continue

            stop_px = px * (1 - stop)
            arm_px = px * (1 + arm)
            peak = px
            armed = False
            last = min(n - 1, j0 + hold)
            exit_px, why = None, ""

            for j in range(j0, last + 1):
                # 규칙 3.10 — 진입한 봉(j0)에서도 손절은 본다.
                lvl = peak * (1 - trail) if armed else stop_px
                if l[j] <= lvl:
                    exit_px = min(lvl, o[j])        # 갭 반영
                    why = "TRAIL" if armed else "STOP"
                    break
                if h[j] > peak:
                    peak = h[j]
                    if not armed and peak >= arm_px:
                        armed = True
            if exit_px is None:
                exit_px, why = c[last], "TIME"

            ret = exit_px / px - 1 - FEE_TAKER * 2 - SLIPPAGE
            rows.append(dict(sym=sym, ts=I["ts"][i], ret=min(ret, RET_CAP),
                             why=why, bars=min(hold, last - j0)))
            i = last + 1                            # 겹치는 진입 금지
    return rows


def stats(rows):
    if not rows:
        return None
    df = pd.DataFrame(rows)
    r = df["ret"].values
    gain, loss = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=len(r), mean=r.mean(), total=r.sum(),
                pf=(gain / loss) if loss > 0 else float("inf"),
                win=(r > 0).mean(), syms=df["sym"].nunique(),
                sym_win=(df.groupby("sym")["ret"].sum() > 0).mean(),
                trail=(df["why"] == "TRAIL").mean(),
                top10=r.sum() - np.sort(r)[::-1][:10].sum())


def header():
    print(f"{'설정':<40}{'거래':>6}{'평균':>9}{'누적':>10}{'PF':>7}"
          f"{'승률':>7}{'트레일':>7}{'수익종목':>9}{'상위10제외':>11}")
    print("-" * 106)


def line(label, s):
    if not s:
        print(f"{label:<40}  —")
        return
    print(f"{label:<40}{s['n']:>6}{s['mean']*100:>8.3f}%{s['total']*100:>9.1f}%"
          f"{s['pf']:>7.2f}{s['win']*100:>6.0f}%{s['trail']*100:>6.0f}%"
          f"{s['sym_win']*100:>8.0f}%{s['top10']*100:>10.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="base",
                    choices=["base", "grid", "entry", "exit", "control", "split"])
    ap.add_argument("--min-bars", type=int, default=20000)
    a = ap.parse_args()

    data = load_1m(a.min_bars)
    span = [(pd.Timestamp(v["ts"][0], unit="ms"), pd.Timestamp(v["ts"][-1], unit="ms"))
            for v in data.values()]
    print(f"1분봉 {len(data)}종 | {min(s[0] for s in span).date()} "
          f"~ {max(s[1] for s in span).date()}")
    print(f"왕복 비용 {(FEE_TAKER*2+SLIPPAGE)*100:.2f}% (테이커 진입·청산)\n")

    D = dict(vol_mult=8.0, mom_bars=5, mom_pct=0.015, quiet_pct=0.003,
             brk_bars=30, stop=0.01, trail=0.02, arm=0.01, hold=120)

    if a.mode == "base":
        header()
        for e in ("chase", "pre", "breakout"):
            line(f"{e}", stats(simulate(data, entry=e, **D)))

    elif a.mode == "entry":
        header()
        for e in ("chase", "pre", "breakout"):
            for vm in (5.0, 8.0, 15.0):
                line(f"{e} 거래량x{vm:.0f}",
                     stats(simulate(data, entry=e, **{**D, "vol_mult": vm})))
        print()
        for mb, mp in itertools.product((3, 5, 15), (0.01, 0.02, 0.04)):
            line(f"chase {mb}분 +{mp:.0%} 이상",
                 stats(simulate(data, entry="chase",
                                **{**D, "mom_bars": mb, "mom_pct": mp})))

    elif a.mode == "exit":
        header()
        for st, tr in itertools.product((0.005, 0.01, 0.02), (0.01, 0.02, 0.04)):
            line(f"chase 손절-{st:.1%} 트레일-{tr:.1%}",
                 stats(simulate(data, entry="chase",
                                **{**D, "stop": st, "trail": tr})))
        print()
        for hd in (30, 60, 120, 240, 480):
            line(f"chase 시간상한 {hd}분",
                 stats(simulate(data, entry="chase", **{**D, "hold": hd})))
        print()
        for ar in (0.0, 0.005, 0.01, 0.02):
            line(f"chase 트레일 작동 +{ar:.1%}",
                 stats(simulate(data, entry="chase", **{**D, "arm": ar})))

    elif a.mode == "control":
        header()
        for e in ("chase", "pre", "breakout"):
            s = stats(simulate(data, entry=e, **D))
            line(f"{e} 신호", s)
            if not s:
                continue
            tot = sum(len(v["c"]) for v in data.values())
            p = s["n"] / tot
            for seed in (1, 2, 3):
                rng = np.random.default_rng(seed)
                line(f"  └ 무작위 대조군 #{seed}",
                     stats(simulate(data, entry=e, **D,
                                    random_at=lambda s_, i_, _r=rng, _p=p: _r.random() < _p)))

    elif a.mode == "split":
        header()
        ts = np.concatenate([v["ts"] for v in data.values()])
        cut = np.median(ts)
        for e in ("chase", "pre", "breakout"):
            rows = simulate(data, entry=e, **D)
            df = pd.DataFrame(rows)
            if df.empty:
                continue
            line(f"{e} 전체", stats(rows))
            line(f"  └ 전반기", stats(df[df.ts < cut].to_dict("records")))
            line(f"  └ 후반기", stats(df[df.ts >= cut].to_dict("records")))


if __name__ == "__main__":
    main()
