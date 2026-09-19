"""바이낸스 USDⓈ-M 무기한선물 펀딩비 이력을 받아 data/funding/ 에 저장한다.

선물 롱은 펀딩비를 계속 낸다(대부분의 기간 양수 = 롱이 숏에게 지급).
현물에는 없는 비용이라 반드시 반영해야 한다.

    ./.venv-research/bin/python backtest/fetch_funding.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests

OUT = Path(__file__).resolve().parent.parent / "data" / "funding"
URL = "https://fapi.binance.com/fapi/v1/fundingRate"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "DOTUSDT", "MATICUSDT",
    "ATOMUSDT", "UNIUSDT", "ETCUSDT", "BCHUSDT", "FILUSDT", "TRXUSDT",
    "NEARUSDT", "APTUSDT",
]


def fetch(symbol: str) -> pd.DataFrame:
    rows: list[dict] = []
    start = 1568000000000  # 2019-09 무기한선물 개시 무렵
    while True:
        r = requests.get(
            URL, params={"symbol": symbol, "startTime": start, "limit": 1000}, timeout=20
        )
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch:
            break
        rows += batch
        nxt = int(batch[-1]["fundingTime"]) + 1
        if nxt <= start or len(batch) < 1000:
            break
        start = nxt
        time.sleep(0.35)  # 5분/500회 제한
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows)
    d["time"] = pd.to_datetime(d["fundingTime"], unit="ms")
    d["rate"] = d["fundingRate"].astype(float)
    return d[["time", "rate"]].drop_duplicates("time").sort_values("time")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for s in SYMBOLS:
        p = OUT / f"{s}.csv"
        if p.exists():
            print(f"  skip {s}")
            continue
        d = fetch(s)
        if d.empty:
            print(f"  빈값 {s}")
            continue
        d.to_csv(p, index=False)
        ann = d["rate"].mean() * 3 * 365
        print(f"  {s:<10} {len(d):6,}건 {d['time'].iloc[0].date()} → "
              f"{d['time'].iloc[-1].date()}  평균 연 {ann:+.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
