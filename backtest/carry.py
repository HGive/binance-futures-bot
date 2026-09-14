"""
펀딩비 차익 (현물 롱 + 무기한 숏, 델타 중립) 백테스트.

가격 방향과 무관하게 펀딩비를 받는다. 8시간 격자에서 돈다.

    시점 τ_k (펀딩 정산 직후, 봉 마감):
        지금까지 알려진 펀딩비로 보유 종목을 정한다 → τ_k 종가로 체결
    τ_k → τ_k+1:
        현물 손익 + 선물 숏 손익 (= 베이시스 변화) + τ_k+1 펀딩비

비용: 현물·선물 각각 수수료+슬리피지, 진입과 청산에 4번.
위험: 숏 쪽 증거금이 바닥나면(봉 고가 기준) 강제 청산 + 청산 수수료.
      증거금 절반을 잃으면 그 종목을 청산 후 재진입(리밸런싱)해 청산가를 밀어낸다.

    RESEARCH_DATA=... python backtest/carry.py
"""
import glob
import os
import sys
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.fetch_research import OUT, spot_symbol  # noqa: E402

H8 = 8 * 3600_000
PER_YEAR = 3 * 365


@dataclass(frozen=True)
class Cost:
    spot: float      # 현물 편도 (수수료+슬리피지)
    perp: float      # 선물 편도
    liq_fee: float = 0.015   # 강제 청산 시 명목가 대비 손실


TAKER = Cost(spot=0.0010 + 0.0005, perp=0.0005 + 0.0005)   # 편도 합 0.25% → 왕복 0.5%
MAKERISH = Cost(spot=0.00075 + 0.0002, perp=0.00036 + 0.0002)  # BNB 할인 + 지정가 위주


@dataclass(frozen=True)
class Params:
    lookback: int = 9          # 평균 낼 펀딩 횟수 (9 = 3일)
    enter_apr: float = 0.20    # 이 연율 이상이면 진입 후보
    exit_apr: float = 0.05     # 보유 중 이 연율 밑으로 떨어지면 청산
    k: int = 5                 # 최대 보유 종목
    lev: float = 2.0           # 숏 레버리지 (격리)
    deploy: float = 0.8        # 자본 중 투입 비율 (나머지 20% 현금)
    min_qv: float = 20e6       # 선물 일평균 거래대금 하한 (USDT)
    min_spot_qv: float = 3e6   # 현물 일평균 거래대금 하한
    min_age: int = 90          # 상장 후 최소 봉 수 (90 = 30일)
    mmr: float = 0.02          # 유지증거금률 (알트 보수적)
    selection: str = "top"     # "top" | "random" (베이스라인)


def load_panel(min_bars=270):
    fund_files = sorted(glob.glob(os.path.join(OUT, "funding", "*.csv")))
    syms, P, PH, S, F, QV, SQV = [], {}, {}, {}, {}, {}, {}
    for ff in fund_files:
        sym = os.path.basename(ff)[:-4]
        pf = os.path.join(OUT, "perp", f"{sym}.csv")
        ssym, mult = spot_symbol(sym)
        sf = os.path.join(OUT, "spot", f"{ssym}.csv")
        if not (os.path.exists(pf) and os.path.exists(sf)):
            continue
        p = pd.read_csv(pf)
        s = pd.read_csv(sf)
        if len(p) < min_bars or len(s) < min_bars:
            continue
        f = pd.read_csv(ff)
        # 정산 시각을 8시간 격자에 올린다. 4h/1h 정산은 그 8시간 구간에 합산.
        tr = (f["fundingTime"] / 60000).round() * 60000
        f["tau"] = (np.ceil(tr / H8) * H8).astype("int64")
        fs = f.groupby("tau")["fundingRate"].sum()
        tau_p = p["t"] + H8           # 봉 마감 = 정산 시각
        tau_s = s["t"] + H8
        P[sym] = pd.Series(p["c"].values, tau_p)
        PH[sym] = pd.Series(p["h"].values, tau_p)
        QV[sym] = pd.Series(p["qv"].values, tau_p)
        S[sym] = pd.Series(s["c"].values * mult, tau_s)
        SQV[sym] = pd.Series(s["qv"].values, tau_s)
        F[sym] = fs
        syms.append(sym)
    idx = sorted(set().union(*[set(v.index) for v in P.values()]))
    idx = pd.Index(idx)

    def mat(d):
        return pd.DataFrame({k: v[~v.index.duplicated()] for k, v in d.items()}).reindex(idx)
    panel = dict(P=mat(P), PH=mat(PH), S=mat(S), F=mat(F), QV=mat(QV), SQV=mat(SQV))
    # 현물만 액면변경(DREP ×100, BNX ÷100 ...)되면 가짜 베이시스 손익이 생긴다.
    # 두 가격이 2배 이상 벌어진 봉은 데이터 단절로 보고 비운다 → 보유 중이면 직전 가격에 청산.
    ratio = panel["P"] / panel["S"]
    broken = (ratio > 2) | (ratio < 0.5)
    panel["S"] = panel["S"].mask(broken)
    panel["P"] = panel["P"].mask(broken)
    return syms, idx, panel


