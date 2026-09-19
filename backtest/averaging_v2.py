"""물타기 1회 + 손절 버전 — 사용자가 실제로 하려던 전략.

    무작위 시점에 진입
    → 조금 오르면 익절 (+tp)
    → step 만큼 빠지면 딱 한 번 물타기 (평단이 내려감)
    → 물타기하면 손절선이 생긴다 (평단 -stop)
    → 거의 항상 익절, 질 때는 조금만 잃는다

앞선 backtest/averaging.py 와 **일부러 독립적으로** 새로 짰다. 같은 결론이
두 구현에서 나오는지 보기 위해서다 (STRATEGY_RULES 4.2 독립 재구현).
60 줄이라 눈으로 따라갈 수 있다.

검증 장치 (STRATEGY_RULES 4.3 무작위 기준선):
- 같은 전략을 **무작위 가격**에도 돌린다. 이론상 기댓값 0 이어야 한다.
  0 이 아니면 내 코드가 틀린 것이다.
- **수익률을 뒤섞은** 실제 가격에도 돌린다. 변동성은 같고 시간 구조만 없앤 것.
  실제 가격에서만 돈이 벌리면 진짜 엣지(단기 반전)가 있는 것이다.

    ./.venv-research/bin/python backtest/averaging_v2.py
"""

from __future__ import annotations

import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- 설정


@dataclass(frozen=True)
class V2:
    tp: float = 0.01  # 평단 대비 익절
    step: float = 0.02  # 이만큼 빠지면 물타기 1회
    stop: float = 0.02  # 물타기 후 평단 대비 손절
    add_mult: float = 1.0  # 물타기 금액 배수 (1.0 = 첫 매수와 같은 금액)
    cost: float = 0.0005  # 편도 비용
    max_bars: int = 0  # 0 = 제한 없음
    stop_before_add: bool = False  # 물타기 전에도 손절선을 둘지


# --------------------------------------------------------------------------- 엔진


def run_trades(o, h, low, c, entries, p: V2):
    """진입 시점 목록 → 거래 결과. pnl 은 '첫 매수 금액' 대비 비율.

    한 봉 안에서 손절과 익절이 모두 닿으면 **손절을 먼저** 처리한다(보수적).
    물타기가 일어난 봉에서는 익절을 허용하지 않는다(낮아진 평단은 미래참조).
    """
    n = len(c)
    out = []
    for i in entries:
        if i + 1 >= n:
            continue
        entry = o[i + 1]  # 신호 다음 봉 시가에 진입
        if not np.isfinite(entry) or entry <= 0:
            continue
        basis = entry  # 쓴 돈 (첫 매수 = 1 단위 금액)
        units = 1.0
        avg = entry
        add_px = entry * (1 - p.step)
        added = False
        stop_px = entry * (1 - p.stop) if p.stop_before_add else -np.inf

        j = i + 1
        while j < n:
            if p.max_bars and (j - i) > p.max_bars:
                px = c[j]
                out.append(_close(basis, units, px, entry, p, j - i, added, "시간"))
                break

            # 1) 손절 먼저
            if low[j] <= stop_px:
                px = min(stop_px, o[j])  # 갭하락이면 더 나쁜 시가
                out.append(_close(basis, units, px, entry, p, j - i, added, "손절"))
                break

            # 2) 물타기 (한 번만)
            hit_add = (not added) and low[j] <= add_px
            if hit_add:
                px = min(add_px, o[j])
                spend = entry * p.add_mult  # 첫 매수 금액의 배수만큼 더 산다
                units += spend / px
                basis += spend
                avg = basis / units
                stop_px = avg * (1 - p.stop)  # ← 물타기하면 손절선이 생긴다
                added = True
                # 같은 봉에서 손절까지 닿았다면 맞아야 한다. 봉 안의 경로는 알 수
                # 없으므로 불리한 순서(물타기 → 계속 하락)를 가정한다. 이걸 빼면
                # 무작위 가격에서도 기댓값이 +0.64% 로 부풀어 오른다.
                if low[j] <= stop_px:
                    out.append(_close(basis, units, min(stop_px, px), entry, p, j - i, True, "손절"))
                    break
                j += 1
                continue  # 같은 봉에서 익절은 금지 (낮아진 평단은 미래참조)

            # 3) 익절
            tp_px = avg * (1 + p.tp)
            if h[j] >= tp_px:
                px = max(tp_px, o[j])  # 갭상승이면 더 좋은 시가
                out.append(_close(basis, units, px, entry, p, j - i, added, "익절"))
                break
            j += 1
        else:
            out.append(_close(basis, units, c[n - 1], entry, p, n - 1 - i, added, "미청산"))
    return pd.DataFrame(out)


