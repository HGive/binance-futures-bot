#!/usr/bin/env python3
"""
SWING_DCA 실행 봇 — 코인 스윙 물타기, 하루 한 번 + 중간 점검.

    ./.venv/bin/python live/swing_bot.py --mode paper --once
    ./.venv/bin/python live/swing_bot.py --mode testnet --once
    ./.venv/bin/python live/swing_bot.py --mode live --once --i-know

설계
  - 신호·자격·가격 계산은 strategies/swing_dca.py 를 그대로 부른다 (규칙 3.1, 3.7).
  - **주문을 거래소에 걸어둔다.** 봇이 안 돌고 있어도 익절·물타기·손절이 체결된다.
        물타기  지정가 매수  평단 × (1-STEP)     (아직 안 물었을 때만)
        익절    지정가 매도  평단 × (1+TP)       reduceOnly
        손절    STOP_MARKET 평단 × (1-STOP)     reduceOnly  (규칙 3.5)
  - 보유량·평단은 매번 거래소에서 읽는다. 물탔는지도 명목으로 판정한다 (규칙 3.6).
    파일에 남기는 건 킬스위치용 자본 고점과 정지 플래그, 재진입 대기뿐.
  - 물타기가 체결되면 평단이 내려가므로 익절·손절을 다음 실행에서 다시 건다.
    그 사이 남아있는 옛 주문은 **익절은 더 비싸고 손절은 더 높다** — 둘 다 안전한 방향이다.
  - 같은 날 여러 번 돌려도 안전하다. 이미 맞는 주문이 걸려 있으면 건드리지 않는다.
  - **다른 봇과 계정을 같이 쓸 수 있다** (`--capital`, `--reserved`).
        `--capital`   자기 시드를 금액으로 못 박는다. 계좌 잔고를 안 본다.
                      자기 자본 = 시드 + 자기 종목의 실현손익 + 자기 포지션 평가손익.
        `--reserved`  다른 봇 몫인 종목. 후보에서 빼고, 그 포지션은 건드리지 않는다.
        이 둘이 없으면 계좌 전체를 자기 것으로 보고 남의 포지션에 주문을 건다.

기대치는 strategies/swing_dca.py 머리말 참고. 요약: 연 +24%, MDD -62%,
승률 97%. **BTC·ETH 그냥 보유(연 +33%)를 이기지 못한다.** 알고 돌린다.
"""
import argparse
import asyncio
import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import ccxt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import strategies.swing_dca as S  # noqa: E402
from live import ai_gate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.environ.get("SWING_LOG_DIR") or os.path.join(ROOT, "logs")
DAY_MS = 86_400_000
PAPER_FEE = 0.0006
TOP_N = 150                 # 거래대금 상위 이만큼만 후보로 본다 (API 호출 절약)
GATE_KEY = "swing_dca"
P = S.LIVE

# 계정을 나눠 쓸 때 자기 실현손익으로 인정하는 수입 항목.
INCOME_KINDS = ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")


def parse_symbols(text):
    """쉼표로 나열한 심볼 목록. 빈 문자열은 빈 집합."""
    return {s.strip() for s in (text or "").split(",") if s.strip()}


def precise_amount(ex, sym, amt):
    """수량 단위 미만이면 0. ccxt 는 이때 예외를 던져서 봇이 멈춘다."""
    try:
        return float(ex.amount_to_precision(sym, amt))
    except (ccxt.InvalidOrder, ccxt.ArgumentsRequired, ValueError):
        return 0.0


def closed_daily(ohlcv, now_ms):
    """마감된 일봉만 (규칙 3.2)."""
    df = pd.DataFrame(ohlcv, columns=["t", "o", "h", "l", "c", "v"])
    df = df[df["t"] + DAY_MS <= now_ms]
    return df.set_index(pd.to_datetime(df["t"], unit="ms"))[["o", "h", "l", "c", "v"]]


