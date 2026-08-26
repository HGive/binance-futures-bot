#!/usr/bin/env python3
"""
REBOUND 실행기 — 1분봉 선물, 거래량 급등 + 급락을 지정가로 받는다.

  poetry run python live/rebound_bot.py --mode testnet
  poetry run python live/rebound_bot.py --mode paper --top 30

왜 별도 프로세스인가
────────────────────────────────────────────────────────────
백테스트에서 잰 결과, **1분 늦으면 전략이 죽는다**:

    지연 0분  평균 +0.152%  PF 1.50  체결률 99%
    지연 1분  평균 -0.132%  PF 0.68  체결률 66%

그래서 폴링(REST 반복 조회)으로는 못 한다. 웹소켓으로 1분봉 마감을 받은
**그 순간** 지정가를 걸어야 한다. 일봉을 도는 live/engine.py 와는
요구사항이 근본적으로 다르므로 섞지 않는다.

주문 흐름
────────────────────────────────────────────────────────────
    봉 마감 → 신호 → 지정가 매수(메이커) → 3분 안에 안 붙으면 취소
    체결 → 익절 지정가 + 손절 스톱마켓을 **거래소에** 건다 (규칙 3.5)
    둘 중 하나 체결되면 나머지 취소 / 60분 지나면 시장가 정리

STRATEGY_RULES 3.1 — 신호와 상수는 strategies/rebound.py 가 유일한 출처.
"""
import os, sys, json, time, asyncio, argparse, logging, traceback
from collections import deque
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402  (로깅 설정 부작용)
import strategies.rebound as S  # noqa: E402
from live import ai_gate  # noqa: E402

STATE = "logs/rebound_state.json"
NEED = S.VOL_MA_BARS + S.DROP_BARS + 5      # 지표에 필요한 최소 봉 수
MIN_NOTIONAL = 5.0


class Book:
    """심볼별 1분봉 링버퍼. 마감된 봉만 들어온다."""
    def __init__(self):
        self.q = deque(maxlen=NEED + 20)
        self.last_ts = 0

    def push(self, bar):
        ts = int(bar[0])
        if ts <= self.last_ts:
            return False
        self.q.append(bar)
        self.last_ts = ts
        return True

    def ready(self):
        return len(self.q) >= NEED

    def signal(self):
        """strategies/rebound.py 의 규칙을 그대로 쓴다."""
        c = [float(b[4]) for b in self.q]
        v = [float(b[5]) for b in self.q]
        qv = [v[i] * c[i] for i in range(len(c))]
        ma = sum(qv[-S.VOL_MA_BARS:]) / S.VOL_MA_BARS
        ind = {"close": c, "qv": qv, "qv_ma": [ma] * len(c),
               "drop": [0.0] * len(c)}
        ind["drop"][-1] = c[-1] / c[-1 - S.DROP_BARS] - 1
        return S.entry_signal(ind, len(c) - 1), c[-1]


