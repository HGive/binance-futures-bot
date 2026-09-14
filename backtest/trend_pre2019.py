"""
RESEARCH_DATA=... python backtest/trend_pre2019.py  (FRED CBBTCUSD·CBETHUSD 를 RESEARCH_DATA/ext 에 받아둔다)

확정한 TREND_VT 규칙을 설계에 한 번도 안 쓴 2015~2019 코인베이스 일봉에 적용 (진짜 표본 외).
손절은 고가/저가가 없어 제외. 펀딩비 이력이 없어 가정값으로 (롱이 연 x% 지불)."""
import sys, os; sys.path.insert(0, ".")
from dataclasses import replace
import numpy as np, pandas as pd
import backtest.trend as TR, strategies.trend_vt as ST
from backtest.combo import metrics
from backtest.fetch_research import OUT
def fred(i):
    d = pd.read_csv(os.path.join(OUT, "ext", f"{i}.csv")); d.columns = ["date", "v"]
    d["v"] = pd.to_numeric(d["v"], errors="coerce"); d = d.dropna()
    return pd.Series(d.v.values, index=pd.to_datetime(d.date))
close = pd.DataFrame({"BTCUSDT": fred("CBBTCUSD"), "ETHUSDT": fred("CBETHUSD")})
close = close.reindex(pd.date_range(close.index[0], close.index[-1], freq="D")).ffill(limit=3)
qv = pd.DataFrame(1.0, index=close.index, columns=close.columns)
# 코인베이스 종가 vs 바이낸스 선물 종가 차이 (겹치는 구간) — 데이터 신뢰 확인
bc, _, _ = TR.load_daily()
common = bc.index.intersection(close.index)
gap = (close.loc[common] / bc.loc[common, ["BTCUSDT", "ETHUSDT"]] - 1).abs()
print(f"코인베이스 vs 바이낸스 선물 일봉 차이 중앙값 BTC {gap.BTCUSDT.median()*100:.2f}% ETH {gap.ETHUSDT.median()*100:.2f}% (시각 차이 포함)")
END = pd.Timestamp("2019-09-09")
P0 = replace(ST.LIVE, stop_k=None)
def run(apr, cost, end=END, start=None):
    f = pd.DataFrame(apr / 365, index=close.index, columns=close.columns)
    c, st = TR.simulate(close, qv, f, replace(P0, cost=cost), start=start, end=end)
    first_trade = c[c.diff().fillna(0) != 0].index[0]
    return c[c.index >= first_trade - pd.Timedelta(days=1)], st
hold = close.loc[:END].pct_change().mean(axis=1).fillna(0)
for apr, cost in ((0.0, 0.001), (0.11, 0.001), (0.11, 0.002), (0.20, 0.002)):
    c, st = run(apr, cost); m = metrics(c)
    yr = c.resample("YE").last(); ann = np.r_[yr.iloc[0] / c.iloc[0] - 1, yr.pct_change().dropna().values]
    print(f"펀딩 롱지불 연 {apr:.0%} 비용 편도 {cost:.1%}: {c.index[0].date()}~{c.index[-1].date()} 연 {m['cagr']*100:5.1f}% MDD {m['mdd']*100:6.1f}% 샤프 {m['sharpe']:.2f} | "
          f"연도별 {dict(zip(yr.index.year, np.round(ann*100, 1)))}")
h = (1 + hold[hold.index >= c.index[0]]).cumprod(); mh = metrics(h)
print(f"비교: BTC·ETH 반반 보유 같은 기간 연 {mh['cagr']*100:.1f}% MDD {mh['mdd']*100:.1f}% 샤프 {mh['sharpe']:.2f}")
# BTC 만 2015~ (ETH 없는 시기 포함)
cb = close[["BTCUSDT"]]
f = pd.DataFrame(0.11 / 365, index=cb.index, columns=cb.columns)
c, _ = TR.simulate(cb, qv[["BTCUSDT"]], f, replace(P0, fixed=("BTCUSDT",)), end=END)
c = c[c.index >= "2015-04-01"]; m = metrics(c)
yr = c.resample("YE").last(); ann = np.r_[yr.iloc[0] / c.iloc[0] - 1, yr.pct_change().dropna().values]
print(f"BTC 만 2015-04~2019-09 (펀딩 11%, 비용 0.1%): 연 {m['cagr']*100:.1f}% MDD {m['mdd']*100:.1f}% 샤프 {m['sharpe']:.2f} | 연도별 {dict(zip(yr.index.year, np.round(ann*100,1)))}")
# 무작위 신호 베이스라인 (같은 구간)
orig = TR.targets; rng = np.random.default_rng(7); sh = []
c0, _ = run(0.11, 0.001); s0 = metrics(c0)["sharpe"]
for k in range(100):
    blocks = np.repeat(rng.choice([-1.0, 1.0], size=(len(close) // 20 + 1, 2)), 20, axis=0)[:len(close)]
    sig = pd.DataFrame(blocks, index=close.index, columns=close.columns)
    def patched(cc, q, pp, sig=sig):
        w, r = orig(cc, q, pp); return w.abs() * sig.reindex_like(w).fillna(0), r
    TR.targets = patched
    try:
        cr, _ = run(0.11, 0.001); sh.append(metrics(cr)["sharpe"])
    finally:
        TR.targets = orig
print(f"무작위 신호 100회 샤프 중앙값 {np.median(sh):.2f} / 95% {np.percentile(sh,95):.2f} → 규칙 {s0:.2f} 는 {np.mean(np.array(sh) < s0)*100:.0f}% 보다 위")
