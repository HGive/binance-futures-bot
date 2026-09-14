"""
시계열 추세추종 (변동성 목표) 백테스트 — 무기한 선물, 일봉.

신호·비중·거래 판정은 strategies/trend_vt.py 에만 있다 (규칙 3.1, 3.7). 여기선 import 만 한다.
    신호 = 여러 룩백(20/60/120일)의 방향 평균  → -1 ~ +1
    비중 = 신호 × (목표 변동성 / 종목 실현 변동성)
    포트폴리오 전체 변동성을 다시 목표에 맞춘다, 총 레버리지 상한.

비용: 선물 편도 수수료+슬리피지, 실제 펀딩비(롱은 내고 숏은 받는다).
판단은 τ 일 종가까지의 정보, 체결은 τ 일 종가 (다음 날 수익부터 반영).

    RESEARCH_DATA=... python backtest/trend.py
"""
import glob
import os
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.fetch_research import OUT  # noqa: E402
from strategies.trend_vt import LIVE, TParams, daily_vol, stop_price, targets, trade_mask  # noqa: E402,F401

# 탐색 단계에서 비교한 변형들 (LIVE 와의 차이만 적는다). 탐색·사전등록 당시엔 손절이 없었다.
TOP20 = replace(LIVE, fixed=(), max_w=0.25, stop_k=None)
STOP_EXIT_FEE = 0.0005      # 손절 체결 수수료 (슬리피지는 TParams.stop_slip)