def prepare(panel, p: Params):
    """판단에 쓰는 지표 — 전부 τ_k 까지의 정보만."""
    F = panel["F"]
    favg = F.rolling(p.lookback, min_periods=p.lookback).mean()
    qv = panel["QV"].rolling(21, min_periods=21).mean() * 3        # 일평균
    sqv = panel["SQV"].rolling(21, min_periods=21).mean() * 3
    age = panel["P"].notna().cumsum()
    basis = panel["P"] / panel["S"] - 1
    return favg, qv, sqv, age, basis


def simulate(panel, p: Params, cost: Cost, start=None, end=None, seed=0, record=False):
    P = panel["P"].values
    PH = panel["PH"].values
    S = panel["S"].values
    F = np.nan_to_num(panel["F"].values)
    times = panel["P"].index.values
    favg, qv, sqv, age, basis = prepare(panel, p)
    favg, qv, sqv, age, basis = favg.values, qv.values, sqv.values, age.values, basis.values
    n_t, n_s = P.shape
    rng = np.random.default_rng(seed)
    per_bar_enter = p.enter_apr / PER_YEAR
    per_bar_exit = p.exit_apr / PER_YEAR

    k0 = 0 if start is None else int(np.searchsorted(times, start))
    k1 = n_t - 1 if end is None else int(np.searchsorted(times, end))
    cash = 1.0
    pos = {}   # j -> dict(qs, qp, pe, margin, t0, fees, fund)
    eq = np.full(n_t, np.nan)
    stats = dict(fees=0.0, funding=0.0, liq=0, rebal=0, trades=0)
    closed = []   # (sym_idx, pnl, bars)

    def equity(k):
        v = cash
        for j, x in pos.items():
            s = S[k, j] if np.isfinite(S[k, j]) else x["last_s"]
            pr = P[k, j] if np.isfinite(P[k, j]) else x["last_p"]
            v += x["qs"] * s + x["qp"] * (x["pe"] - pr) + x["margin"]
        return v

    def close(j, k, px_perp=None, liq=False):
        nonlocal cash
        x = pos.pop(j)
        s = S[k, j] if np.isfinite(S[k, j]) else x["last_s"]
        pr = px_perp if px_perp is not None else (P[k, j] if np.isfinite(P[k, j]) else x["last_p"])
        spot_val = x["qs"] * s
        perp_pnl = x["qp"] * (x["pe"] - pr)
        fee = spot_val * cost.spot + x["qp"] * pr * cost.perp
        if liq:
            perp_pnl = -x["margin"]                   # 증거금 전부 잃음
            fee = spot_val * cost.spot + x["qp"] * pr * cost.liq_fee
            stats["liq"] += 1
        cash += spot_val + x["margin"] + perp_pnl - fee
        stats["fees"] += fee
        pnl = (spot_val - x["cost_spot"]) + perp_pnl - fee - x["fees"] + x["fund"]
        closed.append((j, pnl, k - x["t0"], x["fund"]))

    def open_(j, k, alloc):
        nonlocal cash
        notional = alloc * p.lev / (p.lev + 1)
        margin = alloc - notional
        qs = notional / S[k, j]
        qp = notional / P[k, j]
        fee = notional * (cost.spot + cost.perp)
        cash -= notional + margin + fee
        stats["fees"] += fee
        stats["trades"] += 1
        pos[j] = dict(qs=qs, qp=qp, pe=P[k, j], margin=margin, t0=k, fees=fee, fund=0.0,
                      cost_spot=notional, last_s=S[k, j], last_p=P[k, j])

    for k in range(max(k0, 1), k1 + 1):
        # 1) τ_{k-1} → τ_k 구간 결과 반영: 강제청산 검사, 펀딩 수령
        for j in list(pos.keys()):
            x = pos[j]
            liq_px = x["pe"] * (1 + (x["margin"] / (x["qp"] * x["pe"])) - p.mmr)
            if np.isfinite(PH[k, j]) and PH[k, j] >= liq_px:
                close(j, k, px_perp=liq_px, liq=True)
                continue
            if not (np.isfinite(P[k, j]) and np.isfinite(S[k, j])):
                # 데이터 끝 (상장폐지) → 마지막 가격에 청산, 슬리피지 3배
                close(j, k)
                cash -= x["qs"] * x["last_s"] * cost.spot * 2
                continue
            fr = F[k, j]
            got = x["qp"] * P[k, j] * fr
            cash += got
            x["fund"] += got
            stats["funding"] += got
            x["last_s"], x["last_p"] = S[k, j], P[k, j]

        E = equity(k)
        eq[k] = E

        # 2) 보유 중 청산 판단
        for j in list(pos.keys()):
            x = pos[j]
            if not np.isfinite(favg[k, j]) or favg[k, j] < per_bar_exit:
                close(j, k)
                continue
            # 증거금 절반을 잃었으면 리밸런싱: 닫고 다시 연다
            loss = x["qp"] * (P[k, j] - x["pe"])
            if loss > 0.5 * x["margin"]:
                alloc = x["qs"] * S[k, j] + x["margin"] - loss
                close(j, k)
                if alloc > 0:
                    open_(j, k, alloc)
                    stats["rebal"] += 1

        # 3) 신규 진입
        free = p.k - len(pos)
        if free > 0:
            ok = (np.isfinite(favg[k]) & (favg[k] >= per_bar_enter) & np.isfinite(P[k]) & np.isfinite(S[k])
                  & (np.nan_to_num(qv[k]) >= p.min_qv) & (np.nan_to_num(sqv[k]) >= p.min_spot_qv)
                  & (age[k] >= p.min_age) & (np.abs(np.nan_to_num(basis[k], nan=1)) < 0.02))
            cand = [j for j in np.where(ok)[0] if j not in pos]
            if cand:
                if p.selection == "random":
                    rng.shuffle(cand)
                else:
                    cand.sort(key=lambda j: -favg[k, j])
                per_alloc = E * p.deploy / p.k
                for j in cand[:free]:
                    if cash > per_alloc * 1.01 - E * (1 - p.deploy) * 0:
                        open_(j, k, per_alloc)

    for j in list(pos.keys()):
        close(j, k1)
    eq[k1] = cash
    curve = pd.Series(eq[k0:k1 + 1], index=pd.to_datetime(times[k0:k1 + 1], unit="ms")).ffill()
    return curve, stats, closed