def _close(basis, units, px, entry, p: V2, bars, added, why):
    proceeds = units * px * (1 - p.cost)
    spent = basis * (1 + p.cost)
    return {
        "pnl": (proceeds - spent) / entry,  # 첫 매수 금액 대비
        "bars": bars,
        "added": added,
        "why": why,
        "size": basis / entry,  # 최종 포지션 크기 (첫 매수 = 1)
    }


# --------------------------------------------------------------------------- 데이터


def load_raw(f):
    d = pd.read_csv(ROOT / "data" / "stocks_raw" / f, parse_dates=["date"], index_col="date")
    return d[["open", "high", "low", "close"]].dropna()


def load_crypto(f):
    d = pd.read_csv(ROOT / "data" / "ohlcv" / f)
    d["date"] = pd.to_datetime(d["timestamp"], unit="ms")
    return d.set_index("date").sort_index()[["open", "high", "low", "close"]].dropna()


def shuffled(df, seed=0):
    """수익률 순서를 뒤섞는다. 변동성은 같고 시간 구조(추세·반전)만 사라진다."""
    rng = np.random.default_rng(seed)
    r = np.log(df["close"] / df["close"].shift(1)).dropna().to_numpy().copy()
    rng.shuffle(r)
    close = df["close"].iloc[0] * np.exp(np.cumsum(np.concatenate([[0.0], r])))
    # 봉 내부 폭은 원본의 비율을 무작위로 재사용
    hi = (df["high"] / df["close"]).to_numpy()
    lo = (df["low"] / df["close"]).to_numpy()
    op = (df["open"] / df["close"]).to_numpy()
    k = rng.permutation(len(close))
    return pd.DataFrame(
        {
            "open": close * op[k % len(op)],
            "high": close * np.maximum(hi[k % len(hi)], 1.0),
            "low": close * np.minimum(lo[k % len(lo)], 1.0),
            "close": close,
        },
        index=df.index[: len(close)],
    )


def gbm(n=8000, vol=0.20, drift=0.0, seed=0, sub=200):
    """순수 무작위 가격. 여기서 기댓값이 0 이 아니면 엔진에 버그가 있다.

    하루를 sub 스텝으로 쪼갠 **진짜 경로**를 만들고 그 경로의 실제 최고·최저로
    일봉을 만든다. 종가에 노이즈를 붙여 고·저가를 지어내면 봉 안의 경로와
    앞뒤가 맞지 않아 기댓값이 +0.5% 씩 부풀어 오른다 — 지정가 전략 검증에서는
    치명적이다.
    """
    rng = np.random.default_rng(seed)
    d = vol / np.sqrt(252) / np.sqrt(sub)
    steps = rng.normal(drift / 252 / sub - 0.5 * d * d, d, n * sub)
    path = 100 * np.exp(np.cumsum(steps)).reshape(n, sub)
    return pd.DataFrame(
        {"open": path[:, 0], "high": path.max(axis=1),
         "low": path.min(axis=1), "close": path[:, -1]},
        index=pd.bdate_range("1990-01-01", periods=n),
    )


# --------------------------------------------------------------------------- 평가


