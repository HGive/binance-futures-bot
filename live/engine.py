#!/usr/bin/env python3
"""
전략 실행 엔진 — 어댑터를 갈아끼워서 여러 전략을 같은 코드로 돌린다.

  진입   일봉 마감(UTC 00:00) 직후 스캔 → 시장가 매수
  청산   어댑터가 정한다.
         OCO 를 쓰는 전략(HIBERNATE)은 익절·손절을 진입 즉시 거래소에 건다.
         트레일링이 매일 움직이는 전략(SURFER)은 손절만 걸고 매일 갱신한다.
  상태   logs/<전략>_<모드>.json — 재시작해도 이어서 돈다

STRATEGY_RULES 3.5 — 손절은 가능한 한 거래소에 걸어둔다. 봇이 죽으면 코드 손절도 죽는다.
"""
import os, sys, json, time, argparse, logging, traceback
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from backtest import data as D
from live import ai_gate, adapters, sizing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "logs")
DAY_MS = 86_400_000
STOP_LIMIT_GAP = 0.02
SLIPPAGE = 0.0005


def drop_incomplete(df, step_ms=DAY_MS):
    """진행 중인 마지막 봉을 버린다 — 백테스트는 '그날 종가' 로 판정한다."""
    if not len(df):
        return df
    now = int(pd.Timestamp.utcnow().timestamp() * 1000)
    if int(df["timestamp"].iloc[-1]) + step_ms > now:
        return df.iloc[:-1].reset_index(drop=True)
    return df


