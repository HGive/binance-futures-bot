"""
전략 조합 — 일별 자산곡선들을 받아 상관관계와 배분별 성적을 본다.

배분 방식은 미리 정한 두 가지만 쓴다 (탐색하지 않는다):
    equal : 같은 비율, 매월 리밸런싱
    invvol: 직전 90일 변동성의 역수 비율, 매월 리밸런싱 (정보는 리밸런싱 시점까지만)

    python backtest/combo.py name=path.csv name=path.csv ...
"""
import sys

import numpy as np
import pandas as pd


def load_curves(pairs):
    rets = {}
    for name, path in pairs:
        c = pd.read_csv(path, parse_dates=[0], index_col=0).iloc[:, 0]
        c.index = pd.to_datetime(c.index, utc=True).tz_localize(None).normalize()
        c = c[~c.index.duplicated(keep="last")].resample("1D").last().ffill()
        rets[name] = c.pct_change()
    return pd.DataFrame(rets)


def blend(rets, how="equal", cost=0.0):
    rets = rets.dropna()
    w = pd.DataFrame(index=rets.index, columns=rets.columns, dtype=float)
    month = rets.index.to_period("M")
    first = ~pd.Series(month, index=rets.index).duplicated()
    vol = rets.rolling(90, min_periods=30).std().shift(1)
    cur = None
    for i, d in enumerate(rets.index):
        if first.iloc[i] or cur is None:
            if how == "invvol" and vol.loc[d].notna().all():
                iv = 1 / vol.loc[d].replace(0, np.nan)
                cur = (iv / iv.sum()).fillna(1 / len(rets.columns))
            else:
                cur = pd.Series(1 / len(rets.columns), index=rets.columns)
        w.loc[d] = cur
    port = (w * rets).sum(axis=1)
    return (1 + port).cumprod()


def metrics(curve):
    c = curve.dropna()
    days = (c.index[-1] - c.index[0]).days
    tot = c.iloc[-1] / c.iloc[0] - 1
    cagr = (1 + tot) ** (365 / max(days, 1)) - 1
    d = c.pct_change().dropna()
    return dict(total=tot, cagr=cagr, vol=d.std() * np.sqrt(365),
                sharpe=d.mean() / d.std() * np.sqrt(365) if d.std() > 0 else np.nan,
                mdd=(c / c.cummax() - 1).min(), worst3m=(c / c.shift(91) - 1).min(),
                pos_month=(c.resample("ME").last().pct_change().dropna() > 0).mean())


def fmt(m):
    return (f"연 {m['cagr']*100:6.1f}% | 변동성 {m['vol']*100:5.1f}% | 샤프 {m['sharpe']:5.2f} | "
            f"MDD {m['mdd']*100:6.1f}% | 최악3개월 {m['worst3m']*100:6.1f}% | 월+ {m['pos_month']*100:3.0f}%")


if __name__ == "__main__":
    pairs = [a.split("=", 1) for a in sys.argv[1:]]
    rets = load_curves(pairs).dropna()
    print(f"공통 구간 {rets.index[0].date()} ~ {rets.index[-1].date()} ({len(rets)}일)\n")
    print("일수익 상관계수")
    print(rets.corr().round(2).to_string(), "\n")
    for col in rets.columns:
        print(f"{col:>12s}  " + fmt(metrics((1 + rets[col]).cumprod())))
    for how in ("equal", "invvol"):
        print(f"{'조합 '+how:>12s}  " + fmt(metrics(blend(rets, how))))