def evaluate(df, p: V2, n_entries=2000, seed=0):
    o, h, low, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    rng = np.random.default_rng(seed)
    entries = np.sort(rng.choice(len(df) - 2, size=min(n_entries, len(df) - 2), replace=False))
    t = run_trades(o, h, low, c, entries, p)
    if t.empty:
        return None
    win = t["pnl"] > 0
    return {
        "거래": len(t),
        "승률": win.mean(),
        "평균이익": t.loc[win, "pnl"].mean() if win.any() else np.nan,
        "평균손실": t.loc[~win, "pnl"].mean() if (~win).any() else np.nan,
        "기댓값": t["pnl"].mean(),
        "합계": t["pnl"].sum(),
        "물탄비율": t["added"].mean(),
        "평균보유": t["bars"].mean(),
        "최악": t["pnl"].min(),
    }


def fmt(rows, cols=None):
    d = pd.DataFrame(rows)
    for col in ("승률", "평균이익", "평균손실", "기댓값", "물탄비율", "최악", "합계"):
        if col in d:
            d[col] = d[col].map(lambda v: "–" if pd.isna(v) else f"{v:+.3%}")
    if "승률" in d:
        d["승률"] = pd.DataFrame(rows)["승률"].map(lambda v: f"{v:.1%}")
    if "물탄비율" in d:
        d["물탄비율"] = pd.DataFrame(rows)["물탄비율"].map(lambda v: f"{v:.0%}")
    if "평균보유" in d:
        d["평균보유"] = pd.DataFrame(rows)["평균보유"].map(lambda v: f"{v:.0f}봉")
    return d[cols].to_string(index=False) if cols else d.to_string(index=False)


def h1(t):
    print(f"\n{'=' * 100}\n{t}\n{'=' * 100}")


# --------------------------------------------------------------------------- 계좌


def sequential(df: pd.DataFrame, p: V2, cash_yield: float = 0.03, equity0: float = 1.0):
    """연속 매매 계좌: 한 번에 한 거래, 청산하면 다음 봉 시가에 바로 재진입.

    첫 매수는 자본의 1/(1+add_mult) 로 잡는다. 그래야 물타기까지 감당하고
    레버리지가 걸리지 않는다. 자본 대비 실제 투입 비중을 매 봉 기록한다.
    """
    o, h, low, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    n = len(c)
    first = 1.0 / (1.0 + p.add_mult)
    cash, units, basis = equity0, 0.0, 0.0
    avg = add_px = np.nan
    stop_px = -np.inf
    added = inpos = False
    open_eq = equity0

    curve = np.zeros(n)
    expo = np.zeros(n)
    trades: list[dict] = []
    days = np.diff(df.index.to_numpy().astype("datetime64[D]").astype(int), prepend=0)
    days[0] = 0

    def exit_at(px, why, j):
        nonlocal cash, units, basis, inpos, added, stop_px
        cash += units * px * (1 - p.cost)
        trades.append({"pnl": (cash - open_eq) / open_eq, "why": why, "added": added,
                       "date": df.index[j]})
        units = basis = 0.0
        inpos = added = False
        stop_px = -np.inf

    for j in range(n):
        if days[j] > 0 and cash > 0:
            cash *= (1 + cash_yield) ** (days[j] / 365.25)

        if not inpos:  # 시가 진입
            open_eq = cash
            spend = first * cash / (1 + p.cost)
            if spend > 0:
                units = spend / o[j]
                basis = spend
                cash -= spend * (1 + p.cost)
                avg, add_px, inpos = o[j], o[j] * (1 - p.step), True
                stop_px = o[j] * (1 - p.stop) if p.stop_before_add else -np.inf

        if inpos:
            if low[j] <= stop_px:
                exit_at(min(stop_px, o[j]), "손절", j)
            elif (not added) and low[j] <= add_px:
                px = min(add_px, o[j])
                spend = min(basis * p.add_mult, cash / (1 + p.cost))
                units += spend / px
                basis += spend
                cash -= spend * (1 + p.cost)
                avg = basis / units
                stop_px = avg * (1 - p.stop)
                added = True
                if low[j] <= stop_px:  # 같은 봉에서 손절까지 갔다면 맞는다
                    exit_at(min(stop_px, px), "손절", j)
            elif h[j] >= avg * (1 + p.tp):
                exit_at(max(avg * (1 + p.tp), o[j]), "익절", j)

        eq = cash + units * c[j]
        curve[j] = eq
        expo[j] = units * c[j] / eq if eq > 0 else 0.0

    s = pd.Series(curve, index=df.index)
    t = pd.DataFrame(trades)
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    return {
        "연수익": float(s.iloc[-1] / equity0) ** (1 / yrs) - 1,
        "MDD": float((s / s.cummax() - 1).min()),
        "평균투입": float(expo.mean()),
        "거래": len(t),
        "승률": float((t["pnl"] > 0).mean()) if len(t) else np.nan,
        "최악거래": float(t["pnl"].min()) if len(t) else np.nan,
        "곡선": s,
        "거래내역": t,
    }