def metrics(curve, stats=None, closed=None, syms=None):
    c = curve.dropna()
    days = (c.index[-1] - c.index[0]).total_seconds() / 86400
    tot = c.iloc[-1] / c.iloc[0] - 1
    cagr = (1 + tot) ** (365 / max(days, 1)) - 1
    daily = c.resample("1D").last().pct_change().dropna()
    vol = daily.std() * np.sqrt(365)
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else np.nan
    mdd = (c / c.cummax() - 1).min()
    q = c.resample("1D").last()
    worst3m = (q / q.shift(91) - 1).min()
    monthly = c.resample("ME").last().pct_change().dropna()
    out = dict(total=tot, cagr=cagr, vol=vol, sharpe=sharpe, mdd=mdd, worst3m=worst3m,
               pos_month=(monthly > 0).mean(), pos_day=(daily > 0).mean())
    if closed:
        pnl = np.array([x[1] for x in closed])
        out["trades"] = len(pnl)
        gp, gl = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
        out["pf"] = gp / gl if gl > 0 else np.inf
        out["win"] = (pnl > 0).mean()
        out["hold_days"] = np.mean([x[2] for x in closed]) / 3
        by = pd.Series(pnl).groupby([x[0] for x in closed]).sum()
        out["sym_pos"] = (by > 0).mean()
        out["n_sym"] = len(by)
    if stats:
        out.update({k: v for k, v in stats.items()})
    return out


def fmt(m):
    s = (f"총 {m['total']*100:7.1f}% | 연 {m['cagr']*100:6.1f}% | MDD {m['mdd']*100:6.1f}% | "
         f"샤프 {m['sharpe']:5.2f} | 최악3개월 {m['worst3m']*100:6.1f}% | 월+ {m['pos_month']*100:4.0f}%")
    if "trades" in m:
        s += (f" | 거래 {m['trades']:4d} PF {m['pf']:5.2f} 보유 {m['hold_days']:5.1f}일 "
              f"종목+ {m['sym_pos']*100:3.0f}%/{m['n_sym']} | 펀딩 {m['funding']*100:6.1f}% 비용 {m['fees']*100:5.1f}% 청산 {m['liq']}")
    return s


if __name__ == "__main__":
    syms, idx, panel = load_panel()
    t0, t1 = idx[0], idx[-1]
    split = int(t0 + (t1 - t0) * 0.7)
    print(f"종목 {len(syms)} | {pd.to_datetime(t0, unit='ms').date()} ~ {pd.to_datetime(t1, unit='ms').date()} "
          f"| 개발/검증 경계 {pd.to_datetime(split, unit='ms').date()}")
    base = Params()
    for name, cost in (("테이커", TAKER), ("지정가위주", MAKERISH)):
        c, st, cl = simulate(panel, base, cost, end=split)
        print(f"[개발] 기본 {name:6s} " + fmt(metrics(c, st, cl)))
