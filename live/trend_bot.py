#!/usr/bin/env python3
"""
TREND_VT 실행 봇 — BTC·ETH 무기한, 하루 한 번 (UTC 00:00 일봉 마감 직후).

    poetry run python live/trend_bot.py --mode paper --once                 # 주문 없음, 가상 장부
    poetry run python live/trend_bot.py --mode testnet --loop                # 데모 실주문, 매일
    poetry run python live/trend_bot.py --mode live --loop --i-know          # 실전

설계
  - 신호·비중·거래 판정·손절가는 strategies/trend_vt.py 의 함수를 그대로 부른다 (규칙 3.1, 3.7).
  - 시세는 모드와 상관없이 **실서버 공개 일봉**으로 계산한다. 백테스트와 같은 데이터여야 한다.
  - 보유량은 매번 거래소에서 읽고, 목표는 매번 일봉에서 다시 계산한다. 추측으로 복원할 상태가 없다 (규칙 3.6).
    파일에 남기는 건 킬스위치용 자본 고점과 정지 플래그뿐.
  - 같은 날 두 번 돌려도 안전하다: 목표와 보유가 같으면 밴드 규칙이 거래를 막는다.
  - 재난 손절은 STOP_MARKET 알고 주문으로 매일 갱신한다 (규칙 3.5). 못 걸면 그 종목을 정리한다.
  - **전용 서브계정**에서 돌린다. 계정 자본 전체를 이 전략 자본으로 본다.
    데모에서만, 다른 봇과 나눠 쓸 수 있다: `--reserved` 로 남의 종목을 가드에서 빼고
    `--reserve-equity` 로 남의 시드를 자본에서 뺀다. 둘은 같이 써야 한다.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import ccxt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import strategies.trend_vt as S  # noqa: E402
from live import ai_gate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 로그·상태 위치. macOS 는 ~/Desktop, ~/Documents 를 보호해서 launchd 가 그 안에
# 쓰지 못한다(Operation not permitted). 프로젝트가 그런 폴더 아래 있으면
# TREND_LOG_DIR 로 보호되지 않는 경로를 지정한다. 상태 파일(킬스위치 자본 고점,
# 정지 플래그)이 여기 남으므로 경로를 바꾸면 그 이력도 같이 옮겨야 한다.
LOG_DIR = os.environ.get("TREND_LOG_DIR") or os.path.join(ROOT, "logs")
DAY_MS = 86_400_000
PAPER_FEE = 0.0005 + 0.0005     # 백테스트 비용과 같게
CATCHUP_HOURS = 6               # 일봉 마감 후 이 시간 안이면 따라잡는다. 넘으면 다음 마감을 기다린다
GATE_KEY = "trend_vt"


def apply_gate(target_w, cur_w, gate):
    """AI 게이트는 브레이크만 (docs/ai_gate.md). 비중을 줄이거나 신규 노출을 막을 뿐, 늘리거나 뒤집지 못한다."""
    tw = target_w * gate["size_mult"]                  # size_mult 는 ai_gate.read 가 0~1 로 자른다
    if not gate["allow_new"]:
        if cur_w == 0 or np.sign(tw) != np.sign(cur_w):
            tw = 0.0                                    # 새로 열거나 반대로 여는 건 막는다 (정리는 허용)
        else:
            tw = np.sign(tw) * min(abs(tw), abs(cur_w))
    return tw


def closed_bars(ohlcv, now_ms):
    """ccxt 일봉 → 마감된 봉만 (규칙 3.2). 인덱스는 UTC 날짜."""
    df = pd.DataFrame(ohlcv, columns=["t", "o", "h", "l", "c", "v"])
    df = df[df["t"] + DAY_MS <= now_ms]
    return df.set_index(pd.to_datetime(df["t"], unit="ms"))[["o", "h", "l", "c"]]


def closed_daily(ohlcv, now_ms):
    return closed_bars(ohlcv, now_ms)["c"]


def stop_hit(side, stop, o, h, l):
    """그 봉에서 손절이 터졌으면 체결가(시가 갭 반영), 아니면 None. 백테스트 simulate 와 같은 규칙."""
    if side > 0 and l <= stop:
        return min(stop, o)
    if side < 0 and h >= stop:
        return max(stop, o)
    return None


def plan(close: pd.DataFrame, equity: float, positions: dict, prices: dict, gate=None):
    """순수 계산: 목표 비중 → 종목별 (목표수량, 현재수량, 거래할지, 손절가). 주문은 안 낸다."""
    gate = gate or {"allow_new": True, "size_mult": 1.0}
    w, _ = S.targets(close, pd.DataFrame(1.0, index=close.index, columns=close.columns), S.LIVE)
    vol_d = S.daily_vol(close).iloc[-1]
    last = close.iloc[-1]
    out = {}
    for sym, cid in zip(S.SYMBOLS, S.IDS):
        px = prices[sym]
        cur_qty = positions.get(sym, 0.0)
        # 밴드 판정은 백테스트처럼 마감 종가로 한 비중으로. 실시간 가격은 주문 수량에만 쓴다.
        cur_w = cur_qty * float(last[cid]) / equity if equity > 0 else 0.0
        raw_w = float(w[cid].iloc[-1])
        tw = float(apply_gate(raw_w, cur_w, gate))
        trade = bool(S.trade_mask([tw], [cur_w])[0])
        side = np.sign(tw if trade else cur_w)
        stop = float(S.stop_price(last[cid], side, vol_d[cid])) if side != 0 else None
        out[sym] = dict(signal_w=raw_w, target_w=tw, cur_w=cur_w, target_qty=tw * equity / px, cur_qty=cur_qty,
                        trade=trade, stop=stop, close=float(last[cid]), vol_d=float(vol_d[cid]))
    return out, str(close.index[-1].date())


def precise_amount(ex, sym, qty):
    """거래소 수량 단위로 내림. 단위보다 작으면 0 (ccxt 는 여기서 InvalidOrder 를 던진다 → 봇이 멈춘다)."""
    try:
        return float(ex.amount_to_precision(sym, abs(qty)))
    except ccxt.InvalidOrder:
        return 0.0


def parse_symbols(text):
    """쉼표로 나열한 심볼 목록. 빈 문자열은 빈 집합."""
    return {s.strip() for s in (text or "").split(",") if s.strip()}


def foreign_positions(positions, reserved=()):
    """이 전략 심볼이 아닌 포지션 목록. 있으면 다른 봇과 계정을 같이 쓰는 것이다.

    `reserved` 에 넣은 종목은 '남의 것인 줄 알고 있다'는 선언이라 통과시킨다.
    이 봇은 어차피 S.SYMBOLS 만 읽고 주문하므로 그 포지션을 건드리지 않는다.

    `"*"` 는 '이 계정을 다른 봇과 나눠 쓴다'는 뜻이다. 상대가 종목을 매일
    바꾸면(SWING_DCA 처럼) 이름을 미리 적을 수 없다. 대신 --reserve-equity 로
    남의 시드를 반드시 빼야 한다 — setup() 이 그걸 강제한다.
    """
    reserved = set(reserved)
    if "*" in reserved:
        return []
    return [p["symbol"] for p in positions
            if float(p.get("contracts") or 0)
            and p["symbol"] not in S.SYMBOLS and p["symbol"] not in reserved]


class TrendBot:
    def __init__(self, mode, paper_equity=10_000.0, reserved=None, reserve_equity=0.0):
        self.mode = mode
        self.dry = mode == "paper"
        self.reserved = set(reserved or ())
        self.reserve_equity = float(reserve_equity or 0.0)
        self.state_path = os.path.join(LOG_DIR, f"trend_vt_{mode}.json")
        self.journal = os.path.join(LOG_DIR, f"trend_vt_{mode}.jsonl")
        self.st = {"peak": None, "halted": False, "last_bar": None}
        if self.dry:
            self.st.update(cash=paper_equity, pos={})
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                self.st.update(json.load(f))
        self.pub = None
        self.ex = None
        self.bars = {}
        self.skipped = []

    async def setup(self, maintenance=False):
        # 시세: 실서버 공개 API (키 없음). 주문: 모드별 계정.
        self.pub = config.make_exchange("futures", testnet=False, use_pro=True, need_keys=False)
        if not maintenance:
            await self.pub.load_markets()
        if not self.dry:
            self.ex = config.make_exchange("futures", testnet=(self.mode == "testnet"), use_pro=True)
            await self.ex.load_markets()
            others = foreign_positions(await self.ex.fetch_positions(), self.reserved)
            if others:
                # 다른 봇(REBOUND 는 거래대금 상위 120종 = BTC·ETH 포함)과 계정을 같이 쓰면
                # 서로의 포지션을 자기 것으로 읽고 손절 주문을 지운다. 시작하지 않는다.
                raise RuntimeError(f"전용 계정이 아니다 — 다른 포지션 {others[:5]}. "
                                   "데모도 실전도 이 전략만 쓰는 계정(서브계정/별도 데모 키)에서 "
                                   "돌린다. 데모에서 일부러 나눠 쓴다면 그 종목을 --reserved 로, "
                                   "남의 시드를 --reserve-equity 로 빼라.")
            if self.reserved and not self.reserve_equity:
                raise RuntimeError(
                    "--reserved 를 쓰면 --reserve-equity 도 있어야 한다. 계정을 나눠 쓰는데 "
                    "계좌 잔고를 전부 자기 자본으로 세면 그만큼 크게 베팅한다.")
            dual = await self.ex.fapiPrivateGetPositionSideDual()
            if str(dual.get("dualSidePosition")).lower() == "true":
                raise RuntimeError("헤지 모드 계정이다. 이 봇은 단방향(One-way) 모드 전용 — 바이낸스 설정에서 바꿔라.")
            for sym in (() if maintenance else S.SYMBOLS):
                for call in (lambda: self.ex.set_margin_mode("isolated", sym),
                             lambda: self.ex.set_leverage(S.LEVERAGE, sym)):
                    try:
                        await call()
                    except Exception as e:     # 이미 설정돼 있으면 -4046 등으로 거절된다
                        logging.info(f"[{sym}] 설정 건너뜀: {type(e).__name__}: {str(e)[:80]}")

    async def maintenance(self):
        """마지막 판단의 손절만 복구한다. 새 시세·진입·리밸런싱은 없다."""
        _, positions = await self.account({})
        opened = await self.ex.fapiPrivateGetOpenAlgoOrders({})
        rows = opened if isinstance(opened, list) else opened.get("orders", [])
        with open(self.journal, encoding="utf-8") as f:
            last = json.loads(list(f)[-1])
        for sym in S.SYMBOLS:
            qty = positions.get(sym, 0.0)
            if not qty:
                await self.cancel_algos(sym)
                continue
            mid = self.ex.market(sym)["id"]
            side = "SELL" if qty > 0 else "BUY"
            protected = any(o.get("symbol") == mid and o.get("orderType") == "STOP_MARKET"
                            and o.get("side") == side
                            and str(o.get("reduceOnly")).lower() == "true"
                            and float(o.get("quantity") or 0) >= abs(qty) for o in rows)
            if not protected:
                if not await self.refresh_stop(sym, qty, last["orders"][sym]):
                    raise RuntimeError(f"[{sym}] 보호 주문 복구 실패로 청산")
        logging.info("TREND 안전 점검 완료 — 신규 매매 없음")

    def rebase_peak(self):
        """자본 기준(--reserve-equity)이 바뀌면 킬스위치 고점도 같이 옮긴다.

        안 옮기면 기준을 바꾼 순간 옛 고점과 비교돼 없던 낙폭이 생긴다.
        실제로 1,000 을 빼자마자 -21% 로 읽혀 킬스위치가 터졌다.
        """
        old = self.st.get("reserve_equity")
        if old is None:
            self.st["reserve_equity"] = self.reserve_equity
            return
        if float(old) == self.reserve_equity:
            return
        delta = self.reserve_equity - float(old)
        if self.st.get("peak"):
            self.st["peak"] = float(self.st["peak"]) - delta
            logging.info(f"자본 기준 변경 {old} → {self.reserve_equity} "
                         f"— 고점도 {self.st['peak']:.2f} 로 옮긴다")
        self.st["reserve_equity"] = self.reserve_equity
        self.save()

    async def close_all(self):
        for ex in (self.pub, self.ex):
            if ex is not None:
                await ex.close()

    def save(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def write_journal(self, rec):
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ── 계정 읽기 ─────────────────────────────────────
    async def fetch_close(self):
        now = self.pub.milliseconds()
        cols = {}
        self.bars = {}
        for sym, cid in zip(S.SYMBOLS, S.IDS):
            o = await self.pub.fetch_ohlcv(sym, S.TIMEFRAME, limit=S.HISTORY_DAYS + 5)
            b = closed_bars(o, now)
            self.bars[sym] = b
            cols[cid] = b["c"]
        return pd.DataFrame(cols).dropna()

    def paper_stops(self):
        """paper 장부에서 지난 실행 이후 마감된 봉들로 손절을 판정한다 (실전에선 거래소가 한다)."""
        last = self.st.get("last_bar")
        for sym, st in list(self.st.get("stops", {}).items()):
            p = self.st["pos"].get(sym)
            if not p or last is None:
                continue
            side = np.sign(p["qty"])
            b = self.bars[sym]
            for day, row in b[b.index > pd.Timestamp(last)].iterrows():
                fill = stop_hit(side, st, row["o"], row["h"], row["l"])
                if fill is not None:
                    fill *= (1 - side * S.LIVE.stop_slip)
                    logging.warning(f"[{sym}] (paper) 재난 손절 체결 {day.date()} @ {fill:.2f} (손절가 {st:.2f})")
                    self.paper_fill(sym, -p["qty"], fill)
                    break
        self.st["stops"] = {}

    async def prices(self):
        out = {}
        for sym in S.SYMBOLS:
            t = await self.pub.fetch_ticker(sym)
            out[sym] = float(t["last"])
        return out

    async def account(self, prices):
        """(자본, {심볼: 부호있는 수량})."""
        if self.dry:
            pos = {s: p["qty"] for s, p in self.st["pos"].items()}
            upnl = sum(p["qty"] * (prices[s] - p["entry"]) for s, p in self.st["pos"].items())
            return self.st["cash"] + upnl, pos
        bal = await self.ex.fetch_balance()
        equity = float(bal["info"].get("totalMarginBalance") or bal["USDT"]["total"])
        # 계정을 나눠 쓸 때 남의 시드를 뺀다. 근사다 — 상대 봇의 평가손익까지는
        # 못 걷어낸다. 데모에서만 쓰고, 실전은 전용 서브계정으로 간다.
        equity -= self.reserve_equity
        pos = {}
        for p in await self.ex.fetch_positions(list(S.SYMBOLS)):
            q = float(p.get("contracts") or 0)
            if q:
                pos[p["symbol"]] = q if p["side"] == "long" else -q
        return equity, pos

    # ── 주문 ───────────────────────────────────────────
    async def market(self, sym, qty_signed, reduce_only, px):
        side = "buy" if qty_signed > 0 else "sell"
        m = (self.pub if self.dry else self.ex).market(sym)
        amt = precise_amount(self.pub if self.dry else self.ex, sym, qty_signed)
        min_cost = (m.get("limits", {}).get("cost", {}) or {}).get("min") or 5.0
        if amt <= 0 or (amt * px < min_cost and not reduce_only):
            logging.info(f"[{sym}] {side} {amt} ≈ {amt*px:.1f} USDT < 최소 {min_cost} → 건너뜀")
            self.skipped.append(dict(symbol=sym, side=side, amount=amt, notional=amt * px, min=min_cost))
            return 0.0
        if self.dry:
            self.paper_fill(sym, amt if side == "buy" else -amt, px)
            logging.info(f"[{sym}] (paper) {side} {amt} @ {px:.2f} reduceOnly={reduce_only}")
            return amt
        params = {"reduceOnly": True} if reduce_only else {}
        o = await self.ex.create_order(sym, "market", side, amt, None, params)
        logging.info(f"[{sym}] {side} {amt} reduceOnly={reduce_only} → {o.get('id')} avg={o.get('average')}")
        return amt

    def paper_fill(self, sym, dq, px):
        p = self.st["pos"].get(sym, {"qty": 0.0, "entry": px})
        q0 = p["qty"]
        fee = abs(dq) * px * PAPER_FEE
        self.st["cash"] -= fee
        if q0 == 0 or np.sign(q0) == np.sign(dq):          # 늘리기 → 평단 갱신
            q1 = q0 + dq
            p["entry"] = (q0 * p["entry"] + dq * px) / q1
            p["qty"] = q1
        else:                                               # 줄이기/뒤집기 → 실현
            closed = min(abs(dq), abs(q0)) * np.sign(q0)
            self.st["cash"] += closed * (px - p["entry"])
            q1 = q0 + dq
            p["qty"] = q1
            if np.sign(q1) != np.sign(q0) and q1 != 0:
                p["entry"] = px
        if abs(p["qty"]) < 1e-12:
            self.st["pos"].pop(sym, None)
        else:
            self.st["pos"][sym] = p

    async def position_qty(self, sym):
        if self.dry:
            return self.st["pos"].get(sym, {}).get("qty", 0.0)
        for p in await self.ex.fetch_positions([sym]):
            q = float(p.get("contracts") or 0)
            if q:
                return q if p["side"] == "long" else -q
        return 0.0

    async def rebalance(self, sym, d, px):
        cur, tgt = d["cur_qty"], d["target_qty"]
        if cur != 0 and np.sign(cur) != np.sign(tgt):       # 뒤집기/정리: 먼저 전량 청산
            await self.market(sym, -cur, True, px)
            cur = await self.position_qty(sym)               # 체결을 확인한다. 남았으면 반대로 열지 않는다
            if cur != 0:
                logging.error(f"[{sym}] Error: 청산 후에도 {cur} 남음 — 이번엔 반대 방향 진입을 하지 않는다")
                return
        delta = tgt - cur
        if delta == 0:
            return
        reducing = cur != 0 and abs(tgt) < abs(cur)
        await self.market(sym, delta, reducing, px)

    async def cancel_algos(self, sym, keep=None):
        """이 심볼의 조건부 주문을 지운다 (keep 은 남긴다). 응답의 symbol 을 직접 확인한다."""
        mid = self.ex.market(sym)["id"]
        opened = await self.ex.fapiPrivateGetOpenAlgoOrders({})
        rows = opened if isinstance(opened, list) else opened.get("orders", [])
        for o in rows:
            if o.get("symbol") != mid or str(o.get("algoId")) == str(keep):
                continue
            await self.ex.fapiPrivateDeleteAlgoOrder({"algoId": str(o["algoId"])})

    async def refresh_stop(self, sym, qty, d):
        """새 STOP_MARKET 을 **먼저** 걸고, 그 다음 예전 것을 지운다 — 손절이 없는 순간을 만들지 않는다."""
        if qty == 0:
            if not self.dry:
                try:
                    await self.cancel_algos(sym)             # 정리된 종목에 남은 손절 청소
                except Exception as e:
                    logging.warning(f"[{sym}] 손절 청소 실패: {type(e).__name__}: {e}")
            return True
        side = np.sign(qty)                                  # 실제 보유 방향으로 계산 (목표 방향 아님)
        stop = float(S.stop_price(d["close"], side, d["vol_d"]))
        if self.dry:
            self.st.setdefault("stops", {})[sym] = stop
            logging.info(f"[{sym}] (paper) 재난 손절 {stop:.2f}")
            return True
        sp = float(self.ex.price_to_precision(sym, stop))
        amt = precise_amount(self.ex, sym, qty)
        try:
            o = await self.ex.create_order(sym, "STOP_MARKET", "sell" if side > 0 else "buy", amt, None,
                                           {"stopPrice": sp, "reduceOnly": True})
            logging.info(f"[{sym}] 재난 손절 {amt} @ {sp} → {o.get('id')}")
        except Exception as e:
            logging.error(f"[{sym}] Error: 손절 주문 실패 {type(e).__name__}: {e} — 포지션 정리 (규칙 3.5)")
            px = (await self.prices())[sym]
            await self.market(sym, -qty, True, px)
            return False
        try:
            await self.cancel_algos(sym, keep=o.get("id"))
        except Exception as e:
            logging.warning(f"[{sym}] 예전 손절 취소 실패 (새 손절은 걸려 있음): {type(e).__name__}: {e}")
        return True

    # ── 하루 한 번 ─────────────────────────────────────
    async def run_once(self):
        if self.st.get("halted"):
            logging.error("킬스위치로 정지된 상태다. 확인 후 --reset-kill 로 푼다.")
            return
        self.skipped = []
        close = await self.fetch_close()
        if self.dry:
            self.paper_stops()
        px = await self.prices()
        equity, pos = await self.account(px)
        self.rebase_peak()
        peak = max(self.st.get("peak") or equity, equity)
        self.st["peak"] = peak
        dd = equity / peak - 1
        if dd <= -S.KILL_DD:
            logging.error(f"킬스위치: 자본 {equity:.2f} 고점 {peak:.2f} 대비 {dd:.1%} — 전부 정리하고 멈춘다")
            for sym, q in pos.items():
                await self.market(sym, -q, True, px[sym])
            for sym in S.SYMBOLS:
                if not self.dry:
                    try:
                        await self.cancel_algos(sym)
                    except Exception as e:
                        logging.warning(f"[{sym}] 킬스위치 손절 청소 실패: {type(e).__name__}: {e}")
            self.st["halted"] = True
            self.save()
            return
        gate = ai_gate.read(GATE_KEY)
        if gate["active"]:
            logging.warning(f"AI 게이트 적용 중: 신규 {'허용' if gate['allow_new'] else '금지'}, "
                            f"비중 x{gate['size_mult']:.2f} — {gate['reason']}")
        orders, bar = plan(close, equity, pos, px, gate)
        for sym, d in orders.items():
            logging.info(f"[{sym}] {bar} 종가 {d['close']:.2f} | 목표 {d['target_w']:+.3f} 현재 {d['cur_w']:+.3f} "
                         f"| {'거래' if d['trade'] else '유지(밴드)'} | 손절 {d['stop']}")
            if d["trade"]:
                await self.rebalance(sym, d, px[sym])
        # 체결 후 실제 보유량으로 손절을 건다
        equity2, pos2 = await self.account(px)
        for sym, d in orders.items():
            await self.refresh_stop(sym, pos2.get(sym, 0.0), d)
        self.st["last_bar"] = bar
        self.save()
        rec = dict(ts=datetime.now(timezone.utc).isoformat(), bar=bar, mode=self.mode, equity=equity2, gate=gate,
                   peak=peak, drawdown=dd, orders={s: {k: v for k, v in d.items()} for s, d in orders.items()},
                   positions=pos2, skipped_min_notional=self.skipped)
        self.write_journal(rec)
        logging.info(f"완료 {bar} | 자본 {equity2:.2f} USDT (고점 대비 {dd:.1%}) | 보유 {pos2}")

    async def loop(self):
        errors = []
        while True:
            now = datetime.now(timezone.utc)
            run_today = now.replace(hour=0, minute=3, second=0, microsecond=0)
            latest_bar = str((now - timedelta(days=1)).date())       # 가장 최근에 마감된 일봉
            late = (now - run_today).total_seconds()
            if self.st.get("last_bar") != latest_bar and 0 <= late <= CATCHUP_HOURS * 3600:
                nxt = now                                              # 마감 직후라 따라잡는다
            else:
                if self.st.get("last_bar") != latest_bar and late > CATCHUP_HOURS * 3600:
                    logging.warning(f"{latest_bar} 마감 후 {late/3600:.1f}시간 지남 — 백테스트와 달라지므로 "
                                    f"오늘 몫은 건너뛰고 다음 마감을 기다린다 (보유 포지션은 거래소 손절이 지킨다)")
                nxt = run_today if now < run_today else run_today + timedelta(days=1)
            wait = max(0.0, (nxt - now).total_seconds())
            if wait:
                logging.info(f"다음 실행 {nxt.isoformat()} ({wait/3600:.1f}시간 뒤)")
                await asyncio.sleep(wait)
            try:
                await self.run_once()
                errors.clear()
            except Exception as e:
                logging.error(f"Error: {type(e).__name__}: {e}")
                t = datetime.now(timezone.utc)
                errors = [x for x in errors if (t - x).total_seconds() < 600] + [t]
                if len(errors) >= 5:              # 규칙 7: 10분 내 예외 5회 → 멈춘다
                    logging.error("10분 안에 예외 5회 — 거래소/네트워크 이상으로 보고 멈춘다")
                    return
                await asyncio.sleep(60)
            await asyncio.sleep(1)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["paper", "testnet", "live"], default="paper")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--maintenance", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--paper-equity", type=float, default=10_000.0)
    ap.add_argument("--reserved", default=os.environ.get("TREND_RESERVED", ""),
                    help="다른 봇 몫인 심볼(쉼표). 가드가 이 포지션을 무시한다.")
    ap.add_argument("--reserve-equity", type=float,
                    default=float(os.environ.get("TREND_RESERVE_EQUITY") or 0),
                    help="다른 봇 몫인 시드(USDT). 자본에서 뺀다.")
    ap.add_argument("--reset-kill", action="store_true")
    ap.add_argument("--i-know", action="store_true")
    a = ap.parse_args()
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True,
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(os.path.join(LOG_DIR, f"trend_vt_{a.mode}.log"), encoding="utf-8")])
    if a.mode == "live" and not a.i_know:
        logging.error("실전은 --i-know 가 필요하다 (STRATEGY_RULES 6: 테스트넷 1주 → 소액 2주 뒤에).")
        return
    lock = config.acquire_run_lock(os.path.join(LOG_DIR, f"trend_vt_{a.mode}.json.lock"))
    if lock is None:
        logging.info("다른 실행이 점검 중 — 중복 실행 건너뜀")
        return
    bot = TrendBot(a.mode, a.paper_equity, parse_symbols(a.reserved), a.reserve_equity)
    if bot.reserved:
        logging.info(f"계정 공유 | 남의 시드 {a.reserve_equity:.0f} 제외 | "
                     f"남의 종목 {sorted(bot.reserved)}")
    if a.reset_kill:
        bot.st.update(halted=False, peak=None)
        bot.save()
        logging.info("킬스위치 해제, 고점 초기화")
        lock.close()
        return
    try:
        await bot.setup(maintenance=a.maintenance)
        if a.maintenance:
            await bot.maintenance()
        elif a.loop:
            await bot.loop()
        else:
            await bot.run_once()
    finally:
        await bot.close_all()
        lock.close()


if __name__ == "__main__":
    asyncio.run(main())
