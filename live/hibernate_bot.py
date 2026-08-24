#!/usr/bin/env python3
"""
HIBERNATE (동면) 실전 봇 — 바이낸스 현물, 롱 전용.

백테스트(backtest/walkforward.py --style adaptive --regime btc_ma100)와 규칙을 1:1로 맞춘다.

  진입   일봉 마감(UTC 00:00) 직후 스캔 → 조건 통과 종목을 시장가 매수
         후보가 많으면 '조용한 기간(drought)' 이 긴 것부터
         티커당 자본의 10%, 동시 8종목, BTC 100일선 위일 때만
  청산   1차 목표(종목별) 도달 → 70% 매도 / 나머지는 2배(+100%) 까지
         손절 -20% (1차 익절 뒤에도 유지)
         상장폐지 예고 시 즉시 정리

익절은 지정가 주문을 미리 걸어둔다 (백테스트가 그날 고가로 체결한 것과 같은 뜻).
손절은 봇이 감시한다 — 현물은 같은 수량을 익절·손절 주문에 동시에 못 묶기 때문.

사용:
  python live/hibernate_bot.py --mode paper              # 주문 없이 신호만 (실서버 데이터)
  python live/hibernate_bot.py --mode testnet            # 현물 테스트넷에 실제 주문
  python live/hibernate_bot.py --mode live --i-know      # 실전
"""
import os, sys, json, time, math, argparse, logging, traceback
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import strategies.hibernate as S
from backtest import data as D

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
DAY_MS = 86_400_000
WARMUP = 430          # precompute 가 안정되는 최소 봉 수


def drop_incomplete(df, step_ms: int):
    """아직 진행 중인 마지막 봉을 버린다.

    백테스트는 '그날 종가' 로 판정한다. 오늘의 미완성 봉으로 판정하면
    같은 신호가 하루 종일 켜졌다 꺼졌다 하고, 백테스트와도 어긋난다.
    """
    if not len(df):
        return df
    now = int(pd.Timestamp.utcnow().timestamp() * 1000)
    last = int(df["timestamp"].iloc[-1])
    if last + step_ms > now:
        return df.iloc[:-1].reset_index(drop=True)
    return df


def bars_age(df) -> int:
    """마지막 일봉이 며칠 전 것인가. tz 유무에 상관없이 계산한다."""
    last = pd.Timestamp(df["dt"].iloc[-1])
    now = pd.Timestamp.utcnow()
    if last.tzinfo is None:
        now = now.tz_localize(None)
    elif now.tzinfo is None:
        now = now.tz_localize("UTC")
    return int((now - last).total_seconds() // 86400)


# ────────────────────────────── 상태 ──────────────────────────────
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
        self.d.setdefault("positions", {})
        self.d.setdefault("history", [])
        self.d.setdefault("halted", False)

    def save(self):
        self.d["updated"] = datetime.now(timezone.utc).isoformat()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)      # 쓰다 죽어도 기존 파일이 남게

    @property
    def pos(self):
        return self.d["positions"]


