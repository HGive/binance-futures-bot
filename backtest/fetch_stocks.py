"""미국·한국 주식 일봉을 받아 data/stocks/ 와 data/stocks_raw/ 에 저장한다.

물타기(평단 낮추기) 전략을 코인 말고 다른 시장에서 검증하기 위한 데이터.

두 벌을 받는 이유 (docs/research_averaging_2026-09-19.md):
- `data/stocks/`     배당·분할 조정. 수익률 비교·추세추종용.
- `data/stocks_raw/` 분할만 조정, 배당 미조정. **지정가 주문 검증용.**
  지정가는 화면에 찍히는 실제 가격에 걸린다. 배당조정 가격으로 익절을
  판정하면 존재하지 않는 가격에 체결되어 답이 완전히 달라진다
  (SPY 물타기 MDD: 배당조정 -4.4% vs 배당 미조정 -37.6%).

생존 편향을 피하려고 2007 년엔 우량주였다가 무너진 종목(GE, 씨티, 인텔,
노키아…)을 반드시 같이 넣는다.

    ./.venv-research/bin/python backtest/fetch_stocks.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "stocks"
OUT_RAW = ROOT / "data" / "stocks_raw"

# 분산 추세추종 대안 검증용 (주식·채권·금·원자재·통화)
ETFS = [
    ("IWM", "미국소형"), ("EFA", "선진국"), ("EEM", "신흥국"), ("TLT", "미국장기채"),
    ("IEF", "미국중기채"), ("GLD", "금"), ("SLV", "은"), ("DBC", "원자재"),
    ("USO", "원유"), ("VNQ", "리츠"), ("UUP", "달러"), ("FXE", "유로"),
    ("FXY", "엔"), ("HYG", "하이일드"),
]

# (티커, 표시이름, 분류)
US = [
    # 지수 — 개별 종목과 달리 0 으로 가지 않는다. 물타기의 전제가 성립하는 유일한 집단.
    ("^GSPC", "S&P500", "index"),
    ("^NDX", "나스닥100", "index"),
    ("SPY", "SPY", "index"),
    ("QQQ", "QQQ", "index"),
    # 살아남은 대형주 — 지금 시점에서 고르면 생존 편향이 낀다.
    ("AAPL", "애플", "survivor"),
    ("MSFT", "마이크로소프트", "survivor"),
    ("JNJ", "존슨앤존슨", "survivor"),
    ("KO", "코카콜라", "survivor"),
    ("PG", "P&G", "survivor"),
    ("XOM", "엑손모빌", "survivor"),
    ("JPM", "JP모건", "survivor"),
    ("WMT", "월마트", "survivor"),
    # 2007 년엔 이것들도 우량주였다. 편입 시점 기준으로 고른 집단.
    ("GE", "GE", "fallen"),
    ("C", "씨티그룹", "fallen"),
    ("INTC", "인텔", "fallen"),
    ("F", "포드", "fallen"),
    ("BA", "보잉", "fallen"),
    ("T", "AT&T", "fallen"),
    ("WBA", "월그린", "fallen"),
    ("PFE", "화이자", "fallen"),
    ("NOK", "노키아", "fallen"),
    ("VZ", "버라이즌", "fallen"),
    ("IBM", "IBM", "fallen"),
    ("MMM", "3M", "fallen"),
]

KR = [
    ("KS11", "코스피", "index"),
    ("KQ11", "코스닥", "index"),
    ("069500", "KODEX200", "index"),
    ("005930", "삼성전자", "survivor"),
    ("000660", "SK하이닉스", "survivor"),
    ("005490", "POSCO홀딩스", "fallen"),
    ("005380", "현대차", "survivor"),
    ("035420", "NAVER", "fallen"),
    ("035720", "카카오", "fallen"),
    ("051910", "LG화학", "fallen"),
    ("068270", "셀트리온", "fallen"),
    ("207940", "삼성바이오로직스", "survivor"),
    ("012330", "현대모비스", "fallen"),
    ("055550", "신한지주", "survivor"),
    ("105560", "KB금융", "survivor"),
    ("017670", "SK텔레콤", "fallen"),
    ("032830", "삼성생명", "fallen"),
    ("066570", "LG전자", "fallen"),
]


def save(df: pd.DataFrame, path: Path) -> None:
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    df = df[df["close"] > 0]
    df.index.name = "date"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)


def fetch_us() -> None:
    import yfinance as yf

    items = [(t, n) for t, n, _ in US] + ETFS
    for adjust, out in ((True, OUT), (False, OUT_RAW)):
        kind = "배당조정" if adjust else "배당 미조정"
        print(f"  [{kind}] → {out.name}/")
        for ticker, name in items:
            path = out / f"us_{ticker.lstrip('^')}.csv"
            if path.exists():
                continue
            try:
                # auto_adjust=True: 배당·분할 반영 / False: 분할만 반영
                df = yf.download(
                    ticker,
                    start="1950-01-01",
                    auto_adjust=adjust,
                    progress=False,
                    threads=False,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"    ERR  {name}: {type(exc).__name__} {exc}")
                continue
            if df is None or df.empty:
                print(f"    빈값 {name}")
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [str(c).lower() for c in df.columns]
            if not all(c in df.columns for c in ("open", "high", "low", "close")):
                print(f"    컬럼부족 {name}")
                continue
            save(df, path)
            print(f"    {name:16} {len(df):6,}행 {df.index[0].date()} → {df.index[-1].date()}")
            time.sleep(0.25)


def fetch_kr() -> None:
    import FinanceDataReader as fdr

    for code, name, _cat in KR:
        path = OUT / f"kr_{code}.csv"
        if path.exists():
            print(f"  skip {name}")
            continue
        try:
            df = fdr.DataReader(code, "1990-01-01")
        except Exception as exc:  # noqa: BLE001
            print(f"  ERR  {name}: {type(exc).__name__} {exc}")
            continue
        if df is None or df.empty:
            print(f"  빈값 {name}")
            continue
        df.columns = [str(c).lower() for c in df.columns]
        if "volume" not in df.columns:
            df["volume"] = 0.0
        save(df, path)
        print(f"  {name:16} {len(df):6,}행 {df.index[0].date()} → {df.index[-1].date()}")
        time.sleep(0.3)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    OUT_RAW.mkdir(parents=True, exist_ok=True)
    print("미국:")
    fetch_us()
    print("한국:")
    fetch_kr()
    print(f"\n{OUT.name}/ {len(list(OUT.glob('*.csv')))}개, "
          f"{OUT_RAW.name}/ {len(list(OUT_RAW.glob('*.csv')))}개 저장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