class Bot:
    def __init__(self, mode, top, seed, max_conc, usdt):
        self.mode = mode
        self.dry = (mode == "paper")
        self.top, self.seed, self.max_conc = top, seed, max_conc
        self.fixed_usdt = usdt
        # 실서버 선물 스트림(fstream.binance.com)은 회선에 따라 연결만 되고 데이터가 안 온다.
        # 데모 스트림(fstream.binancefuture.com)은 같은 시세를 정상으로 보낸다.
        # 그래서 paper 도 데모 주소를 쓴다 — 주문은 어차피 안 나간다.
        self.ex = config.make_exchange("futures", testnet=(mode != "live"),
                                       use_pro=True, need_keys=not self.dry)
        self.books = {}
        self.lags, self.nbar, self.beat = [], 0, 30
        self.d = {"positions": {}, "pending": {}, "history": [], "cash": seed}
        if os.path.exists(STATE):
            try:
                self.d.update(json.load(open(STATE, encoding="utf-8")))
            except Exception as e:
                logging.error(f"상태 파일 손상: {e} — 새로 시작")
        for k in ("positions", "pending", "history"):
            self.d.setdefault(k, {} if k != "history" else [])

    def save(self):
        self.d["updated"] = datetime.now(timezone.utc).isoformat()
        tmp = STATE + ".tmp"
        json.dump(self.d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        os.replace(tmp, STATE)

    # ── 유니버스 ────────────────────────────────────────
    async def universe(self):
        mk = await self.ex.load_markets()
        tk = await self.ex.fetch_tickers()
        rows = []
        for sym, m in mk.items():
            if not (m.get("swap") and m.get("linear") and m.get("active")):
                continue
            if m.get("quote") != "USDT" or m.get("settle") != "USDT":
                continue
            t = tk.get(sym) or {}
            qv = t.get("quoteVolume") or 0
            if qv < S.MIN_QV_MA * 60 * 24:          # 하루 거래대금 하한
                continue
            rows.append((qv, sym))
        rows.sort(reverse=True)
        syms = [s for _, s in rows[:self.top]]
        logging.info(f"유니버스 {len(syms)}종 (거래대금 상위)")
        return syms

    async def prime(self, syms):
        """웹소켓만으로는 60봉이 쌓일 때까지 1시간 걸린다. REST 로 미리 채운다."""
        for sym in syms:
            try:
                o = await self.ex.fetch_ohlcv(sym, "1m", limit=NEED + 10)
                b = Book()
                for bar in o[:-1]:                   # 마지막은 미완성 봉
                    b.push(bar)
                self.books[sym] = b
            except Exception as e:
                logging.warning(f"{sym} 초기 적재 실패: {e}")
            await asyncio.sleep(self.ex.rateLimit / 1000)
        logging.info(f"초기 적재 완료 {len(self.books)}종")

    # ── 자금 ────────────────────────────────────────────
    async def equity(self):
        if self.dry:
            return self.d["cash"]
        b = await self.ex.fetch_balance()
        return float(b.get("USDT", {}).get("total") or 0)

    def budget(self, eq):
        if self.fixed_usdt:
            return self.fixed_usdt
        return max(eq * 0.10, MIN_NOTIONAL)

    # ── 진입 ────────────────────────────────────────────
    async def try_enter(self, sym, price):
        if sym in self.d["positions"] or sym in self.d["pending"]:
            return
        if len(self.d["positions"]) + len(self.d["pending"]) >= self.max_conc:
            return
        g = ai_gate.read("rebound")
        if not g["allow_new"]:
            logging.info(f"{sym} 신호 — 게이트가 막았다: {g['reason']}")
            return
        eq = await self.equity()
        notional = self.budget(eq) * g["size_mult"] * S.LEVERAGE
        if notional < MIN_NOTIONAL:
            logging.error(f"시드 부족 — 명목 {notional:.2f} USDT 는 최소주문 미달. 진입 안 함.")
            return
        limit = S.entry_price({"close": [price]}, 0)
        qty = notional / limit

        if self.dry:
            oid = f"paper-{int(time.time()*1000)}"
        else:
            try:
                await self.ex.set_leverage(S.LEVERAGE, sym)
            except Exception as e:
                logging.warning(f"{sym} 레버리지 설정 실패(무시): {e}")
            qty = float(self.ex.amount_to_precision(sym, qty))
            limit = float(self.ex.price_to_precision(sym, limit))
            try:
                o = await self.ex.create_order(sym, "limit", "buy", qty, limit,
                                               {"postOnly": True})
                oid = o["id"]
            except Exception as e:
                logging.error(f"{sym} 매수 주문 실패: {e}")
                return
        self.d["pending"][sym] = dict(id=oid, price=limit, qty=qty,
                                      placed=int(time.time()), bars=0)
        self.save()
        logging.info(f"[진입주문] {sym} 지정가 {limit} × {qty} (명목 {qty*limit:.2f} USDT)")

    # ── 대기 주문 점검 (봉마다) ──────────────────────────
    async def check_pending(self, sym, book):
        p = self.d["pending"].get(sym)
        if not p:
            return
        p["bars"] += 1
        low = float(book.q[-1][3])
        filled = False
        if self.dry:
            filled = low <= p["price"]
        else:
            try:
                o = await self.ex.fetch_order(p["id"], sym)
                filled = o.get("status") == "closed" or (o.get("filled") or 0) > 0
                if filled:
                    p["qty"] = float(o.get("filled") or p["qty"])
                    p["price"] = float(o.get("average") or p["price"])
            except Exception as e:
                logging.warning(f"{sym} 주문 조회 실패: {e}")
                return
        if filled:
            del self.d["pending"][sym]
            await self.on_fill(sym, p["price"], p["qty"])
        elif p["bars"] >= S.ENTRY_WAIT_BARS:
            if not self.dry:
                try:
                    await self.ex.cancel_order(p["id"], sym)
                except Exception as e:
                    logging.warning(f"{sym} 취소 실패: {e}")
            del self.d["pending"][sym]
            self.save()
            logging.info(f"[미체결취소] {sym} {S.ENTRY_WAIT_BARS}분 안에 안 붙었다")

    async def on_fill(self, sym, entry, qty):
        tp, sl = S.exit_prices(entry)
        ids = {}
        if not self.dry:
            tp = float(self.ex.price_to_precision(sym, tp))
            sl = float(self.ex.price_to_precision(sym, sl))
            try:   # 익절 지정가 (메이커)
                o = await self.ex.create_order(sym, "limit", "sell", qty, tp,
                                               {"reduceOnly": True})
                ids["tp"] = o["id"]
            except Exception as e:
                logging.error(f"{sym} 익절 주문 실패: {e}")
            try:   # 손절 스톱마켓 — 규칙 3.5, 거래소에 건다
                o = await self.ex.create_order(sym, "STOP_MARKET", "sell", qty, None,
                                               {"stopPrice": sl, "reduceOnly": True})
                ids["sl"] = o["id"]
            except Exception as e:
                logging.error(f"{sym} 손절 주문 실패: {e} — 즉시 시장가 정리한다")
                await self.close(sym, entry, qty, "NO_STOP")
                return
        self.d["positions"][sym] = dict(entry=entry, qty=qty, tp=tp, sl=sl,
                                        ids=ids, bars=0, opened=int(time.time()))
        if self.dry:
            self.d["cash"] -= qty * entry / S.LEVERAGE
        self.save()
        logging.info(f"[체결] {sym} @ {entry} × {qty} | 익절 {tp} 손절 {sl}")

    # ── 보유 점검 (봉마다) ──────────────────────────────
    async def check_position(self, sym, book):
        p = self.d["positions"].get(sym)
        if not p:
            return
        p["bars"] += 1
        bar = book.q[-1]
        hi, lo, cl = float(bar[2]), float(bar[3]), float(bar[4])

        if self.dry:
            if lo <= p["sl"]:
                return await self.settle(sym, p["sl"], "SL")
            if hi >= p["tp"]:
                return await self.settle(sym, p["tp"], "TP")
        else:
            for tag in ("tp", "sl"):
                oid = p["ids"].get(tag)
                if not oid:
                    continue
                try:
                    o = await self.ex.fetch_order(oid, sym)
                except Exception:
                    continue
                if o.get("status") == "closed":
                    px = float(o.get("average") or (p["tp"] if tag == "tp" else p["sl"]))
                    await self.cancel_rest(sym, p, skip=tag)
                    return await self.settle(sym, px, tag.upper())

        if p["bars"] >= S.MAX_HOLD_BARS:
            await self.cancel_rest(sym, p)
            await self.close(sym, cl, p["qty"], "TIME")

    async def cancel_rest(self, sym, p, skip=None):
        if self.dry:
            return
        for tag, oid in (p.get("ids") or {}).items():
            if tag == skip:
                continue
            try:
                await self.ex.cancel_order(oid, sym)
            except Exception:
                pass

    async def close(self, sym, price, qty, reason):
        if not self.dry:
            try:
                await self.ex.create_order(sym, "market", "sell", qty,
                                           None, {"reduceOnly": True})
            except Exception as e:
                logging.error(f"{sym} 정리 실패: {e}")
                return
        await self.settle(sym, price, reason)

    async def settle(self, sym, price, reason):
        p = self.d["positions"].pop(sym, None)
        if not p:
            return
        ret = price / p["entry"] - 1
        if self.dry:
            self.d["cash"] += p["qty"] * p["entry"] / S.LEVERAGE + p["qty"] * (price - p["entry"])
        self.d["history"].append(dict(sym=sym, entry=p["entry"], exit=price, ret=ret,
                                      reason=reason, bars=p["bars"],
                                      at=datetime.now(timezone.utc).isoformat()))
        self.d["history"] = self.d["history"][-500:]
        self.save()
        logging.info(f"[청산] {sym} {reason} {ret*100:+.2f}% (자본기준 {ret*S.LEVERAGE*100:+.2f}%)")

    # ── 메인 루프 ───────────────────────────────────────
    async def run(self):
        syms = await self.universe()
        await self.prime(syms)
        logging.info(f"REBOUND v{S.VERSION} 시작 — 모드 {self.mode} / "
                     f"레버리지 {S.LEVERAGE}배 / 동시 {self.max_conc}칸")
        while True:
            try:
                bars = await self.ex.watch_ohlcv_for_symbols(
                    [[s, "1m"] for s in syms])
                for sym, tfs in bars.items():
                    book = self.books.get(sym)
                    if book is None:
                        continue
                    for bar in tfs.get("1m", []):
                        # 마감된 봉만 — 현재 분에 속한 봉은 아직 안 끝났다
                        if int(bar[0]) >= self.ex.milliseconds() // 60000 * 60000:
                            continue
                        if not book.push(bar):
                            continue
                        # 지연 측정 — 이 전략의 생사가 걸린 숫자다.
                        # 봉 마감(=봉시각+60초) 부터 지금까지 몇 초인가.
                        lag = (self.ex.milliseconds() - (int(bar[0]) + 60_000)) / 1000
                        self.lags.append(lag)
                        self.nbar += 1
                        if self.nbar % self.beat == 0:
                            L = sorted(self.lags)[-500:]
                            logging.info(f"[박동] 봉 {self.nbar}건 처리 | 지연 중앙값 "
                                         f"{L[len(L)//2]:.1f}초 / 최대 {max(L):.1f}초 | "
                                         f"보유 {len(self.d['positions'])} 대기 {len(self.d['pending'])}")
                            self.lags = self.lags[-500:]
                        await self.check_pending(sym, book)
                        await self.check_position(sym, book)
                        if book.ready():
                            hit, px = book.signal()
                            if hit:
                                logging.info(f"[신호] {sym} @ {px}")
                                await self.try_enter(sym, px)
            except KeyboardInterrupt:
                break
            except Exception:
                logging.error("루프 예외:\n" + traceback.format_exc())
                await asyncio.sleep(5)
        await self.ex.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["paper", "testnet", "live"], default="paper")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--seed", type=float, default=1000.0)
    ap.add_argument("--max-conc", type=int, default=3)
    ap.add_argument("--usdt", type=float, default=None, help="1건당 증거금 고정 (시드가 적을 때)")
    ap.add_argument("--i-know", action="store_true")
    a = ap.parse_args()
    if a.mode == "live" and not a.i_know:
        print("REBOUND 는 아직 v0.x 다. 실전은 --i-know 를 붙여라."); sys.exit(1)
    os.environ.setdefault("LOG_FILENAME", f"rebound_{a.mode}.log")
    asyncio.run(Bot(a.mode, a.top, a.seed, a.max_conc, a.usdt).run())


if __name__ == "__main__":
    main()
