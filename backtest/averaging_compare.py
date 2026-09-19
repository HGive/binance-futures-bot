"""같은 물타기 전략을 시장만 바꿔가며 돌린다.

질문: "잡코인 말고 우량주·지수로 하면 되는 거 아니냐?"
답을 말이 아니라 숫자로 내기 위한 스크립트.

    ./.venv-research/bin/python backtest/averaging_compare.py
"""

from __future__ import annotations

import sys
import warnings
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from backtest.averaging import (  # noqa: E402
    OHLCV,
    STOCKS,
    AvgParams,
    Result,
    load_crypto,
    load_stock,
    simulate,
)

# 시장별 왕복 비용. 물타기는 거래가 잦아서 비용이 결과를 좌우한다.
COST = {
    "us": (0.0002, 0.0),  # 수수료 0, 슬리피지만. 양도세는 따로 다룬다.
    "kr": (0.0002, 0.0015),  # + 매도 시 거래세 0.15%
    "crypto": (0.00045, 0.0),  # 바이낸스 테이커
}

# 이름 매핑
US_NAMES = {
    "GSPC": ("S&P500", "지수"),
    "NDX": ("나스닥100", "지수"),
    "SPY": ("SPY", "지수"),
    "QQQ": ("QQQ", "지수"),
    "AAPL": ("애플", "살아남음"),
    "MSFT": ("마이크로소프트", "살아남음"),
    "JNJ": ("존슨앤존슨", "살아남음"),
    "KO": ("코카콜라", "살아남음"),
    "PG": ("P&G", "살아남음"),
    "XOM": ("엑손모빌", "살아남음"),
    "JPM": ("JP모건", "살아남음"),
    "WMT": ("월마트", "살아남음"),
    "GE": ("GE", "무너짐"),
    "C": ("씨티그룹", "무너짐"),
    "INTC": ("인텔", "무너짐"),
    "F": ("포드", "무너짐"),
    "BA": ("보잉", "무너짐"),
    "T": ("AT&T", "무너짐"),
    "PFE": ("화이자", "무너짐"),
    "NOK": ("노키아", "무너짐"),
    "VZ": ("버라이즌", "무너짐"),
    "IBM": ("IBM", "무너짐"),
    "MMM": ("3M", "무너짐"),
}
KR_NAMES = {
    "KS11": ("코스피", "지수"),
    "KQ11": ("코스닥", "지수"),
    "069500": ("KODEX200", "지수"),
    "005930": ("삼성전자", "살아남음"),
    "000660": ("SK하이닉스", "살아남음"),
    "005380": ("현대차", "살아남음"),
    "207940": ("삼성바이오", "살아남음"),
    "055550": ("신한지주", "살아남음"),
    "105560": ("KB금융", "살아남음"),
    "005490": ("POSCO", "무너짐"),
    "035420": ("NAVER", "무너짐"),
    "035720": ("카카오", "무너짐"),
    "051910": ("LG화학", "무너짐"),
    "068270": ("셀트리온", "무너짐"),
    "012330": ("현대모비스", "무너짐"),
    "017670": ("SK텔레콤", "무너짐"),
    "032830": ("삼성생명", "무너짐"),
    "066570": ("LG전자", "무너짐"),
}

# 설정 ------------------------------------------------------------------------
CONFIGS = {
    # 사용자가 하던 형태: 짧은 익절 + 촘촘한 물타기 + 금액 키우기
    "공격": AvgParams(tp=0.015, step=0.025, step_scale=1.15, mult=1.5, max_adds=8),
    # 흔한 DCA 봇 기본값에 가까운 형태
    "표준": AvgParams(tp=0.02, step=0.04, step_scale=1.2, mult=1.3, max_adds=9),
    # 폭락을 견디려고 사다리를 넓게 편 형태
    "넓게": AvgParams(tp=0.03, step=0.06, step_scale=1.25, mult=1.0, max_adds=12),
}


def bad_data(df: pd.DataFrame) -> bool:
    """액면변경(리디노미네이션) 등으로 가격이 튄 종목을 거른다."""
    r = df["close"] / df["close"].shift(1)
    return bool(((r > 5) | (r < 0.2)).any())


def load_universe() -> dict[str, tuple[pd.DataFrame, str, str]]:
    uni: dict[str, tuple[pd.DataFrame, str, str]] = {}
    for f in sorted(STOCKS.glob("us_*.csv")):
        key = f.stem[3:]
        if key not in US_NAMES:
            continue
        name, cat = US_NAMES[key]
        uni[name] = (load_stock(f.name), "us", cat)
    for f in sorted(STOCKS.glob("kr_*.csv")):
        key = f.stem[3:]
        if key not in KR_NAMES:
            continue
        name, cat = KR_NAMES[key]
        uni[name] = (load_stock(f.name), "kr", cat)
    for sym in ("BTC", "ETH"):
        p = OHLCV / f"spot_{sym}_USDT_1d.csv"
        if p.exists():
            uni[sym] = (load_crypto(p.name), "crypto", "메이저코인")
    return uni


