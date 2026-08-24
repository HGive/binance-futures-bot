"""급등 상위 종목의 '다음날' 5분봉만 골라서 수집."""
import os, sys, time, threading
import numpy as np
import pandas as pd
import ccxt
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.gainer_scalp import load_daily, build_panel

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scalp5m")
TF, STEP = "5m", 300_000
DAY = 86_400_000
_lock = threading.Lock()


def mk_ex():
    ex = ccxt.binance({"enableRateLimit": True, "options": {"fetchCurrencies": False}})
    ex.rateLimit = 350
    return ex


def path(sym):
    return os.path.join(OUT, f"spot_{sym}_{TF}.csv")


def existing(sym):
    p = path(sym)
    if not os.path.exists(p):
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])


def work(sym, days, ex):
    """days: 필요한 거래일(00:00 UTC ms) 목록"""
    df = existing(sym)
    have = set(df["timestamp"].values.tolist()) if len(df) else set()
    ccxt_sym = sym.replace("_", "/")
    rows, calls = [], 0
    for d0 in sorted(days):
        # 그 날 첫 봉이 이미 있으면 건너뜀
        if d0 in have:
            continue
        cursor, end = d0, d0 + DAY
        while cursor < end:
            for a in range(4):
                try:
                    b = ex.fetch_ohlcv(ccxt_sym, TF, since=cursor, limit=1000)
                    break
                except ccxt.BadSymbol:
                    return sym, 0, "badsymbol"
                except Exception as e:
                    if a == 3:
                        return sym, calls, f"err:{str(e)[:60]}"
                    time.sleep(15 * (a + 1) if "429" in str(e) else 2 * (a + 1))
            calls += 1
            if not b:
                break
            rows.extend(b)
            have.update(r[0] for r in b)
            nxt = b[-1][0] + STEP
            if nxt <= cursor:
                break
            cursor = nxt
    if rows:
        new = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        allx = pd.concat([df, new], ignore_index=True).drop_duplicates("timestamp").sort_values("timestamp")
        with _lock:
            allx.to_csv(path(sym), index=False)
    return sym, calls, "ok"


def main():
    months = int(os.environ.get("MONTHS", 24))
    topn = int(os.environ.get("TOPN", 10))
    workers = int(os.environ.get("WORKERS", 3))
    d = load_daily()
    p = build_panel(d)
    p = p[p["qv20"] >= 3e6]
    p["rk"] = p.groupby("timestamp")["chg"].rank(ascending=False, method="first")
    top = p[p["rk"] <= topn]
    cut = p["timestamp"].max() - months * 30 * DAY
    top = top[top["timestamp"] >= cut]
    # 신호일 d 의 '다음날'을 거래한다
    need = {}
    for sym, g in top.groupby("sym"):
        need[sym] = sorted(set((g["timestamp"] + DAY).astype("int64").tolist()))
    print(f"대상 심볼 {len(need)}개 / 심볼-일 {sum(len(v) for v in need.values()):,}건", flush=True)

    exs = [mk_ex() for _ in range(workers)]
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(work, s, v, exs[i % workers]): s for i, (s, v) in enumerate(need.items())}
        for f in as_completed(futs):
            sym, calls, st = f.result()
            done += 1
            if st != "ok" or done % 25 == 0:
                print(f"[{done}/{len(need)}] {sym} calls={calls} {st}", flush=True)
    print("완료", flush=True)


if __name__ == "__main__":
    main()
