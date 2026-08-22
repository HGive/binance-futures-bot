"""
OHLCV 수집 + CSV 캐시.

STRATEGY_RULES.md 4.2: fetch_ohlcv 는 1회 최대 1000봉이므로
since 기반 페이징으로 수집하고 CSV 로 캐시한다. 매번 새로 받지 않는다.
"""
import os
import time
import pandas as pd
import ccxt

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ohlcv")
TF_MS = {"1h": 3600_000, "4h": 14400_000, "1d": 86400_000, "15m": 900_000}

_exchange = None


def get_exchange():
    global _exchange
    if _exchange is None:
        _exchange = ccxt.binanceusdm({
            "enableRateLimit": True,
            "options": {"fetchCurrencies": False},
        })
        _exchange.load_markets()
    return _exchange


def _cache_path(symbol: str, timeframe: str) -> str:
    safe = symbol.replace("/", "_").replace(":", "-")
    return os.path.join(CACHE_DIR, f"{safe}_{timeframe}.csv")


def fetch_ohlcv_paged(symbol: str, timeframe: str, since_ms: int, until_ms: int | None = None) -> pd.DataFrame:
    """since 부터 현재까지 1000봉씩 페이징 수집."""
    ex = get_exchange()
    step = TF_MS[timeframe]
    until_ms = until_ms or ex.milliseconds()
    out, cursor = [], since_ms
    while cursor < until_ms:
        for attempt in range(4):
            try:
                batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=1000)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        if not batch:
            break
        out.extend(batch)
        nxt = batch[-1][0] + step
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 1000:
            break
    if not out:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(out, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def load(symbol: str, timeframe: str = "4h", years: float = 2.5, refresh: bool = False) -> pd.DataFrame:
    """캐시가 있으면 읽고, 없으면 수집 후 저장."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(symbol, timeframe)
    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path)
    else:
        ex = get_exchange()
        since = ex.milliseconds() - int(years * 365 * 86400_000)
        df = fetch_ohlcv_paged(symbol, timeframe, since)
        if len(df):
            df.to_csv(path, index=False)
    if len(df):
        df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def liquid_universe(top_n: int = 150, min_quote_vol: float = 5_000_000) -> list[str]:
    """현재 24h 거래대금 기준 상위 심볼. (생존 편향 있음 — 리포트에 명시)"""
    ex = get_exchange()
    tickers = ex.fetch_tickers()
    rows = []
    for sym, t in tickers.items():
        m = ex.markets.get(sym)
        if not m or not m.get("swap") or m.get("quote") != "USDT" or not m.get("active") or not m.get("linear"):
            continue
        qv = t.get("quoteVolume") or 0
        if qv < min_quote_vol:
            continue
        rows.append((sym, qv))
    rows.sort(key=lambda r: -r[1])
    return [s for s, _ in rows[:top_n]]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--years", type=float, default=2.5)
    ap.add_argument("--top", type=int, default=150)
    args = ap.parse_args()

    syms = liquid_universe(args.top)
    print(f"universe: {len(syms)} symbols")
    for i, s in enumerate(syms, 1):
        try:
            df = load(s, args.timeframe, args.years)
            span = f"{df['dt'].iloc[0].date()} ~ {df['dt'].iloc[-1].date()}" if len(df) else "EMPTY"
            print(f"[{i}/{len(syms)}] {s:28s} {len(df):6d} bars  {span}", flush=True)
        except Exception as e:
            print(f"[{i}/{len(syms)}] {s:28s} FAIL {type(e).__name__}: {e}", flush=True)