# ────────────────────────────── 봇 ──────────────────────────────
class HibernateBot:
    def __init__(self, mode, seed_usdt=None, top=400, quote="USDT"):
        self.mode = mode                        # paper | testnet | live
        self.dry = (mode == "paper")
        self.top = top
        self.quote = quote
        self.seed_override = seed_usdt
        self.data_ex = config.public_exchange("spot")       # 과거 봉: 항상 실서버
        self.data_ex.load_markets()
        self.ex = None
        if not self.dry:
            self.ex = config.make_exchange("spot", testnet=(mode == "testnet"),
                                           use_pro=False, need_keys=True)
            self.ex.load_markets()
        self.state = State(os.path.join(STATE_DIR, f"hibernate_{mode}.json"))
        self.daily = {}      # sym -> DataFrame
        self.pre = {}        # sym -> precompute dict

    # ── 데이터 ────────────────────────────────────────────────
    def universe(self):
        """주문 가능한 현물 USDT 페어 중 거래대금 상위."""
        src = self.ex if self.ex else self.data_ex
        try:
            tickers = src.fetch_tickers()
        except Exception as e:
            logging.warning(f"tickers 실패({e}) — 실서버로 대체")
            tickers = self.data_ex.fetch_tickers()
        rows = []
        for sym, m in src.markets.items():
            if not m.get("spot") or not m.get("active") or m.get("quote") != self.quote:
                continue
            t = tickers.get(sym) or {}
            rows.append((sym, t.get("quoteVolume") or 0))
        rows.sort(key=lambda r: -r[1])
        return [s for s, _ in rows[: self.top]]

    def refresh(self, syms):
        """일봉 캐시를 오늘까지 채운다. 실패한 종목은 건너뛴다."""
        D.MARKET = "spot"
        ok, fail = 0, []
        for i, sym in enumerate(syms, 1):
            try:
                df = D.load(sym, "1d", years=3.0, ex=self.data_ex, topup=True)
                df = drop_incomplete(df, DAY_MS)
                if len(df) < WARMUP:
                    continue
                if "qv" not in df:
                    df["qv"] = df["volume"] * df["close"]
                self.daily[sym] = df.reset_index(drop=True)
                ok += 1
            except Exception as e:
                fail.append(f"{sym}:{type(e).__name__}")
            if i % 50 == 0:
                logging.info(f"  일봉 {i}/{len(syms)} …")
        logging.info(f"일봉 준비 {ok}종 (실패 {len(fail)})")
        if fail:
            logging.info(f"  실패 예: {fail[:5]}")

    def compute(self):
        self.pre = {}
        for sym, df in self.daily.items():
            try:
                self.pre[sym] = S.precompute(df)
            except Exception as e:
                logging.warning(f"[{sym}] precompute 실패: {e}")

    def btc_ok(self):
        b = self.daily.get("BTC/USDT")
        if b is None or len(b) < S.REGIME_MA_DAYS + 1:
            logging.warning("BTC 일봉이 부족하다 — 신규 진입 보류")
            return False
        ma = b["close"].rolling(S.REGIME_MA_DAYS).mean().iloc[-1]
        px = b["close"].iloc[-1]
        up = bool(px > ma)
        logging.info(f"BTC {px:,.0f} / {S.REGIME_MA_DAYS}일선 {ma:,.0f} → 신규 진입 {'허용' if up else '금지'}")
        return up

    # ── 계좌 ──────────────────────────────────────────────────
    def equity(self):
        """총자산(USDT 환산). paper 는 상태파일로 추적."""
        if self.dry:
            eq = self.state.d.get("paper_cash", self.seed_override or 1000.0)
            for sym, p in self.state.pos.items():
                px = self.last_price(sym) or p["entry"]
                eq += p["qty"] * px
            return eq
        bal = self.ex.fetch_balance()
        total = float(bal.get(self.quote, {}).get("total") or 0)
        for sym, p in self.state.pos.items():
            px = self.last_price(sym) or p["entry"]
            base = sym.split("/")[0]
            q = float(bal.get(base, {}).get("total") or 0)
            total += q * px
        return total

    def free_usdt(self):
        if self.dry:
            return self.state.d.get("paper_cash", self.seed_override or 1000.0)
        return float(self.ex.fetch_balance().get(self.quote, {}).get("free") or 0)

    def last_price(self, sym):
        df = self.daily.get(sym)
        if df is not None and len(df):
            return float(df["close"].iloc[-1])
        return None

    def live_price(self, sym):
        src = self.ex if self.ex else self.data_ex
        try:
            return float(src.fetch_ticker(sym)["last"])
        except Exception:
            return self.last_price(sym)

    # ── 주문 ──────────────────────────────────────────────────
    def _mkt(self, sym):
        src = self.ex if self.ex else self.data_ex
        return src.market(sym)

    def _fits(self, sym, amount, price):
        """수량·금액 최소 단위를 통과하는가."""
        m = self._mkt(sym)
        lim = m.get("limits", {})
        amin = (lim.get("amount") or {}).get("min")
        cmin = (lim.get("cost") or {}).get("min")
        if amin and amount < amin:
            return False, f"수량 최소 {amin} 미달"
        if cmin and amount * price < cmin:
            return False, f"주문금액 최소 {cmin} 미달"
        return True, ""

    def buy_market(self, sym, usdt):
        px = self.live_price(sym)
        src = self.ex if self.ex else self.data_ex
        amount = float(src.amount_to_precision(sym, usdt / px))
        ok, why = self._fits(sym, amount, px)
        if not ok:
            return None, why
        if self.dry:
            fill = px * (1 + S.SLIPPAGE)
            self.state.d["paper_cash"] = self.free_usdt() - amount * fill
            return {"filled": amount, "average": fill}, ""
        o = self.ex.create_order(sym, "market", "buy", amount)
        o = self._settle(sym, o)
        # 현물 매수 수수료는 코인으로 빠진다 — 실제 보유 수량을 다시 읽는다
        time.sleep(1.0)
        base = sym.split("/")[0]
        free = float(self.ex.fetch_balance().get(base, {}).get("free") or 0)
        got = min(float(o.get("filled") or amount), free)
        o["filled"] = got
        return o, ""

    def sell_market(self, sym, amount):
        if self.dry:
            px = self.live_price(sym) * (1 - S.SLIPPAGE)
            self.state.d["paper_cash"] = self.free_usdt() + amount * px
            return {"filled": amount, "average": px}
        base = sym.split("/")[0]
        free = float(self.ex.fetch_balance().get(base, {}).get("free") or 0)
        amt = float(self.ex.amount_to_precision(sym, min(amount, free)))
        if amt <= 0:
            return None
        return self._settle(sym, self.ex.create_order(sym, "market", "sell", amt))

    def sell_limit(self, sym, amount, price):
        if self.dry:
            return {"id": f"paper-{sym}-{price:.8f}"}
        base = sym.split("/")[0]
        free = float(self.ex.fetch_balance().get(base, {}).get("free") or 0)
        amt = float(self.ex.amount_to_precision(sym, min(amount, free)))
        prc = float(self.ex.price_to_precision(sym, price))
        ok, why = self._fits(sym, amt, prc)
        if not ok:
            logging.warning(f"[{sym}] 지정가 매도 불가 — {why}")
            return None
        return self.ex.create_order(sym, "limit", "sell", amt, prc)

    def cancel(self, sym, oid):
        if self.dry or not oid:
            return
        try:
            self.ex.cancel_order(oid, sym)
        except Exception as e:
            logging.info(f"[{sym}] 주문 {oid} 취소 실패(이미 체결/취소?): {e}")

    def _settle(self, sym, o):
        """시장가 주문의 실제 체결가를 채워 넣는다."""
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

    # ── 진입 ──────────────────────────────────────────────────
    def candidates(self):
        out = []
        held = set(self.state.pos)
        for sym, df in self.daily.items():
            if sym in held:
                continue
            pre = self.pre.get(sym)
            if pre is None:
                continue
            i = len(df) - 1
            if i < WARMUP:
                continue
            # 일봉이 이틀 이상 밀려 있으면 거래정지/상폐 신호로 본다
            if bars_age(df) > 2:
                continue
            try:
                if S.entry_signal(pre, i, float(df["close"].iloc[i])):
                    out.append((int(pre["drought"][i]), sym, i))
            except Exception:
                continue
        out.sort(reverse=True)          # 오래 조용했던 것부터
        return out

    def enter(self, sym, i, budget):
        pre = self.pre[sym]
        tg = S.target_gain(pre, i)
        o, why = self.buy_market(sym, budget)
        if o is None:
            logging.info(f"[{sym}] 진입 건너뜀 — {why}")
            return False
        entry = float(o["average"])
        qty = float(o["filled"])
        p = {"qty": qty, "entry": entry, "target_gain": tg,
             "tp1": entry * (1 + tg), "tp2": entry * (1 + S.DOUBLE_TP),
             "stop": entry * (1 + S.STOP_PCT), "runner": False,
             "opened": datetime.now(timezone.utc).isoformat(),
             "drought": int(pre["drought"][i]), "tp1_id": None, "tp2_id": None,
             "budget": budget}
        self.state.pos[sym] = p
        self.place_exits(sym)
        self.state.save()
        logging.info(f"★ 진입 {sym}  {qty:.6g} @ {entry:.8g}  ({budget:.1f} USDT)  "
                     f"목표 +{tg*100:.0f}% → {p['tp1']:.8g} / 2배 {p['tp2']:.8g} / 손절 {p['stop']:.8g}  "
                     f"조용 {p['drought']}일")
        return True

    def place_exits(self, sym):
        """익절 지정가를 걸어둔다. 1차 70% + 2차 30%."""
        p = self.state.pos[sym]
        if p["runner"]:
            o = self.sell_limit(sym, p["qty"], p["tp2"])
            p["tp2_id"] = o["id"] if o else None
            return
        q1 = p["qty"] * S.SPLIT_AT_FIRST
        q2 = p["qty"] - q1
        o1 = self.sell_limit(sym, q1, p["tp1"])
        p["tp1_id"] = o1["id"] if o1 else None
        o2 = self.sell_limit(sym, q2, p["tp2"])
        p["tp2_id"] = o2["id"] if o2 else None
        if not o1:
            logging.warning(f"[{sym}] 1차 익절 주문 실패 — 봇이 가격으로 감시한다")

    # ── 청산 ──────────────────────────────────────────────────
    def close(self, sym, reason, frac=1.0):
        p = self.state.pos.get(sym)
        if not p:
            return
        self.cancel(sym, p.get("tp1_id"))
        self.cancel(sym, p.get("tp2_id"))
        p["tp1_id"] = p["tp2_id"] = None
        q = p["qty"] * frac
        o = self.sell_market(sym, q)
        px = float(o["average"]) if o and o.get("average") else self.live_price(sym)
        pnl = q * (px - p["entry"])
        roi = (px / p["entry"] - 1)
        self.state.d["history"].append(
            {"symbol": sym, "reason": reason, "qty": q, "entry": p["entry"], "exit": px,
             "pnl": pnl, "roi": roi, "opened": p["opened"],
             "closed": datetime.now(timezone.utc).isoformat()})
        logging.info(f"◆ 청산 {sym} [{reason}] {q:.6g} @ {px:.8g}  ROI {roi*100:+.1f}%  PnL {pnl:+.2f}")
        if frac >= 0.999:
            del self.state.pos[sym]
        else:
            p["qty"] -= q
            p["runner"] = True
            self.place_exits(sym)
        self.state.save()

    def manage(self):
        """보유 종목 점검 — 손절 감시 + 걸어둔 익절 체결 확인."""
        for sym in list(self.state.pos):
            p = self.state.pos[sym]
            px = self.live_price(sym)
            if px is None:
                continue

            # 1) 걸어둔 익절이 체결됐는지
            if not self.dry:
                filled = self._check_exit_orders(sym, p, px)
                if filled:
                    continue

            # 2) paper 는 가격으로 판정
            if self.dry:
                if not p["runner"] and px >= p["tp1"]:
                    self.close(sym, "TP1", S.SPLIT_AT_FIRST)
                    continue
                if p["runner"] and px >= p["tp2"]:
                    self.close(sym, "TP2", 1.0)
                    continue

            # 3) 손절 (1차 익절 뒤에도 유지 — 백테스트와 동일)
            if px <= p["stop"]:
                self.close(sym, "STOP", 1.0)
                continue

            # 4) 상장폐지 예고 — 일봉이 멈췄으면 정리
            df = self.daily.get(sym)
            if df is not None:
                age = bars_age(df)
                if age > 3:
                    logging.warning(f"[{sym}] 일봉이 {age}일째 멈춤 — 정리한다")
                    self.close(sym, "DELISTED", 1.0)

    def _check_exit_orders(self, sym, p, px):
        for key, tag in (("tp1_id", "TP1"), ("tp2_id", "TP2")):
            oid = p.get(key)
            if not oid:
                continue
            try:
                o = self.ex.fetch_order(oid, sym)
            except Exception:
                continue
            if o.get("status") != "closed":
                continue
            q = float(o.get("filled") or 0)
            fill = float(o.get("average") or o.get("price") or px)
            pnl = q * (fill - p["entry"])
            self.state.d["history"].append(
                {"symbol": sym, "reason": tag, "qty": q, "entry": p["entry"], "exit": fill,
                 "pnl": pnl, "roi": fill / p["entry"] - 1, "opened": p["opened"],
                 "closed": datetime.now(timezone.utc).isoformat()})
            logging.info(f"◆ 익절 {sym} [{tag}] {q:.6g} @ {fill:.8g}  ROI {(fill/p['entry']-1)*100:+.1f}%")
            p[key] = None
            p["qty"] = max(p["qty"] - q, 0)
            if tag == "TP1":
                p["runner"] = True
            if p["qty"] <= 0 or tag == "TP2":
                del self.state.pos[sym]
                self.state.save()
                return True
            self.state.save()
        return False

    # ── 하루 1회 ───────────────────────────────────────────────
    def daily_tick(self):
        logging.info("=" * 64)
        logging.info(f"HIBERNATE v{S.VERSION} [{self.mode}] 일일 점검 "
                     f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
        syms = self.universe()
        logging.info(f"유니버스 {len(syms)}종")
        need = sorted(set(syms) | set(self.state.pos) | {"BTC/USDT"})
        self.refresh(need)
        self.compute()

        self.manage()

        eq = self.equity()
        free = self.free_usdt()
        logging.info(f"자산 {eq:,.2f} USDT / 현금 {free:,.2f} / 보유 {len(self.state.pos)}종")
        for s, p in self.state.pos.items():
            px = self.live_price(s) or p["entry"]
            logging.info(f"   {s:<16} {(px/p['entry']-1)*100:+6.1f}%  목표 +{p['target_gain']*100:.0f}%"
                         f"{' (러너)' if p['runner'] else ''}")

        if self.state.d.get("halted"):
            logging.warning("정지 상태 — 신규 진입 없음")
            return
        if len(self.state.pos) >= S.MAX_CONCURRENT:
            logging.info(f"자리 {S.MAX_CONCURRENT}개 다 참 — 신규 진입 없음")
            return
        if not self.btc_ok():
            return

        budget = eq * S.TICKER_MARGIN_PCT
        cands = self.candidates()
        logging.info(f"진입 후보 {len(cands)}종, 1종목당 {budget:,.2f} USDT")
        for dr, sym, i in cands[:20]:
            logging.info(f"   후보 {sym:<16} 조용 {dr}일  목표 +{S.target_gain(self.pre[sym], i)*100:.0f}%")
        for _, sym, i in cands:
            if len(self.state.pos) >= S.MAX_CONCURRENT:
                break
            if self.free_usdt() < budget:
                logging.info("현금 부족 — 진입 중단")
                break
            self.enter(sym, i, budget)
        self.state.save()

    def fast_tick(self):
        """손절·익절 체결 감시. 데이터 재수집 없이 현재가만 본다."""
        if not self.state.pos:
            return
        self.manage()


def next_utc_midnight():
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=2, second=0, microsecond=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["paper", "testnet", "live"], default="paper")
    ap.add_argument("--once", action="store_true", help="일일 점검 1회만 하고 종료")
    ap.add_argument("--seed", type=float, default=1000.0, help="paper 모드 시드")
    ap.add_argument("--top", type=int, default=400)
    ap.add_argument("--fast", type=int, default=300, help="손절 감시 주기(초)")
    ap.add_argument("--i-know", action="store_true", help="live 모드 확인")
    ap.add_argument("--force-entry", default=None,
                    help="테스트넷 주문 경로 점검용. 신호와 무관하게 이 심볼을 산다 (예: BTC/USDT)")
    ap.add_argument("--force-usdt", type=float, default=None, help="--force-entry 금액")
    ap.add_argument("--close-all", action="store_true", help="보유 전량 시장가 정리")
    args = ap.parse_args()

    if args.mode == "live" and not args.i_know:
        print("실전은 --i-know 를 붙여라. STRATEGY_RULES.md 6절 관문을 먼저 통과했는지 확인할 것.")
        sys.exit(1)

    os.environ.setdefault("LOG_FILENAME", f"hibernate_{args.mode}.log")
    bot = HibernateBot(args.mode, seed_usdt=args.seed, top=args.top)
    if args.mode == "paper" and "paper_cash" not in bot.state.d:
        bot.state.d["paper_cash"] = args.seed
        bot.state.save()

    if args.close_all:
        for sym in list(bot.state.pos):
            bot.close(sym, "MANUAL", 1.0)
        logging.info("전량 정리 완료")
        return

    if args.force_entry:
        if args.mode == "live":
            print("--force-entry 는 실전에서 못 쓴다."); sys.exit(1)
        sym = args.force_entry
        bot.refresh([sym, "BTC/USDT"])
        bot.compute()
        if sym not in bot.daily:
            logging.error(f"{sym} 일봉을 못 받았다"); sys.exit(1)
        i = len(bot.daily[sym]) - 1
        amt = args.force_usdt or max(bot.equity() * S.TICKER_MARGIN_PCT, 15.0)
        logging.warning(f"[강제 진입] 신호를 무시하고 {sym} 를 {amt:.2f} USDT 산다 — 주문 경로 점검용")
        ok = bot.enter(sym, i, amt)
        logging.info("주문 경로 점검 " + ("성공" if ok else "실패"))
        logging.info(f"상태 파일: {bot.state.path}")
        return

    if args.once:
        bot.daily_tick()
        return

    bot.daily_tick()
    nxt = next_utc_midnight()
    while True:
        try:
            time.sleep(args.fast)
            bot.fast_tick()
            if datetime.now(timezone.utc) >= nxt:
                bot.daily_tick()
                nxt = next_utc_midnight()
        except KeyboardInterrupt:
            logging.info("중단")
            break
        except Exception:
            logging.error("루프 예외:\n" + traceback.format_exc())
            time.sleep(30)


if __name__ == "__main__":
    main()