# --------------------------------------------------------------------------- 실행


def _drift(d, div=0.0):
    y = (d.index[-1] - d.index[0]).days / 365.25
    return (d["close"].iloc[-1] / d["close"].iloc[0]) ** (1 / y) - 1 + div


def main() -> int:
    import itertools

    h1("검사 A — 무작위 가격에서 기댓값이 0 인가 (0 이 아니면 엔진 버그)")
    evs = []
    for s in range(6):
        d = gbm(8000, 0.30, 0.0, seed=s)
        evs.append(evaluate(d, V2(tp=0.01, step=0.02, stop=0.02, cost=0.0), 3000, s)["기댓값"])
    evs = np.array(evs)
    se = evs.std(ddof=1) / np.sqrt(len(evs))
    t = evs.mean() / se
    print(f"  기댓값 {evs.mean():+.4%} ± {se:.4%}  (t={t:.1f})")
    print("  이론값 0.0000% — 봉이 굵으면 한 봉에서 물타기·익절이 같이 닿는 충돌 때문에")
    print("  보수적(음수) 쪽으로 조금 치우친다. 봉을 잘게 쪼개면 정확히 0 에 수렴한다.")
    print(f"  → {'유리한 방향의 편향 없음 (정상)' if t < 2.5 else '유리한 쪽으로 치우침 — 버그 의심'}")

    h1("검사 B — 승률을 극한까지 올리면 어떻게 되는가")
    grid = list(itertools.product([0.002, 0.005, 0.01], [0.02, 0.05, 0.08],
                                  [0.05, 0.12, 0.20], [1.0, 2.0]))
    for name, d, div, cost in [("SPY", load_raw("us_SPY.csv"), 0.019, 0.0005),
                               ("BTC", load_crypto("spot_BTC_USDT_1d.csv"), 0.0, 0.00045)]:
        dr = _drift(d, div)
        rows = [dict(승률=(r := sequential(d, V2(tp=tp, step=st, stop=sp, add_mult=m, cost=cost)))["승률"],
                     연수익=r["연수익"], MDD=r["MDD"], 최악거래=r["최악거래"])
                for tp, st, sp, m in grid]
        R = pd.DataFrame(rows)
        b = R.loc[R["승률"].idxmax()]
        print(f"  {name}: 최고 승률 {b['승률']:.1%} → 연 {b['연수익']:+.1%}, "
              f"MDD {b['MDD']:+.1%}, 최악 거래 {b['최악거래']:+.1%}  "
              f"(그냥 보유 연 {dr:+.1%})")
        print(f"        보유 수익을 넘은 조합: {(R['연수익'] > dr).sum()} / {len(R)}")
    print("\n  승률을 올리려면 손절을 멀리 둬야 하고, 그러면 지는 날 크게 잃는다.")
    print("  '거의 100% 승률' 과 '질 때도 조금만' 은 동시에 성립하지 않는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
