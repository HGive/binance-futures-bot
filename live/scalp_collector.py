#!/usr/bin/env python3
"""
스캘핑용 실시간 데이터 수집기.

봉(OHLCV)에서 뽑은 신호 14개는 전부 기각됐다 (docs/backtest_scalp_screen.md).
남은 정보원은 **봉에 안 담기는 것** 뿐이다:

  호가 불균형   매수호가 총량 vs 매도호가 총량
  호가 두께     상위 N호가에 쌓인 금액, 스프레드
  체결 방향     최근 체결 중 매수 체결 비중 (테이커 매수 압력)
  체결 크기     대형 체결(고래) 비중
  호가 변화율   호가가 얼마나 빨리 소진/보충되는가

이건 과거 데이터를 살 수 없다. 지금부터 쌓아야 한다.

  10초마다 스냅샷을 찍고, 그 시점의 미래 수익률(1/5/15/30/60분 뒤)을 나중에 채운다.
  하루만 돌려도 심볼당 8,640개 관측이 쌓인다.

사용:
  python live/scalp_collector.py --top 20 --hours 24
  python live/scalp_collector.py --analyze data/flow/flow_20260824.csv
"""
import os, sys, time, json, argparse, logging, asyncio, traceback
from datetime import datetime, timezone
from collections import deque, defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "flow")
SNAP_SEC = 10
DEPTH_LEVELS = 20
TRADE_WINDOW = 60          # 체결 통계를 볼 최근 초


def now_ms():
    return int(time.time() * 1000)


class SymbolState:
    def __init__(self, sym):
        self.sym = sym
        self.trades = deque()      # (ts, price, amount, is_buy)
        self.book = None
        self.last_mid = None

    def add_trades(self, ts_list):
        self.trades.extend(ts_list)
        cut = now_ms() - TRADE_WINDOW * 1000
        while self.trades and self.trades[0][0] < cut:
            self.trades.popleft()

    def snapshot(self):
        if not self.book:
            return None
        bids = self.book.get("bids") or []
        asks = self.book.get("asks") or []
        if not bids or not asks:
            return None
        bb, ba = bids[0][0], asks[0][0]
        mid = (bb + ba) / 2
        if mid <= 0:
            return None
        bid_usd = sum(p * q for p, q in bids[:DEPTH_LEVELS])
        ask_usd = sum(p * q for p, q in asks[:DEPTH_LEVELS])
        # 중간가 ±0.1% / ±0.5% 안에 쌓인 금액 (가까운 벽)
        near_b = sum(p * q for p, q in bids if p >= mid * 0.999)
        near_a = sum(p * q for p, q in asks if p <= mid * 1.001)
        far_b = sum(p * q for p, q in bids if p >= mid * 0.995)
        far_a = sum(p * q for p, q in asks if p <= mid * 1.005)

        tr = list(self.trades)
        buy_usd = sum(p * a for _, p, a, b in tr if b)
        sell_usd = sum(p * a for _, p, a, b in tr if not b)
        tot = buy_usd + sell_usd
        sizes = [p * a for _, p, a, _ in tr]
        big = np.percentile(sizes, 90) if len(sizes) >= 10 else 0.0
        big_usd = sum(s for s in sizes if s >= big) if big > 0 else 0.0

        return dict(
            ts=now_ms(), sym=self.sym, mid=mid,
            spread=(ba - bb) / mid,
            book_imb=(bid_usd - ask_usd) / (bid_usd + ask_usd) if bid_usd + ask_usd else 0.0,
            near_imb=(near_b - near_a) / (near_b + near_a) if near_b + near_a else 0.0,
            far_imb=(far_b - far_a) / (far_b + far_a) if far_b + far_a else 0.0,
            depth_usd=bid_usd + ask_usd,
            near_depth=near_b + near_a,
            trade_imb=(buy_usd - sell_usd) / tot if tot else 0.0,
            trade_usd=tot,
            n_trades=len(tr),
            big_share=big_usd / tot if tot else 0.0,
        )


async def watch_symbol(ex, st, stop):
    async def book_loop():
        while not stop.is_set():
            try:
                st.book = await ex.watch_order_book(st.sym, limit=50)
            except Exception as e:
                logging.warning(f"[{st.sym}] 호가 끊김: {type(e).__name__}: {e}")
                await asyncio.sleep(3)

    async def trade_loop():
        while not stop.is_set():
            try:
                ts = await ex.watch_trades(st.sym)
                st.add_trades([(t["timestamp"] or now_ms(), t["price"], t["amount"],
                                t["side"] == "buy") for t in ts])
            except Exception as e:
                logging.warning(f"[{st.sym}] 체결 끊김: {type(e).__name__}: {e}")
                await asyncio.sleep(3)

    await asyncio.gather(book_loop(), trade_loop())