class SwingBot:
    def __init__(self, mode, paper_equity=1_000.0, capital=0.0, reserved=None):
        self.mode = mode
        self.dry = mode == "paper"
        self.capital = float(capital or 0.0)
        self.reserved = set(reserved or ())
        self.state_path = os.path.join(LOG_DIR, f"swing_dca_{mode}.json")
        self.journal = os.path.join(LOG_DIR, f"swing_dca_{mode}.jsonl")
        self.st = {"peak": None, "halted": False, "cooldown": {}}
        if self.dry:
            self.st.update(cash=paper_equity, pos={})
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                self.st.update(json.load(f))
        self.pub = None
        self.ex = None

    # ---------------------------------------------------------------- 연결
    async def connect(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        self.pub = config.make_exchange("futures", testnet=False, use_pro=True, need_keys=False)
        await self.pub.load_markets()
        if not self.dry:
            self.ex = config.make_exchange("futures", testnet=(self.mode == "testnet"), use_pro=True)
            await self.ex.load_markets()
            others = self.foreign_positions(await self.ex.fetch_positions())
            if others:
                raise RuntimeError(
                    f"이 전략이 열지 않은 포지션이 있다: {others[:5]}. "
                    "SWING_DCA 는 계정 전체를 자기 자본으로 보고 보유분에 익절·손절을 건다. "
                    "다른 봇(예: TREND_VT 의 BTC·ETH 롱)과 계정을 같이 쓰면 서로의 주문을 "
                    "지운다. 전용 서브계정에서 돌리거나, 그 종목을 --reserved 로 빼고 "
                    "--capital 로 자기 시드를 못 박는다.")
            if self.reserved and not self.capital:
                raise RuntimeError(
                    "--reserved 를 쓰면 --capital 도 있어야 한다. 계정을 나눠 쓰는데 "
                    "계좌 잔고를 자기 시드로 세면 남의 돈만큼 더 산다.")
            if self.st.get("started_ms") is None:
                self.st["started_ms"] = self.ex.milliseconds()
                self.save()
            dual = await self.ex.fapiPrivateGetPositionSideDual()
            if str(dual.get("dualSidePosition")).lower() == "true":
                raise RuntimeError("헤지 모드 계정이다. 단방향(One-way) 전용이다.")

    def foreign_positions(self, positions):
        """이 전략이 연 적이 없는 포지션 목록.

        숏은 이 전략이 절대 만들지 않으므로 무조건 남의 것이다.
        롱은 `owned` 에 기록해 둔 종목만 자기 것으로 본다 — 수량·평단은 항상
        거래소에서 읽고(규칙 3.6), 기록은 '어느 종목을 내가 열었나'만 담는다.
        이게 없으면 같은 계정의 다른 롱 봇(TREND_VT 의 BTC·ETH)을 자기 포지션으로
        오인해 그 손절 주문을 지운다.

        `reserved` 에 넣은 종목은 '남의 것인 줄 알고 있다'는 뜻이라 통과시킨다.
        대신 후보에서도 빠지므로 이 전략이 그 종목을 여는 일은 없다.
        """
        owned = set(self.st.get("owned", []))
        out = []
        for p in positions:
            if not float(p.get("contracts") or 0):
                continue
            sym = p["symbol"]
            if sym in self.reserved:
                continue
            if p.get("side") == "short" or sym not in owned:
                out.append(sym)
        return out

    def remember(self, sym):
        self.st.setdefault("owned", [])
        if sym not in self.st["owned"]:
            self.st["owned"].append(sym)
        # 실현손익을 되짚으려면 '한 번이라도 가졌던' 목록이 따로 필요하다.
        self.st.setdefault("owned_ever", [])
        if sym not in self.st["owned_ever"]:
            self.st["owned_ever"].append(sym)

    def forget(self, sym):
        if sym in self.st.get("owned", []):
            self.st["owned"].remove(sym)

    async def close(self):
        for x in (self.pub, self.ex):
            if x:
                try:
                    await x.close()
                except Exception:  # noqa: BLE001
                    pass

    # ---------------------------------------------------------------- 데이터
    async def universe(self):
        """거래대금 상위 TOP_N 중 자격을 갖춘 종목과 진입 신호를 돌려준다."""
        now = self.pub.milliseconds()
        tickers = await self.pub.fetch_tickers()
        rows = []
        for sym, t in tickers.items():
            m = self.pub.markets.get(sym)
            if not m or not m.get("swap") or m.get("quote") != S.QUOTE or not m.get("active"):
                continue
            if sym in self.reserved:        # 다른 봇 몫 — 이 전략은 손대지 않는다
                continue
            qv = float(t.get("quoteVolume") or 0)
            if qv > 0:
                rows.append((sym, qv))
        rows.sort(key=lambda x: -x[1])
        cands = [s for s, _ in rows[:TOP_N]]

        closes, qvs = {}, {}
        sem = asyncio.Semaphore(8)

        async def one(sym):
            async with sem:
                try:
                    o = await self.pub.fetch_ohlcv(sym, S.TIMEFRAME, limit=S.HISTORY_DAYS)
                except Exception:  # noqa: BLE001
                    return
                d = closed_daily(o, now)
                if len(d) < P.min_age:
                    return
                closes[sym] = d["c"]
                qvs[sym] = d["c"] * d["v"]

        await asyncio.gather(*(one(s) for s in cands))
        if not closes:
            return [], None
        close = pd.DataFrame(closes).sort_index()
        qv = pd.DataFrame(qvs).reindex(close.index)
        elig = S.eligible(close, qv, P)
        sig = S.entry_signal(close)
        last = close.index[-1]
        ready = [s for s in close.columns
                 if bool(elig.loc[last, s]) and bool(sig.loc[last, s])]
        return ready, last

    # ---------------------------------------------------------------- 계정
    async def equity(self):
        if self.dry:
            return self.st["cash"] + sum(
                v["qty"] * v["last"] for v in self.st["pos"].values())
        if self.capital:
            # 계좌 잔고에는 다른 봇의 돈이 섞여 있다. 자기 몫만 센다.
            return self.capital + await self.own_realized() + await self.own_unrealized()
        bal = await self.ex.fetch_balance()
        return float(bal[S.QUOTE]["total"])

    async def own_realized(self):
        """이 전략이 한 번이라도 가졌던 종목의 실현손익·수수료·펀딩 합계.

        거래소 기록에서 매번 다시 더한다 — 파일에 누적하면 실행이 겹치거나
        빠졌을 때 틀어진다(규칙 3.6).
        """
        ever = [s for s in self.st.get("owned_ever", []) if s in self.ex.markets]
        if not ever:
            return 0.0
        ids = {self.ex.market(s)["id"] for s in ever}
        cursor, total = int(self.st.get("started_ms") or 0), 0.0
        for _ in range(20):                     # 페이지 상한 — 무한루프 방지
            rows = await self.ex.fapiPrivateGetIncome(
                {"startTime": cursor, "limit": 1000})
            if not rows:
                break
            for r in rows:
                if r.get("symbol") in ids and r.get("incomeType") in INCOME_KINDS:
                    total += float(r.get("income") or 0)
            if len(rows) < 1000:
                break
            cursor = int(rows[-1]["time"]) + 1
        return total

    async def own_unrealized(self):
        """자기 포지션의 평가손익."""
        owned = set(self.st.get("owned", []))
        return sum(float(p.get("unrealizedPnl") or 0)
                   for p in await self.ex.fetch_positions()
                   if p["symbol"] in owned and float(p.get("contracts") or 0))

    async def positions(self):
        """{심볼: {qty, avg}} — 거래소가 진실이다 (규칙 3.6).

        **자기가 연 종목만** 돌려준다. 계정을 나눠 쓸 때 이게 없으면 남의 롱에
        익절·손절 주문을 걸어버린다.
        """
        if self.dry:
            return {k: {"qty": v["qty"], "avg": v["avg"]} for k, v in self.st["pos"].items()}
        owned = set(self.st.get("owned", []))
        out = {}
        for p in await self.ex.fetch_positions():
            q = float(p.get("contracts") or 0)
            if q > 0 and p.get("side") == "long" and p["symbol"] in owned:
                out[p["symbol"]] = {"qty": q, "avg": float(p["entryPrice"])}
        return out

    # ---------------------------------------------------------------- 주문
    @staticmethod
    def algo_rows(resp):
        """조건부 주문 응답을 목록으로. **바이낸스는 list 를 준다** — dict 로만
        처리하면 손절이 항상 '없다'고 나와 매번 새로 걸고 옛것을 지운다."""
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict):
            return resp.get("orders", []) or []
        return []

    @staticmethod
    def algo_stop_price(o):
        return float(o.get("stopPrice") or o.get("triggerPrice") or 0)

    async def open_algos(self, sym):
        mid = self.ex.market(sym)["id"]
        rows = self.algo_rows(await self.ex.fapiPrivateGetOpenAlgoOrders({}))
        return [o for o in rows if o.get("symbol") == mid]

    async def ensure_orders(self, sym, pos, budget):
        """이 종목에 필요한 주문(물타기·익절·손절)이 정확히 걸려 있게 만든다."""
        avg, qty = pos["avg"], pos["qty"]
        added = S.has_added(S.cost_basis(qty, avg), budget, P)
        want = {
            "tp": (round(S.tp_price(avg, P), 10), "sell", qty, "limit"),
            "stop": (round(S.stop_price(avg, P), 10), "sell", qty, "stop"),
        }
        if not added:
            # 남은 예산만큼만 더 넣는다. 부분체결이 나도 스스로 맞춰지고
            # 칸 예산을 넘지 않으므로 레버리지가 걸리지 않는다.
            add_notional = S.remaining_add(qty, avg, budget)
            px = S.add_price(avg, P)
            if add_notional > 0:
                want["add"] = (round(px, 10), "buy", add_notional / px, "limit")

        if self.dry:
            self.st["pos"][sym]["orders"] = {k: v[0] for k, v in want.items()}
            return

        existing = await self.ex.fetch_open_orders(sym)
        algo_list = await self.open_algos(sym)

        def near(a, b):
            return abs(a - b) / max(b, 1e-12) < 0.002

        have_tp = any(o["side"] == "sell" and o["type"] == "limit"
                      and near(float(o["price"]), want["tp"][0]) for o in existing)
        have_add = "add" not in want or any(
            o["side"] == "buy" and o["type"] == "limit"
            and near(float(o["price"]), want["add"][0]) for o in existing)
        have_stop = any(near(self.algo_stop_price(a), want["stop"][0]) for a in algo_list)

        # 안 맞는 주문은 지운다
        for o in existing:
            keep = ((o["side"] == "sell" and have_tp and near(float(o["price"]), want["tp"][0]))
                    or (o["side"] == "buy" and "add" in want and have_add
                        and near(float(o["price"]), want["add"][0])))
            if not keep:
                try:
                    await self.ex.cancel_order(o["id"], sym)
                except Exception as e:  # noqa: BLE001
                    logging.warning(f"[{sym}] 주문 취소 실패 {o['id']}: {e}")

        if not have_stop:
            # 새 손절을 **먼저** 걸고, 그 다음 나머지를 지운다.
            # 반대로 하면 손절이 하나도 없는 구간이 생긴다 (trend_bot 과 같은 규칙).
            amt = precise_amount(self.ex, sym, qty)
            if amt > 0:
                try:
                    o = await self.ex.create_order(
                        sym, "STOP_MARKET", "sell", amt, None,
                        {"stopPrice": self.ex.price_to_precision(sym, want["stop"][0]),
                         "reduceOnly": True, "workingType": "MARK_PRICE"})
                    logging.info(f"[{sym}] 손절 {want['stop'][0]:.6g}")
                    keep = str(o.get("id") or o.get("info", {}).get("algoId") or "")
                    for a in await self.open_algos(sym):
                        if str(a.get("algoId")) != keep:
                            try:
                                await self.ex.fapiPrivateDeleteAlgoOrder(
                                    {"algoId": str(a["algoId"])})
                            except Exception:  # noqa: BLE001
                                pass
                except Exception as e:  # noqa: BLE001
                    logging.error(f"[{sym}] 손절 실패 — 포지션을 정리한다: {e}")
                    await self.market_close(sym, qty)
                    return

        if not have_tp:
            amt = precise_amount(self.ex, sym, qty)
            if amt > 0:
                try:
                    await self.ex.create_order(
                        sym, "limit", "sell", amt,
                        self.ex.price_to_precision(sym, want["tp"][0]), {"reduceOnly": True})
                    logging.info(f"[{sym}] 익절 지정가 {want['tp'][0]:.6g}")
                except Exception as e:  # noqa: BLE001
                    logging.warning(f"[{sym}] 익절 주문 실패: {e}")

        if "add" in want and not have_add:
            amt = precise_amount(self.ex, sym, want["add"][2])
            if amt > 0 and self.enough_notional(sym, amt, want["add"][0]):
                try:
                    await self.ex.create_order(
                        sym, "limit", "buy", amt,
                        self.ex.price_to_precision(sym, want["add"][0]))
                    logging.info(f"[{sym}] 물타기 지정가 {want['add'][0]:.6g} × {amt}")
                except Exception as e:  # noqa: BLE001
                    logging.warning(f"[{sym}] 물타기 주문 실패: {e}")

    def enough_notional(self, sym, amt, px):
        m = (self.ex or self.pub).market(sym)
        lim = (m.get("limits") or {}).get("cost") or {}
        return amt * px >= float(lim.get("min") or 5.0)

    async def market_close(self, sym, qty):
        amt = precise_amount(self.ex, sym, qty)
        if amt > 0:
            await self.ex.create_order(sym, "market", "sell", amt, None, {"reduceOnly": True})

    async def open_new(self, sym, budget):
        """새 칸을 연다. 첫 매수는 칸 예산의 1/(1+add_mult)."""
        notional = budget / (1.0 + P.add_mult)
        t = await self.pub.fetch_ticker(sym)
        px = float(t["last"])
        if self.dry:
            self.st["pos"][sym] = {"qty": notional / px, "avg": px, "last": px}
            self.st["cash"] -= notional * (1 + PAPER_FEE)
            self.remember(sym)
            self.save()
            logging.info(f"[{sym}] (paper) 진입 {notional:.2f} USDT @ {px:.6g}")
            return True
        amt = precise_amount(self.ex, sym, notional / px)
        if amt <= 0 or not self.enough_notional(sym, amt, px):
            logging.info(f"[{sym}] 최소 주문액 미달 — 건너뜀")
            return False
        try:
            # 격리 마진 — 계정을 나눠 쓸 때 이 전략의 손실이 다른 봇의 증거금을
            # 갉아먹지 않게 한다. 이미 격리면 거래소가 거부하므로 무시한다.
            await self.ex.set_margin_mode("isolated", sym)
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.ex.set_leverage(S.LEVERAGE, sym)
        except Exception:  # noqa: BLE001
            pass
        try:
            # 주문을 내기 **전에** 기록한다. 체결 직후 봇이 죽어도 다음 실행이
            # 자기 포지션으로 알아본다. 안 열렸으면 아래 정리 단계가 지운다.
            self.remember(sym)
            self.save()
            await self.ex.create_order(sym, "market", "buy", amt)
            logging.info(f"[{sym}] 진입 {amt} @ ~{px:.6g}")
            return True
        except Exception as e:  # noqa: BLE001
            logging.warning(f"[{sym}] 진입 실패: {e}")
            return False

    # ---------------------------------------------------------------- 본체
    async def run_once(self):
        eq = await self.equity()
        peak = max(self.st.get("peak") or eq, eq)
        self.st["peak"] = peak
        dd = eq / peak - 1
        if self.st.get("halted"):
            logging.warning(f"정지 상태 (자본 {eq:.2f}). --reset-kill 로만 푼다.")
            return
        if dd <= -S.KILL_DD:
            logging.error(f"킬스위치: 고점 대비 {dd:.1%} — 전부 정리하고 멈춘다")
            pos = await self.positions()
            if not self.dry:
                for sym, p in pos.items():
                    for o in await self.ex.fetch_open_orders(sym):
                        try:
                            await self.ex.cancel_order(o["id"], sym)
                        except Exception:  # noqa: BLE001
                            pass
                    await self.market_close(sym, p["qty"])
            self.st["halted"] = True
            self.save()
            return

        gate = ai_gate.read(GATE_KEY)
        pos = await self.positions()
        budget = eq / P.slots

        # 청산된 종목은 기록에서 지우고 재진입 대기를 건다
        today = datetime.now(timezone.utc).date()
        for sym in list(self.st.get("owned", [])):
            if sym not in pos:
                self.forget(sym)
                self.st.setdefault("cooldown", {})[sym] = (
                    today + timedelta(days=P.cooldown_days)).isoformat()
                logging.info(f"[{sym}] 청산 확인 — 재진입 {P.cooldown_days}일 대기")
        self.save()

        # 1) 보유분 주문 정리
        for sym, p in pos.items():
            try:
                await self.ensure_orders(sym, p, budget)
            except Exception as e:  # noqa: BLE001
                logging.error(f"[{sym}] 주문 정리 실패: {e}")

        # 2) 빈 칸 채우기
        free = P.slots - len(pos)
        if free > 0 and gate.get("allow_new", True):
            ready, bar = await self.universe()
            cd = self.st.get("cooldown", {})
            today = datetime.now(timezone.utc).date().isoformat()
            ready = [s for s in ready if s not in pos and cd.get(s, "") < today]
            logging.info(f"빈 칸 {free}개 | 자격+신호 종목 {len(ready)}개 (일봉 {bar})")
            for sym in ready[:free]:
                if await self.open_new(sym, budget):
                    p2 = (await self.positions()).get(sym)
                    if p2:
                        try:
                            await self.ensure_orders(sym, p2, budget)
                        except Exception as e:  # noqa: BLE001
                            logging.error(f"[{sym}] 신규 주문 설정 실패: {e}")

        pos = await self.positions()
        eq2 = await self.equity()
        logging.info(f"완료 | 자본 {eq2:.2f} {S.QUOTE} (고점 대비 {eq2 / peak - 1:+.1%}) "
                     f"| 보유 {len(pos)}/{P.slots}칸 {sorted(pos)}")
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(),
                                "equity": eq2, "peak": peak,
                                "positions": {k: v["qty"] for k, v in pos.items()}},
                               ensure_ascii=False) + "\n")
        self.save()

    def save(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.st, f, ensure_ascii=False, indent=1)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["paper", "testnet", "live"], default="paper")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--paper-equity", type=float, default=1_000.0)
    ap.add_argument("--capital", type=float, default=float(os.environ.get("SWING_CAPITAL") or 0),
                    help="자기 시드(USDT). 주면 계좌 잔고 대신 이 금액을 자본으로 쓴다. "
                         "계정을 다른 봇과 나눠 쓸 때 필요하다.")
    ap.add_argument("--reserved", default=os.environ.get("SWING_RESERVED", ""),
                    help="다른 봇 몫인 심볼(쉼표). 후보에서 빼고 그 포지션은 건드리지 않는다.")
    ap.add_argument("--reset-kill", action="store_true")
    ap.add_argument("--i-know", action="store_true")
    a = ap.parse_args()
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True,
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(os.path.join(LOG_DIR, f"swing_dca_{a.mode}.log"),
                                      encoding="utf-8")])
    if a.mode == "live" and not a.i_know:
        print("실전은 --i-know 가 필요하다. 규칙 6: 데모 1주 → 소액 실전 2주.")
        return 1
    bot = SwingBot(a.mode, a.paper_equity, a.capital, parse_symbols(a.reserved))
    if bot.reserved:
        logging.info(f"계정 공유 | 시드 {a.capital:.0f} {S.QUOTE} | 제외 {sorted(bot.reserved)}")
    if a.reset_kill:
        bot.st["halted"] = False
        bot.st["peak"] = None
        bot.save()
        logging.info("킬스위치 해제")
    try:
        await bot.connect()
        await bot.run_once()
    finally:
        await bot.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