def load_alts(limit: int | None = None) -> dict[str, pd.DataFrame]:
    """사용자가 실제로 매매하던 잡코인 집단. 상폐 종목도 포함해야 정직하다."""
    out: dict[str, pd.DataFrame] = {}
    major = {"BTC", "ETH", "BNB", "USDC", "FDUSD", "TUSD", "BUSD", "WBTC", "WBETH"}
    for f in sorted(OHLCV.glob("spot_*_USDT_1d.csv")):
        sym = f.stem.replace("spot_", "").replace("_USDT_1d", "")
        if sym in major or "UP" in sym or "DOWN" in sym:
            continue
        try:
            df = load_crypto(f.name)
        except Exception:  # noqa: BLE001
            continue
        if len(df) < 400 or bad_data(df):
            continue
        out[sym] = df
        if limit and len(out) >= limit:
            break
    return out


def run(df: pd.DataFrame, p: AvgParams, market: str, lev: float = 1.0) -> Result:
    cost, tax = COST[market]
    return simulate(df, replace(p, cost=cost, tax_sell=tax, lev=lev))


def bh_cagr(df: pd.DataFrame) -> float:
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    if yrs <= 0:
        return float("nan")
    return float(df["close"].iloc[-1] / df["close"].iloc[0]) ** (1 / yrs) - 1


def clip(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if start:
        df = df[df.index >= start]
    if end:
        df = df[df.index <= end]
    return df


def table(
    uni: dict,
    p: AvgParams,
    start: str | None = None,
    end: str | None = None,
    lev: float = 1.0,
    min_bars: int = 300,
) -> pd.DataFrame:
    rows = []
    for name, (df0, market, cat) in uni.items():
        df = clip(df0, start, end)
        if len(df) < min_bars:
            continue
        r = run(df, p, market, lev)
        rows.append(
            {
                "종목": name,
                "분류": cat,
                "시장": market,
                "기간": f"{df.index[0].year}~{df.index[-1].year}",
                "연수익": r.cagr,
                "보유시연수익": bh_cagr(df),
                "MDD": r.mdd,
                "승률": r.win_rate,
                "PF": r.pf,
                "최악거래": r.worst_trade,
                "거래수": len(r.trades),
                "물타기최대": r.max_adds_used,
                "청산": "" if r.blown is None else str(r.blown.date()),
                "물린기간": r.underwater_days,
            }
        )
    return pd.DataFrame(rows)


def fmt(df: pd.DataFrame, cols: list[str] | None = None) -> str:
    d = df.copy()
    for c in ("연수익", "보유시연수익", "MDD", "승률", "최악거래"):
        if c in d:
            d[c] = d[c].map(lambda v: "–" if pd.isna(v) else f"{v:+.1%}")
    if "PF" in d:
        d["PF"] = d["PF"].map(lambda v: "–" if pd.isna(v) else ("∞" if np.isinf(v) else f"{v:.2f}"))
    if cols:
        d = d[cols]
    return d.to_string(index=False)


def h(title: str) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")


def main() -> int:
    uni = load_universe()
    print(f"주식·메이저코인 {len(uni)}종목 로드")

    h("설정별 사다리 특성")
    for cname, p in CONFIGS.items():
        ps = p.sized(1.0)
        print(
            f"  {cname}: 익절 {p.tp:.1%} | 간격 {p.step:.1%}×{p.step_scale} | 금액배수 {p.mult} | "
            f"추가매수 {p.max_adds}회 → 감당 하락폭 {p.coverage():.1%} | 첫매수 자본의 {ps.base:.2%}"
        )

    p = CONFIGS["공격"].sized(1.0)

    # --- 1. 공통 기간, 종목만 바꾼다 ------------------------------------
    h("1. 같은 전략·같은 기간(2018-08~2026-08), 종목만 바꿨을 때  [무레버리지·설정=공격]")
    t = table(uni, p, start="2018-08-27", end="2026-08-25")
    t = t.sort_values("연수익", ascending=False)
    print(fmt(t, ["종목", "분류", "연수익", "보유시연수익", "MDD", "승률", "PF", "최악거래", "거래수", "물린기간"]))

    h("2. 잡코인 집단 — 사용자가 실제로 하던 것  [무레버리지·설정=공격]")
    alts = load_alts()
    print(f"  대상 {len(alts)}개 (상폐 포함, 액면변경 종목 제외)")
    rows = []
    for sym, df in alts.items():
        r = run(df, p, "crypto")
        rows.append(
            {
                "종목": sym,
                "연수익": r.cagr,
                "보유시연수익": bh_cagr(df),
                "MDD": r.mdd,
                "승률": r.win_rate,
                "최악거래": r.worst_trade,
                "거래수": len(r.trades),
            }
        )
    a = pd.DataFrame(rows)
    a.to_csv("/tmp/alts.csv", index=False)
    print(f"\n  잡코인 {len(a)}개 결과 분포")
    print(f"    연수익 중앙값 {a['연수익'].median():+.1%} | 평균 {a['연수익'].mean():+.1%}")
    print(f"    돈 번 비율   {(a['연수익'] > 0).mean():.0%}")
    print(f"    MDD 중앙값   {a['MDD'].median():+.1%}")
    print(f"    승률 중앙값   {a['승률'].median():.1%}")
    print(f"    최악거래 중앙값 {a['최악거래'].median():+.1%}")
    print("\n  상위 5:")
    print(fmt(a.nlargest(5, "연수익")))
    print("\n  하위 5:")
    print(fmt(a.nsmallest(5, "연수익")))

    return 0


if __name__ == "__main__":
    sys.exit(main())