def bars_age(df) -> int:
    last = pd.Timestamp(df["dt"].iloc[-1])
    now = pd.Timestamp.utcnow()
    now = now.tz_localize(None) if last.tzinfo is None else (
        now if now.tzinfo else now.tz_localize("UTC"))
    return int((now - last).total_seconds() // 86400)


class State:
    def __init__(self, path):
        self.path = path
        self.d = {"positions": {}, "history": [], "halted": False, "updated": None}
        if os.path.exists(path):
            try:
                self.d = json.load(open(path, encoding="utf-8"))
            except Exception as e:
                logging.error(f"상태 파일 손상: {e} — 백업 후 새로 시작")
                os.rename(path, path + f".broken.{int(time.time())}")
        self.d.setdefault("positions", {}); self.d.setdefault("history", [])
        self.d.setdefault("halted", False)

    def save(self):
        self.d["updated"] = datetime.now(timezone.utc).isoformat()
        tmp = self.path + ".tmp"
        json.dump(self.d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @property
    def pos(self):
        return self.d["positions"]


class Engine:
    def __init__(self, strategy, mode, seed=1000.0, top=400, quote="USDT", shared=None):
        self.A = adapters.get(strategy)
        self.mode = mode
        self.dry = (mode == "paper")
        self.top, self.quote, self.seed = top, quote, seed
        self.shared = shared if shared is not None else {}     # 여러 전략이 일봉을 공유
        self.data_ex = config.public_exchange("spot")
        self.data_ex.load_markets()
        self.ex = None
        if not self.dry:
            self.ex = config.make_exchange(self.A.market, testnet=(mode == "testnet"),
                                           use_pro=False, need_keys=True)
            self.ex.load_markets()
        self.state = State(os.path.join(STATE_DIR, f"{self.A.key}_{mode}.json"))
        if self.dry and "paper_cash" not in self.state.d:
            self.state.d["paper_cash"] = seed
            self.state.save()
        self.daily, self.pre = {}, {}

    # ── 데이터 ────────────────────────────────────────────
    def universe(self):
        src = self.ex if self.ex else self.data_ex
        try:
            tickers = src.fetch_tickers()
        except Exception as e:
            logging.warning(f"tickers 실패({e}) — 실서버로 대체")
            tickers = self.data_ex.fetch_tickers()
        rows = []
        for sym, m in src.markets.items():
            if not (m.get("spot") and m.get("active") and m.get("quote") == self.quote):
                continue
            rows.append((sym, (tickers.get(sym) or {}).get("quoteVolume") or 0))
        rows.sort(key=lambda r: -r[1])
        return [s for s, _ in rows[: self.top]]

    def refresh(self, syms):
        D.MARKET = "spot"
        ok, fail = 0, []
        for i, sym in enumerate(syms, 1):
            if sym in self.shared:
                self.daily[sym] = self.shared[sym]; ok += 1; continue
            try:
                df = drop_incomplete(D.load(sym, "1d", years=3.0, ex=self.data_ex, topup=True))
                if len(df) < self.A.min_bars:
                    continue
                if "qv" not in df:
                    df["qv"] = df["volume"] * df["close"]
                df = df.reset_index(drop=True)
                self.daily[sym] = df
                self.shared[sym] = df
                ok += 1
            except Exception as e:
                fail.append(f"{sym}:{type(e).__name__}")
            if i % 100 == 0:
                logging.info(f"  일봉 {i}/{len(syms)} …")
        logging.info(f"일봉 준비 {ok}종 (실패 {len(fail)})")

    def compute(self):
        self.pre = {}
        for sym, df in self.daily.items():
            try:
                self.pre[sym] = self.A.precompute(df)
            except Exception as e:
                logging.debug(f"[{sym}] precompute 실패: {e}")

    # ── 계좌 ──────────────────────────────────────────────
    def equity(self):
        if self.dry:
            eq = self.state.d.get("paper_cash", self.seed)
            for sym, p in self.state.pos.items():
                eq += p["qty"] * (self.last_price(sym) or p["entry"])
            return eq
        bal = self.ex.fetch_balance()
        total = float(bal.get(self.quote, {}).get("total") or 0)
        for sym, p in self.state.pos.items():
            q = float(bal.get(sym.split("/")[0], {}).get("total") or 0)
            total += q * (self.last_price(sym) or p["entry"])
        return total

    def free_usdt(self):
        if self.dry:
            return self.state.d.get("paper_cash", self.seed)
        return float(self.ex.fetch_balance().get(self.quote, {}).get("free") or 0)

    def last_price(self, sym):
        df = self.daily.get(sym)
        return float(df["close"].iloc[-1]) if df is not None and len(df) else None

    def live_price(self, sym):
        src = self.ex if self.ex else self.data_ex
        try:
            return float(src.fetch_ticker(sym)["last"])
        except Exception:
            return self.last_price(sym)

    # ── 주문 ──────────────────────────────────────────────
    def _mkt(self, sym):
        return (self.ex if self.ex else self.data_ex).market(sym)

    def _fits(self, sym, amount, price):
        lim = self._mkt(sym).get("limits", {})
        amin = (lim.get("amount") or {}).get("min")
        cmin = (lim.get("cost") or {}).get("min")
        if amin and amount < amin:
            return False, f"수량 최소 {amin} 미달"
        if cmin and amount * price < cmin:
            return False, f"주문금액 {amount*price:.2f} < 최소 {cmin}"
        return True, ""

    def buy_market(self, sym, usdt):
        px = self.live_price(sym)
        src = self.ex if self.ex else self.data_ex
        amount = float(src.amount_to_precision(sym, usdt / px))
        ok, why = self._fits(sym, amount, px)
        if not ok:
            return None, why
        if self.dry:
            fill = px * (1 + SLIPPAGE)
            self.state.d["paper_cash"] = self.free_usdt() - amount * fill
            return {"filled": amount, "average": fill}, ""
        o = self._settle(sym, self.ex.create_order(sym, "market", "buy", amount))
        time.sleep(1.0)
        free = float(self.ex.fetch_balance().get(sym.split("/")[0], {}).get("free") or 0)
        o["filled"] = min(float(o.get("filled") or amount), free)   # 수수료가 코인으로 빠진다
        return o, ""

    def sell_market(self, sym, amount):
        if self.dry:
            px = self.live_price(sym) * (1 - SLIPPAGE)
            self.state.d["paper_cash"] = self.free_usdt() + amount * px
            return {"filled": amount, "average": px}
        free = float(self.ex.fetch_balance().get(sym.split("/")[0], {}).get("free") or 0)
        amt = float(self.ex.amount_to_precision(sym, min(amount, free)))
        if amt <= 0:
            return None
        return self._settle(sym, self.ex.create_order(sym, "market", "sell", amt))

    def sell_limit(self, sym, amount, price):
        if self.dry:
            return {"id": f"paper-{sym}-{price:.8f}"}
        free = float(self.ex.fetch_balance().get(sym.split("/")[0], {}).get("free") or 0)
        amt = float(self.ex.amount_to_precision(sym, min(amount, free)))
        prc = float(self.ex.price_to_precision(sym, price))
        ok, why = self._fits(sym, amt, prc)
        if not ok:
            logging.warning(f"[{sym}] 지정가 매도 불가 — {why}")
            return None
        return self.ex.create_order(sym, "limit", "sell", amt, prc)

    def sell_oco(self, sym, amount, tp, stop):
        """익절 지정가 + 손절 스탑을 한 쌍으로. 실패하면 지정가만 걸고 코드 손절로 내려간다."""
        if self.dry:
            return {"id": f"paper-oco-{sym}-{tp:.8f}"}, True
        free = float(self.ex.fetch_balance().get(sym.split("/")[0], {}).get("free") or 0)
        amt = float(self.ex.amount_to_precision(sym, min(amount, free)))
        tpp = float(self.ex.price_to_precision(sym, tp))
        stp = float(self.ex.price_to_precision(sym, stop))
        stl = float(self.ex.price_to_precision(sym, stop * (1 - STOP_LIMIT_GAP)))
        ok, why = self._fits(sym, amt, stp)
        if not ok:
            logging.warning(f"[{sym}] OCO 불가 — {why}")
            return None, False
        try:
            o = self.ex.private_post_order_oco({
                "symbol": self._mkt(sym)["id"], "side": "SELL",
                "quantity": self.ex.amount_to_precision(sym, amt),
                "price": self.ex.price_to_precision(sym, tpp),
                "stopPrice": self.ex.price_to_precision(sym, stp),
                "stopLimitPrice": self.ex.price_to_precision(sym, stl),
                "stopLimitTimeInForce": "GTC"})
            logging.info(f"[{sym}] OCO 익절 {tpp:.8g} / 손절 {stp:.8g} 수량 {amt:.6g}")
            return {"id": str(o.get("orderListId") or o.get("listClientOrderId")), "oco": True}, True
        except Exception as e:
            logging.warning(f"[{sym}] OCO 실패({type(e).__name__}: {e}) — 지정가만, 손절은 코드로")
            return self.sell_limit(sym, amt, tpp), False

    def sell_stop(self, sym, amount, stop):
        """손절만 거래소에 건다 (트레일링 전략용). 매일 갱신한다."""
        if self.dry:
            return {"id": f"paper-stop-{sym}-{stop:.8f}"}
        free = float(self.ex.fetch_balance().get(sym.split("/")[0], {}).get("free") or 0)
        amt = float(self.ex.amount_to_precision(sym, min(amount, free)))
        stp = float(self.ex.price_to_precision(sym, stop))
        stl = float(self.ex.price_to_precision(sym, stop * (1 - STOP_LIMIT_GAP)))
        ok, why = self._fits(sym, amt, stp)
        if not ok:
            logging.warning(f"[{sym}] 손절 주문 불가 — {why}")
            return None
        try:
            return self.ex.create_order(sym, "STOP_LOSS_LIMIT", "sell", amt, stl,
                                        {"stopPrice": stp})
        except Exception as e:
            logging.warning(f"[{sym}] 손절 주문 실패: {e} — 코드 손절로 간다")
            return None

    def cancel(self, sym, oid, oco=False):
        if self.dry or not oid:
            return
        try:
            if oco:
                self.ex.private_delete_orderlist({"symbol": self._mkt(sym)["id"],
                                                  "orderListId": int(oid)})
            else:
                self.ex.cancel_order(oid, sym)
        except Exception as e:
            logging.debug(f"[{sym}] 주문 {oid} 취소 실패(이미 체결/취소?): {e}")

    def _settle(self, sym, o):
        for _ in range(3):
            if o.get("average"):
                return o
            time.sleep(0.7)
            try:
                o = self.ex.fetch_order(o["id"], sym)
            except Exception:
                break
        o.setdefault("average", self.live_price(sym))
        return o

    def cancel_exits(self, sym, p):
        for key, ock in (("tp1_id", "tp1_oco"), ("tp2_id", "tp2_oco"), ("stop_id", None)):
            if p.get(key):
                self.cancel(sym, p[key], oco=bool(ock and p.get(ock)))
                p[key] = None

    # ── 청산 주문 배치 ─────────────────────────────────────
    def place_exits(self, sym):
        p = self.state.pos[sym]
        if self.A.uses_oco:
            if p.get("runner"):
                o, oco = self.sell_oco(sym, p["qty"], p["tp2"], p["stop"])
                p["tp2_id"], p["tp2_oco"] = (o["id"] if o else None), bool(oco)
                p["exchange_stop"] = bool(oco); return
            q1 = p["qty"] * p["split"]
            o1, k1 = self.sell_oco(sym, q1, p["tp1"], p["stop"])
            o2, k2 = self.sell_oco(sym, p["qty"] - q1, p["tp2"], p["stop"])
            p["tp1_id"], p["tp1_oco"] = (o1["id"] if o1 else None), bool(k1)
            p["tp2_id"], p["tp2_oco"] = (o2["id"] if o2 else None), bool(k2)
            p["exchange_stop"] = bool(k1 and k2)
        else:
            o = self.sell_stop(sym, p["qty"], p["stop"])
            p["stop_id"] = o["id"] if o else None
            p["exchange_stop"] = bool(o)
        if not p.get("exchange_stop"):
            logging.warning(f"[{sym}] 거래소 손절 미등록 — 봇이 죽으면 손절도 죽는다")

    # ── 진입 ──────────────────────────────────────────────
    def candidates(self):
        out, held = [], set(self.state.pos)
        for sym, df in self.daily.items():
            if sym in held or sym not in self.pre:
                continue
            i = len(df) - 1
            if i < self.A.min_bars or bars_age(df) > 2:
                continue
            try:
                if self.A.signal(df, self.pre[sym], i):
                    out.append((self.A.rank(self.pre[sym], i), sym, i))
            except Exception:
                continue
        out.sort(reverse=True)
        return out

    def enter(self, sym, i, budget):
        o, why = self.buy_market(sym, budget)
        if o is None:
            logging.info(f"[{sym}] 진입 건너뜀 — {why}")
            return False
        entry, qty = float(o["average"]), float(o["filled"])
        plan = self.A.plan(self.daily[sym], self.pre[sym], i, entry)
        p = {"qty": qty, "entry": entry, "runner": False, "budget": budget,
             "opened": datetime.now(timezone.utc).isoformat(),
             "tp1_id": None, "tp2_id": None, "stop_id": None,
             "tp1_oco": False, "tp2_oco": False, "exchange_stop": False}
        p.update(plan)
        self.state.pos[sym] = p
        self.place_exits(sym)
        self.state.save()
        logging.info(f"★ 진입 {sym}  {qty:.6g} @ {entry:.8g}  ({budget:.2f} USDT)  {plan.get('note','')}")
        return True

    # ── 청산 ──────────────────────────────────────────────
    def close(self, sym, reason, frac=1.0):
        p = self.state.pos.get(sym)
        if not p:
            return
        self.cancel_exits(sym, p)
        q = p["qty"] * frac
        o = self.sell_market(sym, q)
        px = float(o["average"]) if o and o.get("average") else self.live_price(sym)
        self.state.d["history"].append(
            {"symbol": sym, "strategy": self.A.key, "reason": reason, "qty": q,
             "entry": p["entry"], "exit": px, "pnl": q * (px - p["entry"]),
             "roi": px / p["entry"] - 1, "opened": p["opened"],
             "closed": datetime.now(timezone.utc).isoformat()})
        logging.info(f"◆ 청산 {sym} [{reason}] {q:.6g} @ {px:.8g}  ROI {(px/p['entry']-1)*100:+.1f}%")
        if frac >= 0.999:
            del self.state.pos[sym]
        else:
            p["qty"] -= q
        self.state.save()

    def _check_orders(self, sym, p, px):
        """거래소에 걸어둔 주문이 체결됐는지 확인."""
        if self.dry:
            return False
        for key, tag in (("tp1_id", "TP1"), ("tp2_id", "TP2"), ("stop_id", "STOP")):
            oid = p.get(key)
            if not oid or (key.startswith("tp") and p.get(key.replace("_id", "_oco"))):
                # OCO 는 orderListId 라서 fetch_order 로 못 본다 → 잔고로 판단
                continue
            try:
                o = self.ex.fetch_order(oid, sym)
            except Exception:
                continue
            if o.get("status") != "closed":
                continue
            q = float(o.get("filled") or 0)
            fill = float(o.get("average") or o.get("price") or px)
            self.state.d["history"].append(
                {"symbol": sym, "strategy": self.A.key, "reason": tag, "qty": q,
                 "entry": p["entry"], "exit": fill, "pnl": q * (fill - p["entry"]),
                 "roi": fill / p["entry"] - 1, "opened": p["opened"],
                 "closed": datetime.now(timezone.utc).isoformat()})
            logging.info(f"◆ 체결 {sym} [{tag}] {q:.6g} @ {fill:.8g}  ROI {(fill/p['entry']-1)*100:+.1f}%")
            p[key] = None
            p["qty"] = max(p["qty"] - q, 0)
            if tag == "TP1":
                p["runner"] = True
            if p["qty"] <= 0 or tag in ("TP2", "STOP"):
                del self.state.pos[sym]; self.state.save(); return True
            self.state.save()
        return False

    def _reconcile(self, sym, p):
        """실제 잔고와 상태파일이 어긋났는지 본다 (OCO 체결은 주문조회로 안 잡힌다)."""
        if self.dry:
            return False
        try:
            free = float(self.ex.fetch_balance().get(sym.split("/")[0], {}).get("total") or 0)
        except Exception:
            return False
        if free < p["qty"] * 0.05:                      # 사실상 다 팔렸다
            px = self.live_price(sym) or p["entry"]
            self.state.d["history"].append(
                {"symbol": sym, "strategy": self.A.key, "reason": "FILLED_EXCHANGE",
                 "qty": p["qty"], "entry": p["entry"], "exit": px,
                 "pnl": p["qty"] * (px - p["entry"]), "roi": px / p["entry"] - 1,
                 "opened": p["opened"], "closed": datetime.now(timezone.utc).isoformat()})
            logging.info(f"◆ {sym} 거래소에서 체결 완료 확인 — 상태 정리")
            del self.state.pos[sym]; self.state.save(); return True
        if free < p["qty"] * 0.9:                        # 일부만 남음 = 1차 익절 체결
            logging.info(f"[{sym}] 보유 수량 {p['qty']:.6g} → {free:.6g} 로 줄었다. 1차 익절로 본다")
            p["qty"] = free; p["runner"] = True; self.state.save()
        return False

    def manage(self):
        for sym in list(self.state.pos):
            p = self.state.pos[sym]
            px = self.live_price(sym)
            if px is None:
                continue
            if self._check_orders(sym, p, px) or self._reconcile(sym, p):
                continue
            df, pre = self.daily.get(sym), self.pre.get(sym)
            if df is not None and pre is not None:
                r = self.A.on_day(p, df, pre, len(df) - 1, px)
                if r.get("set"):
                    changed = r["set"].get("stop") not in (None, p.get("stop"))
                    p.update(r["set"])
                    if changed and not self.A.uses_oco:
                        self.cancel_exits(sym, p)
                        o = self.sell_stop(sym, p["qty"], p["stop"])
                        p["stop_id"] = o["id"] if o else None
                        logging.info(f"[{sym}] 손절 이동 → {p['stop']:.8g}")
                    self.state.save()
                if r["action"] == "sell":
                    self.close(sym, r["reason"], r.get("frac", 1.0))
                    if r.get("set") and sym in self.state.pos:
                        self.state.pos[sym].update(r["set"])
                        self.place_exits(sym); self.state.save()
                    continue
            if df is not None and bars_age(df) > 3:
                logging.warning(f"[{sym}] 일봉이 멈춤 — 정리한다")
                self.close(sym, "DELISTED", 1.0)

    # ── 하루 1회 ───────────────────────────────────────────
    def daily_tick(self, syms=None):
        logging.info("=" * 64)
        logging.info(f"{self.A.name} [{self.mode}] {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
        syms = syms or self.universe()
        self.refresh(sorted(set(syms) | set(self.state.pos) | {"BTC/USDT"}))
        self.compute()
        self.manage()

        eq, free = self.equity(), self.free_usdt()
        logging.info(f"자산 {eq:,.2f} / 현금 {free:,.2f} / 보유 {len(self.state.pos)}종")
        for s, p in self.state.pos.items():
            px = self.live_price(s) or p["entry"]
            logging.info(f"   {s:<16}{(px/p['entry']-1)*100:+6.1f}%  {self.A.describe(p)}")

        if self.state.d.get("halted"):
            logging.warning("정지 상태 — 신규 진입 없음"); return
        g = ai_gate.read(self.A.key)
        if g["active"]:
            logging.warning(f"AI 개입: 신규진입 {'허용' if g['allow_new'] else '금지'} "
                            f"/ 비중 x{g['size_mult']:.2f} — {g['reason']}")
        if not g["allow_new"]:
            logging.warning("AI 개입으로 신규 진입 없음"); return
        if len(self.state.pos) >= self.A.max_concurrent:
            logging.info(f"자리 {self.A.max_concurrent}개 다 참"); return

        ok, why = self.A.gate(self.daily)
        logging.info(f"시장 필터: {'통과' if ok else '차단'} — {why}")
        if not ok:
            return

        budget = eq * self.A.ticker_pct * g["size_mult"]
        chk = sizing.check(self.A.name, eq, self.A.ticker_pct * g["size_mult"],
                           self.A.max_concurrent, self.A.sell_fracs)
        if not chk["ok"]:
            logging.error(f"시드 부족 — 1종목 {budget:.2f} USDT 로는 검증한 대로 못 돌린다. "
                          f"최소 {chk['min_equity_sell']:.0f} USDT 필요. 신규 진입 안 함.")
            return

        cands = self.candidates()
        logging.info(f"진입 후보 {len(cands)}종, 1종목당 {budget:,.2f} USDT")
        for r, sym, i in cands[:10]:
            logging.info(f"   후보 {sym:<16} {self.A.plan(self.daily[sym], self.pre[sym], i, self.live_price(sym) or 1).get('note','')}")
        for _, sym, i in cands:
            if len(self.state.pos) >= self.A.max_concurrent or self.free_usdt() < budget:
                break
            self.enter(sym, i, budget)
        self.state.save()

    def fast_tick(self):
        if self.state.pos:
            self.manage()
