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
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3600_000,
         "4h": 14400_000, "1d": 86400_000}

_exchange = None
MARKET = "futures"          # "futures" (USDⓈ-M 무기한) | "spot"


def get_exchange():
    global _exchange
    if _exchange is None:
        cls = ccxt.binance if MARKET == "spot" else ccxt.binanceusdm
        _exchange = cls({
            "enableRateLimit": True,
            "options": {"fetchCurrencies": False},
        })
        # klines(limit=1000) 는 요청당 weight 10, IP 한도 분당 2400.
        # 300ms 간격이면 분당 200요청 = weight 2000 으로 여유가 있다.
        _exchange.rateLimit = 300
        _exchange.load_markets()
    return _exchange


def _cache_path(symbol: str, timeframe: str) -> str:
    safe = symbol.replace("/", "_").replace(":", "-")
    tag = "spot_" if MARKET == "spot" else ""
    return os.path.join(CACHE_DIR, f"{tag}{safe}_{timeframe}.csv")


def fetch_ohlcv_paged(symbol: str, timeframe: str, since_ms: int, until_ms: int | None = None, ex=None) -> pd.DataFrame:
    """since 부터 현재까지 1000봉씩 페이징 수집."""
    ex = ex or get_exchange()
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
                # 429(요청 초과)는 넉넉히 쉬었다 재시도
                time.sleep(15 * (attempt + 1) if "429" in str(e) else 2 * (attempt + 1))
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


def load(symbol: str, timeframe: str = "4h", years: float = 2.5, refresh: bool = False,
         ex=None, topup: bool = False) -> pd.DataFrame:
    """캐시가 있으면 읽고, 없으면 수집 후 저장.

    topup=True 면 캐시 마지막 봉 이후를 이어받아 최신으로 만든다.
    실전 봇은 반드시 topup=True 로 쓴다 — 안 그러면 옛날 데이터로 매매한다.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(symbol, timeframe)
    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path)
        if topup and len(df):
            ex = ex or get_exchange()
            step = TF_MS[timeframe]
            last = int(df["timestamp"].iloc[-1])
            if ex.milliseconds() - last > step:          # 마지막 봉 이후가 남아 있다
                new = fetch_ohlcv_paged(symbol, timeframe, last, ex=ex)
                if len(new):
                    df = (pd.concat([df, new], ignore_index=True)
                            .drop_duplicates("timestamp", keep="last")
                            .sort_values("timestamp").reset_index(drop=True))
                    df.to_csv(path, index=False)
    else:
        ex = ex or get_exchange()
        since = ex.milliseconds() - int(years * 365 * 86400_000)
        df = fetch_ohlcv_paged(symbol, timeframe, since, ex=ex)
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
        if not m or m.get("quote") != "USDT" or not m.get("active"):
            continue
        if MARKET == "spot":
            if not m.get("spot"):
                continue
        elif not m.get("swap") or not m.get("linear"):
            continue
        qv = t.get("quoteVolume") or 0
        if qv < min_quote_vol:
            continue
        rows.append((sym, qv))
    rows.sort(key=lambda r: -r[1])
    return [s for s, _ in rows[:top_n]]


def _worker_exchange():
    """스레드마다 별도 인스턴스 — ccxt 인스턴스는 스레드 안전하지 않다."""
    cls = ccxt.binance if MARKET == "spot" else ccxt.binanceusdm
    ex = cls({"enableRateLimit": True, "options": {"fetchCurrencies": False}})
    # klines(limit=1000) 는 요청당 weight 10, IP 한도는 분당 2400.
    # 워커 수 x (1000/rateLimit) x 10 이 2400 을 넘지 않게 잡는다.
    ex.rateLimit = int(os.environ.get("RATELIMIT", 400))
    ex.load_markets()
    return ex


if __name__ == "__main__":
    import argparse
    from concurrent.futures import ThreadPoolExecutor
    import threading

    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--years", type=float, default=2.5)
    ap.add_argument("--top", type=int, default=150)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--market", choices=["futures", "spot"], default="futures")
    ap.add_argument("--min-vol", type=float, default=5_000_000)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    MARKET = args.market

    globals()["MARKET"] = args.market
    syms = liquid_universe(args.top, args.min_vol)
    print(f"universe: {len(syms)} {args.market} symbols, workers={args.workers}", flush=True)
    local = threading.local()
    done = [0]
    lock = threading.Lock()

    def job(item):
        i, sym = item
        try:
            if not hasattr(local, "ex"):
                local.ex = _worker_exchange()
            df = load(sym, args.timeframe, args.years, refresh=args.refresh, ex=local.ex)
            span = f"{df['dt'].iloc[0].date()} ~ {df['dt'].iloc[-1].date()}" if len(df) else "EMPTY"
            msg = f"{sym:28s} {len(df):6d} bars  {span}"
        except Exception as e:
            msg = f"{sym:28s} FAIL {type(e).__name__}: {e}"
        with lock:
            done[0] += 1
            print(f"[{done[0]}/{len(syms)}] {msg}", flush=True)

    if args.workers <= 1:
        for it in enumerate(syms, 1):
            job(it)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(job, enumerate(syms, 1)))