async def collect(symbols, hours, market, testnet):
    import config
    # 시세·호가는 공개 데이터라 키가 필요 없고, 실서버가 데모보다 호가가 진짜다.
    ex = config.make_exchange(market, testnet=False, use_pro=True, need_keys=False)
    await ex.load_markets()
    states = {s: SymbolState(s) for s in symbols}
    stop = asyncio.Event()
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"flow_{datetime.now(timezone.utc):%Y%m%d_%H%M}.csv")
    logging.info(f"수집 시작 — {len(symbols)}종, {hours}시간, {SNAP_SEC}초 간격 → {path}")

    tasks = [asyncio.create_task(watch_symbol(ex, s, stop)) for s in states.values()]
    end = time.time() + hours * 3600
    rows, wrote, last_flush, live = [], 0, time.time(), set()
    try:
        while time.time() < end:
            await asyncio.sleep(SNAP_SEC)
            for s in states.values():
                snap = s.snapshot()
                if snap:
                    rows.append(snap); live.add(s.sym)
            # 건수뿐 아니라 시간으로도 저장한다.
            # 호가를 안 주는 심볼이 섞이면 건수 기준만으로는 한참 안 쌓인다
            # (토큰화 주식류가 그랬다 — 25종 중 일부만 살아서 파일이 안 생겼다)
            if len(rows) >= 500 or time.time() - last_flush > 60:
                if rows:
                    pd.DataFrame(rows).to_csv(path, mode="a", header=(wrote == 0), index=False)
                    wrote += len(rows); rows = []
                    logging.info(f"  {wrote:,}건 저장 / 살아있는 심볼 {len(live)}/{len(states)}"
                                 f"  (남은 시간 {(end-time.time())/3600:.1f}h)")
                last_flush = time.time()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logging.info("중단")
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        if rows:
            pd.DataFrame(rows).to_csv(path, mode="a", header=(wrote == 0), index=False)
            wrote += len(rows)
        try:
            await ex.close()
        except Exception:
            pass
        logging.info(f"총 {wrote:,}건 저장 → {path}")
    return path


FEATS = ["book_imb", "near_imb", "far_imb", "trade_imb", "big_share",
         "spread", "depth_usd", "near_depth", "trade_usd", "n_trades"]


def analyze(path, horizons=(6, 30, 90, 180, 360)):
    """스냅샷마다 미래 수익률을 붙여서, 어떤 신호가 방향을 맞히는지 본다.
    horizons 는 스냅샷 개수 (10초 간격이므로 6=1분, 30=5분, 90=15분, 180=30분, 360=60분)."""
    df = pd.read_csv(path)
    if not len(df):
        print("데이터 없음"); return
    df = df.sort_values(["sym", "ts"]).reset_index(drop=True)
    print(f"{len(df):,}건 / {df.sym.nunique()}종 / "
          f"{pd.to_datetime(df.ts.min(),unit='ms')} ~ {pd.to_datetime(df.ts.max(),unit='ms')}\n")
    for hz in horizons:
        df[f"fwd{hz}"] = df.groupby("sym")["mid"].shift(-hz) / df["mid"] - 1
    for hz in horizons:
        col = f"fwd{hz}"
        g = df.dropna(subset=[col])
        if len(g) < 500:
            continue
        # 시장 전체 흐름 제거 (같은 시각 전 종목 평균)
        g = g.copy()
        g["bucket"] = g.ts // 60_000
        g["rel"] = g[col] - g.groupby("bucket")[col].transform("mean")
        print(f"--- {hz*SNAP_SEC/60:.0f}분 뒤 (시장 대비 초과), n={len(g):,} ---")
        for f in FEATS:
            try:
                q = pd.qcut(g[f], 5, labels=False, duplicates="drop")
            except Exception:
                continue
            lo = g.rel[q == 0].mean() * 100
            hi = g.rel[q == q.max()].mean() * 100
            spread = hi - lo
            if abs(spread) < 0.02:
                continue
            print(f"  {f:<12} 하위20% {lo:>+7.3f}%   상위20% {hi:>+7.3f}%   차이 {spread:>+7.3f}%p")
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--market", choices=["spot", "futures"], default="futures",
                    help="스캘핑은 선물이 맞다 — 메이커 0.02% vs 현물 0.10%")
    ap.add_argument("--testnet", action="store_true")
    ap.add_argument("--symbols", default=None, help="쉼표로 직접 지정")
    ap.add_argument("--analyze", default=None, help="수집된 csv 분석")
    a = ap.parse_args()

    os.environ.setdefault("LOG_FILENAME", "scalp_collector.log")
    if a.analyze:
        analyze(a.analyze); return

    import config
    from backtest import data as D
    D.MARKET = a.market
    if a.symbols:
        syms = a.symbols.split(",")
    else:
        syms = D.liquid_universe(a.top * 2, 50_000_000)
        # 토큰화 주식·상품(XAU, SOXL, MU, CL …)은 크립토 스캘핑 대상이 아니고
        # 호가 스트림도 안 오는 경우가 많다. 이름으로 거르지 말고 실제로 살아있는지는
        # 로그의 '살아있는 심볼' 로 확인한다.
        STOCKS = {"XAU","XAG","SOXL","SPCX","MU","CL","KORU","SNXX","SNDK",
                  "SKHYNIX","SKHY","AAPL","BABA","TSLA","NVDA","AAOI","SAMSUNG"}
        syms = [s for s in syms if s.split("/")[0] not in STOCKS][:a.top]
    logging.info(f"대상: {syms}")
    asyncio.run(collect(syms, a.hours, a.market, a.testnet))


if __name__ == "__main__":
    main()
