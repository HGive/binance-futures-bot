"""
스캘핑 규칙 2개 검증 (1분봉 선물, 롱 전용).

  A. StochRSI 상승  — K, D 가 둘 다 오르는 중이고 K 가 지정 구간에 있을 때
  B. 급등 추종      — 최근 N봉 동안 거래량이 터지고 가격도 오를 때

회계(STRATEGY_RULES 3.3):
  신호 봉에서는 체결하지 않는다. 다음 봉 시가에 들어간다.
  체결 봉에서는 청산을 보지 않는다.
  한 봉에서 TP·SL 이 겹치면 SL 로 친다 (순서를 모르므로 나쁜 쪽).
  겹치는 관측 금지 — 한 거래가 끝난 뒤부터 다시 신호를 본다.

비용: 선물 USDⓈ-M
  지정가(메이커) 0.02% / 시장가(테이커) 0.05% + 슬리피지 0.02%
  레버리지는 승률 요건을 바꾸지 않는다 — 손익을 양쪽으로 똑같이 키운다.
  그래서 여기서는 1배 기준 수익률로 재고, 레버리지는 마지막에 곱한다.
"""
import os, sys, glob, argparse, itertools
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import data as dataio

FEE_MAKER, FEE_TAKER, SLIP = 0.0002, 0.0005, 0.0002
BAR_MS = 60_000


def load_1m(min_bars=20000):
    out = {}
    for p in sorted(glob.glob(os.path.join(dataio.CACHE_DIR, "*_1m.csv"))):
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
        out[base.replace("_1m.csv", "").replace("_USDT-USDT", "/USDT")] = d
    return out


def indicators(d, rsi_p=14, st_p=14, k_p=3, d_p=3):
    c = d["close"].astype(float)
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / rsi_p, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / rsi_p, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    lo, hi = rsi.rolling(st_p).min(), rsi.rolling(st_p).max()
    st = (rsi - lo) / (hi - lo).replace(0, np.nan) * 100
    K = st.rolling(k_p).mean()
    D = K.rolling(d_p).mean()
    v = d["volume"].astype(float)
    qv = v * c
    return dict(
        o=d["open"].astype(float).values, h=d["high"].astype(float).values,
        l=d["low"].astype(float).values, c=c.values,
        ts=d["timestamp"].values.astype("int64"),
        K=K.fillna(50).values, D=D.fillna(50).values,
        dK=(K - K.shift(1)).fillna(0).values, dD=(D - D.shift(1)).fillna(0).values,
        qv=qv.values, qv_ma=qv.rolling(60).mean().values,
        ret3=(c / c.shift(3) - 1).fillna(0).values,
        ret5=(c / c.shift(5) - 1).fillna(0).values,
        green=(c > d["open"].astype(float)).values,
    )


# ────────────────────────── 신호 ──────────────────────────
def sig_stoch(I, i, klo, khi):
    """K, D 가 둘 다 오르는 중 + K 가 klo~khi 구간."""
    return (I["dK"][i] > 0 and I["dD"][i] > 0 and klo <= I["K"][i] <= khi)


def sig_pop(I, i, vmult, rmin, nbars, ngreen):
    """거래량이 평소의 vmult 배 이상 + 최근 nbars 봉 상승률 rmin 이상 + 양봉 ngreen 개 이상."""
    if not np.isfinite(I["qv_ma"][i]) or I["qv_ma"][i] <= 0:
        return False
    if I["qv"][i] < I["qv_ma"][i] * vmult:
        return False
    r = I["ret3"][i] if nbars == 3 else I["ret5"][i]
    if r < rmin:
        return False
    g = int(I["green"][i - nbars + 1:i + 1].sum())
    return g >= ngreen


def sig_random(I, i, every):
    """대조군 — 신호 없이 every 봉마다."""
    return i % every == 0


# ────────────────────────── 시뮬 ──────────────────────────
def run(data, sig, tp, sl, hold, maker_entry=False, min_qv=200_000):
    rets, whys, syms = [], [], []
    fee_in = FEE_MAKER if maker_entry else FEE_TAKER + SLIP
    for sym, I in data.items():
        n = len(I["c"])
        i = 100
        while i < n - hold - 2:
            if not np.isfinite(I["qv_ma"][i]) or I["qv_ma"][i] < min_qv:
                i += 1; continue
            if not sig(I, i):
                i += 1; continue
            e = i + 1                                    # 신호 봉에서는 안 산다
            px = I["o"][e] * (1 + (0 if maker_entry else SLIP))
            tpx, spx = px * (1 + tp), px * (1 - sl)
            last = min(n - 1, e + hold)
            out = None
            for j in range(e + 1, last + 1):             # 체결 봉 제외
                if I["l"][j] <= spx:
                    out = ((spx / px - 1) - fee_in - FEE_TAKER - SLIP, "SL"); break
                if I["h"][j] >= tpx:
                    out = ((tpx / px - 1) - fee_in - FEE_MAKER, "TP"); break
            if out is None:
                out = ((I["c"][last] / px - 1) - fee_in - FEE_TAKER - SLIP, "TIME")
            rets.append(out[0]); whys.append(out[1]); syms.append(sym)
            i = last + 1                                 # 겹치는 관측 금지
    if len(rets) < 50:
        return None
    a = np.array(rets); w = np.array(whys)
    win, loss = a[a > 0], a[a <= 0]
    pf = win.sum() / abs(loss.sum()) if len(loss) and loss.sum() != 0 else float("inf")
    res = (w != "TIME")
    # 결판난 것 중 TP 비율 — 이게 손익분기(SL/(TP+SL))와 직접 비교되는 값이다.
    # a>0 은 수수료 뺀 뒤 이익이었는지라 시간초과가 섞여 뜻이 흐려진다.
    tp_share = (w[res] == "TP").mean() if res.any() else np.nan
    gross = a + (FEE_MAKER if maker_entry else FEE_TAKER + SLIP)   # 진입 비용만 되돌린 근사
    return dict(n=len(a), win=(a > 0).mean(), mean=a.mean(), total=a.sum(), pf=pf,
                nsym=len(set(syms)), tp=(w == "TP").mean(), sl=(w == "SL").mean(),
                tm=(w == "TIME").mean(), be=sl / (tp + sl),
                tp_share=tp_share, resolved=res.mean())


def line(tag, r, base=None):
    if r is None:
        print(f"{tag:<40} 관측 부족"); return
    d = ""
    if base and base["n"]:
        d = f"  대조군대비 {(r['win']-base['win'])*100:+.1f}%p"
    print(f"{tag:<40}{r['n']:>7}{r['win']*100:>7.1f}%{r['be']*100:>8.1f}%"
          f"{(r['win']-r['be'])*100:>+8.1f}%p{r['mean']*100:>+9.3f}%{r['pf']:>7.2f}"
          f"{r['tp']*100:>5.0f}/{r['sl']*100:.0f}/{r['tm']*100:.0f}{d}", flush=True)


def header():
    print(f"{'규칙':<40}{'건수':>7}{'승률':>7}{'손익분기':>8}{'차이':>8}{'평균':>9}{'PF':>7}  TP/SL/시간")
    print("-" * 108)
