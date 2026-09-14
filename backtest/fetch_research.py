"""
펀딩비 이력 + 현물/선물 8시간봉 수집 (리서치용).

8시간봉을 쓰는 이유: 펀딩 정산 시각(00/08/16 UTC)에 봉이 닫힌다.
그래서 "정산 시점의 현물가·선물가"를 그대로 얻고, 일봉은 3개를 묶으면 된다.

저장 위치는 RESEARCH_DATA 환경변수 (기본: data/research).
    python backtest/fetch_research.py funding
    python backtest/fetch_research.py perp
    python backtest/fetch_research.py spot
"""
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("RESEARCH_DATA", os.path.join(ROOT, "data", "research"))
FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"
H8 = 8 * 3600_000
START = 1567296000000  # 2019-09-01


def get(url, params, pause):
    q = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(q, timeout=30) as r:
                data = json.loads(r.read())
            time.sleep(pause)
            return data
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")[:200]
            if e.code == 400:          # 없는 심볼
                return None
            wait = 60 if e.code in (418, 429) else 3 * (attempt + 1)
            print(f"  HTTP {e.code} {body} → {wait}s 대기", flush=True)
            time.sleep(wait)
        except Exception as e:
            print(f"  {e} → 재시도", flush=True)
            time.sleep(3 * (attempt + 1))
    return None


def perp_universe():
    info = get(FAPI + "/fapi/v1/exchangeInfo", {}, 1)
    rows = [(s["symbol"], s["status"], s.get("onboardDate", START))
            for s in info["symbols"]
            if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"]
    names = {r[0] for r in rows}
    # 상장폐지 종목: 기존 현물 일봉 캐시 이름으로 선물 이력이 남아있는지 찔러본다
    for f in glob.glob(os.path.join(ROOT, "data", "ohlcv", "spot_*_USDT_1d.csv")):
        base = os.path.basename(f)[5:-12]
        sym = base + "USDT"
        if sym not in names:
            rows.append((sym, "UNKNOWN", START))
    return rows


def fetch_funding():
    os.makedirs(os.path.join(OUT, "funding"), exist_ok=True)
    rows = perp_universe()
    print(f"선물 후보 {len(rows)}종 (현재 상장 {sum(r[1] != 'UNKNOWN' for r in rows)})", flush=True)
    got = 0
    for i, (sym, status, onboard) in enumerate(rows):
        path = os.path.join(OUT, "funding", f"{sym}.csv")
        if os.path.exists(path):
            got += 1
            continue
        out, cursor = [], max(START, int(onboard) - H8)
        while True:
            # fundingRate 엔드포인트: 5분 500회 한도 → 0.5초 간격 (응답시간 포함 약 400회)
            batch = get(FAPI + "/fapi/v1/fundingRate",
                        {"symbol": sym, "startTime": cursor, "limit": 1000}, 0.5)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 1000:
                break
            cursor = batch[-1]["fundingTime"] + 1
        if out:
            df = pd.DataFrame(out)[["fundingTime", "fundingRate", "markPrice"]]
            df["fundingRate"] = df["fundingRate"].astype(float)
            df["markPrice"] = pd.to_numeric(df["markPrice"], errors="coerce")
            df.drop_duplicates("fundingTime").to_csv(path, index=False)
            got += 1
        if i % 25 == 0:
            print(f"  {i}/{len(rows)} {sym} {len(out)}건 | 누적 {got}종", flush=True)
    print(f"완료: {got}종", flush=True)


def fetch_klines(kind):
    base_url, path_, pause = ((FAPI, "/fapi/v1/klines", 0.35) if kind == "perp"
                              else (SPOT, "/api/v3/klines", 0.15))
    os.makedirs(os.path.join(OUT, kind), exist_ok=True)
    syms = sorted(r[0] for r in perp_universe())
    if kind == "spot":
        syms = sorted({spot_symbol(s)[0] for s in syms})
    print(f"{kind} 8h 봉 {len(syms)}종", flush=True)
    for i, sym in enumerate(syms):
        path = os.path.join(OUT, kind, f"{sym}.csv")
        if os.path.exists(path):
            continue
        out, cursor = [], START
        while True:
            batch = get(base_url + path_, {"symbol": sym, "interval": "8h",
                                           "startTime": cursor, "limit": 1000}, pause)
            if not batch:
                break
            out.extend(b[:8] for b in batch)
            if len(batch) < 1000:
                break
            cursor = batch[-1][0] + H8
        if out:
            df = pd.DataFrame(out, columns=["t", "o", "h", "l", "c", "v", "ct", "qv"])
            df = df.drop(columns="ct").astype(float)
            df["t"] = df["t"].astype("int64")
            df.drop_duplicates("t").to_csv(path, index=False)
        if i % 25 == 0:
            print(f"  {i}/{len(syms)} {sym} {len(out)}봉", flush=True)
    print("완료", flush=True)


def spot_symbol(perp_sym):
    """1000PEPEUSDT → (PEPEUSDT, 1000). 현물 가격 × 배수 = 선물 가격."""
    base = perp_sym[:-4]
    for pre, mult in (("1000000", 1_000_000), ("10000", 10_000), ("1000", 1000), ("1M", 1_000_000)):
        if base.startswith(pre) and len(base) > len(pre):
            return base[len(pre):] + "USDT", mult
    return perp_sym, 1


if __name__ == "__main__":
    what = sys.argv[1]
    if what == "funding":
        fetch_funding()
    elif what in ("perp", "spot"):
        fetch_klines(what)