def load_daily(with_ohl=False):
    """8시간봉 3개 → UTC 일봉. 펀딩은 하루 합. with_ohl=True 면 (시가, 고가, 저가) 도 준다 (손절 판정용)."""
    C, QV, FD, O, H, L = {}, {}, {}, {}, {}, {}
    for pf in sorted(glob.glob(os.path.join(OUT, "perp", "*.csv"))):
        sym = os.path.basename(pf)[:-4]
        p = pd.read_csv(pf)
        if len(p) < 300:
            continue
        day = (p["t"] // 86400_000) * 86400_000
        g = p.groupby(day)
        cnt = g["c"].size()
        c = g["c"].last()[cnt == 3]
        C[sym] = c
        QV[sym] = g["qv"].sum()[cnt == 3]
        if with_ohl:
            O[sym] = g["o"].first()[cnt == 3]
            H[sym] = g["h"].max()[cnt == 3]
            L[sym] = g["l"].min()[cnt == 3]
        ff = os.path.join(OUT, "funding", f"{sym}.csv")
        if os.path.exists(ff):
            f = pd.read_csv(ff)
            tr = (f["fundingTime"] / 60000).round() * 60000
            # 날짜 d 의 보유분이 내는 펀딩: (d, d+1] 구간 → 다음날 00:00 정산 포함
            dd = (np.ceil(tr / 86400_000) * 86400_000 - 86400_000).astype("int64")
            FD[sym] = f.groupby(dd)["fundingRate"].sum()
    idx = pd.Index(sorted(set().union(*[set(v.index) for v in C.values()])))

    def mk(d):
        return pd.DataFrame(d).reindex(idx)
    close, qv, fund = mk(C), mk(QV), mk(FD).reindex(columns=list(C.keys()))
    close.index = pd.to_datetime(close.index, unit="ms")
    qv.index = close.index
    fund.index = close.index
    if not with_ohl:
        return close, qv, fund.fillna(0.0)
    ohl = tuple(mk(d).set_axis(close.index) for d in (O, H, L))
    return close, qv, fund.fillna(0.0), ohl


def simulate(close, qv, fund, p: TParams, start=None, end=None, ohl=None):
    """p.stop_k 가 있으면 재난 손절을 실전과 같은 규칙으로 돌린다 (규칙 3.1·3.5·3.10).

    매일 종가에 목표로 맞춘 뒤 손절가 = stop_price(종가, 방향, daily_vol) 을 건다.
    다음 날 저가(롱)/고가(숏)가 닿으면 min(손절가, 시가) 에 슬리피지를 얹어 청산 → 그날은 비중 0.
    그날 종가에 목표를 다시 계산해 거래 여부를 정한다 (실전 봇이 다음 실행에서 하는 것과 같다).
    """
    w_tgt, ret = targets(close, qv, p)
    use_stop = p.stop_k is not None
    if use_stop and ohl is None:
        raise ValueError("stop_k 를 쓰려면 ohl=(시가,고가,저가) 가 필요하다. load_daily(with_ohl=True)")
    sl = np.ones(len(close), bool)
    if start is not None:
        sl &= close.index >= start
    if end is not None:
        sl &= close.index < end
    dates = close.index[sl]
    R = ret.fillna(0.0).values
    FU = fund.values
    W = w_tgt.values
    C = close.ffill().values
    if use_stop:
        OP, HI, LO = (x.reindex(index=close.index, columns=close.columns).values for x in ohl)
        VD = daily_vol(close, p).values
    ii = np.where(sl)[0]
    eq = 1.0
    cur = np.zeros(close.shape[1])
    stops = np.full(close.shape[1], np.nan)
    curve, turn, fees, fpaid, nstop = [], 0.0, 0.0, 0.0, 0
    by_sym = np.zeros(close.shape[1])
    for n, i in enumerate(ii):
        if n > 0:
            r = np.nan_to_num(R[i]).copy()
            if use_stop:
                for j in np.where((cur != 0) & np.isfinite(stops))[0]:
                    if cur[j] > 0 and LO[i, j] <= stops[j]:
                        fill = min(stops[j], OP[i, j]) * (1 - p.stop_slip)
                    elif cur[j] < 0 and HI[i, j] >= stops[j]:
                        fill = max(stops[j], OP[i, j]) * (1 + p.stop_slip)
                    else:
                        continue
                    rj = fill / C[i - 1, j] - 1
                    pnl_j = cur[j] * rj - cur[j] * FU[i, j]
                    by_sym[j] += pnl_j * eq
                    eq *= 1 + pnl_j
                    c = abs(cur[j]) * (1 + rj) * STOP_EXIT_FEE
                    eq *= 1 - c
                    fees += c * eq
                    cur[j] = 0.0
                    r[j] = 0.0
                    nstop += 1
            # 어제 종가에 정한 비중으로 오늘 수익
            pnl_vec = np.nan_to_num(cur * r - cur * FU[i])   # 롱은 양의 펀딩을 낸다
            by_sym += pnl_vec * eq
            day_pnl = pnl_vec.sum()
            fpaid += np.nansum(cur * FU[i]) * eq
            eq *= 1 + day_pnl
            # 가격 변동으로 비중이 흘러간다
            cur = cur * (1 + r) / (1 + day_pnl)
        tgt = np.nan_to_num(W[i])
        d = np.where(trade_mask(tgt, cur, p.band), tgt - cur, 0.0)
        c = np.abs(d).sum() * p.cost
        eq *= 1 - c
        fees += c * eq
        turn += np.abs(d).sum()
        cur = cur + d
        if use_stop:
            side = np.sign(cur)
            stops = np.where(side != 0, stop_price(C[i], side, VD[i], p.stop_k), np.nan)
        curve.append(eq)
    curve = pd.Series(curve, index=dates)
    years = max((dates[-1] - dates[0]).days / 365, 1e-9)
    return curve, dict(turnover_per_year=turn / years, fees=fees, funding_paid=fpaid, stops=nstop,
                       by_sym=pd.Series(by_sym, index=close.columns))


def metrics(curve):
    c = curve.dropna()
    days = (c.index[-1] - c.index[0]).days
    tot = c.iloc[-1] / c.iloc[0] - 1
    cagr = (1 + tot) ** (365 / max(days, 1)) - 1
    d = c.pct_change().dropna()
    sharpe = d.mean() / d.std() * np.sqrt(365) if d.std() > 0 else np.nan
    mdd = (c / c.cummax() - 1).min()
    worst3m = (c / c.shift(91) - 1).min()
    m = c.resample("ME").last().pct_change().dropna()
    return dict(total=tot, cagr=cagr, sharpe=sharpe, mdd=mdd, worst3m=worst3m, pos_month=(m > 0).mean(),
                vol=d.std() * np.sqrt(365))


def fmt(m):
    return (f"총 {m['total']*100:8.1f}% | 연 {m['cagr']*100:6.1f}% | 변동성 {m['vol']*100:5.1f}% | MDD {m['mdd']*100:6.1f}% | "
            f"샤프 {m['sharpe']:5.2f} | 최악3개월 {m['worst3m']*100:6.1f}% | 월+ {m['pos_month']*100:3.0f}%")


if __name__ == "__main__":
    close, qv, fund, ohl = load_daily(with_ohl=True)
    t0, t1 = close.index[0], close.index[-1]
    split = t0 + (t1 - t0) * 0.7
    print(f"종목 {close.shape[1]} | {t0.date()} ~ {t1.date()} | 개발/검증 경계 {split.date()}")
    long20 = (5, 10, 20, 30, 60, 90, 150, 250, 360)
    nostop = replace(LIVE, stop_k=None)
    variants = {
        "BTC+ETH L/S (사전등록)": nostop,
        "L/S top20": TOP20,
        "롱만 top20": replace(TOP20, long_only=True),
        "BTC+ETH 롱만": replace(nostop, long_only=True),
        "돈키언 top20": replace(TOP20, signal="donchian", lookbacks=long20, long_only=True, target_vol=0.25),
        "돈키언 BTC+ETH": replace(nostop, signal="donchian", lookbacks=long20, long_only=True, target_vol=0.25),
    }
    for name, p in variants.items():
        c, st = simulate(close, qv, fund, p, end=split)
        print(f"[개발] {name:20s} " + fmt(metrics(c)) + f" | 회전 {st['turnover_per_year']:.0f}x/년")
    for name, p in (("LIVE 손절없음", nostop), ("LIVE (손절 k=5)", LIVE)):
        c, st = simulate(close, qv, fund, p, ohl=ohl)
        print(f"[전체] {name:20s} " + fmt(metrics(c)) + f" | 손절 {st.get('stops', 0)}회")
        print(f"[검증] {name:20s} " + fmt(metrics(c[split:])))
