#!/usr/bin/env python3
"""
여러 전략을 한 프로세스에서 돌린다.

  poetry run python live/runner.py --mode paper                    # 신호만
  poetry run python live/runner.py --mode testnet                  # 테스트넷 실주문
  poetry run python live/runner.py --mode testnet --once           # 1회 점검 후 종료
  poetry run python live/runner.py --mode paper --strategies hibernate

일봉은 전략들이 공유한다 (같은 데이터를 두 번 받지 않는다).
스캘핑은 검증된 진입 규칙이 없어서 매매하지 않는다 — live/scalp_collector.py 로 데이터만 모은다.
"""
import os, sys, time, argparse, logging, traceback
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.engine import Engine
from live import adapters, sizing


def next_utc_midnight():
    n = datetime.now(timezone.utc)
    return (n + timedelta(days=1)).replace(hour=0, minute=2, second=0, microsecond=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["paper", "testnet", "live"], default="paper")
    ap.add_argument("--strategies", default="hibernate,surfer")
    ap.add_argument("--seed", type=float, default=1000.0, help="paper 모드 시드")
    ap.add_argument("--top", type=int, default=500,
                    help="백테스트는 542종을 봤다. 400 으로 자르면 298종만 본다")
    ap.add_argument("--fast", type=int, default=300, help="보유 점검 주기(초)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--i-know", action="store_true", help="live 모드 확인")
    ap.add_argument("--force-entry", default=None, help="테스트넷 주문 경로 점검: 신호 무시하고 매수")
    ap.add_argument("--force-strategy", default="hibernate")
    ap.add_argument("--force-usdt", type=float, default=None)
    ap.add_argument("--close-all", action="store_true")
    ap.add_argument("--status", action="store_true", help="상태만 출력하고 종료 (감독용, 매매 안 함)")
    a = ap.parse_args()

    if a.mode == "live" and not a.i_know:
        print("실전은 --i-know 를 붙여라. STRATEGY_RULES.md 6절 관문 먼저 확인."); sys.exit(1)
    os.environ.setdefault("LOG_FILENAME", f"runner_{a.mode}.log")

    keys = [k.strip() for k in a.strategies.split(",") if k.strip()]
    keys = [k for k in keys if getattr(adapters.get(k), "trades", True)]
    if not keys:
        print("돌릴 전략이 없다"); sys.exit(1)

    shared = {}
    engines = []
    for k in keys:
        engines.append(Engine(k, a.mode, seed=a.seed, top=a.top, shared=shared))

    if a.status:
        from live import ai_gate
        out = []
        for e in engines:
            try:
                eq = e.equity()
            except Exception as ex:
                out.append(f"### {e.A.name}: 거래소 조회 실패 — {ex}"); continue
            g = ai_gate.read(e.A.key)
            if e.state.pos:
                try:
                    e.refresh(list(e.state.pos))
                except Exception as ex:
                    out.append(f"  (시세 갱신 실패: {ex})")
            out.append(f"### {e.A.name}  (모드 {a.mode})")
            out.append(f"- 평가액 {eq:,.2f} USDT / 현금 {e.free_usdt():,.2f}")
            out.append(f"- 게이트: {'활성' if g['active'] else '없음'} "
                       f"신규진입 {'허용' if g['allow_new'] else '금지'} 비중x{g['size_mult']}"
                       + (f" — {g['reason']}" if g.get('reason') else ""))
            ps = e.state.pos
            out.append(f"- 보유 {len(ps)}종 / 칸 {e.A.max_concurrent}")
            for sym, p in ps.items():
                px = e.last_price(sym)
                pnl = (px / p["entry"] - 1) if px and p.get("entry") else float("nan")
                out.append(f"    {sym}  진입 {p.get('entry')}  현재 {px}  "
                           f"손익 {pnl*100:+.1f}%  손절 {p.get('stop')}  "
                           f"거래소손절 {'O' if p.get('exchange_stop') else 'X'}")
            hist = e.state.d.get("history", [])[-5:]
            if hist:
                out.append("- 최근 청산 5건: " + ", ".join(
                    f"{h.get('sym')}({h.get('reason')},{h.get('ret',0)*100:+.1f}%)" for h in hist))
            upd = e.state.d.get("updated")
            out.append(f"- 상태 갱신 {upd}")
        print("\n".join(out))
        return

    if a.close_all:
        for e in engines:
            for sym in list(e.state.pos):
                e.refresh([sym]); e.close(sym, "MANUAL", 1.0)
        logging.info("전량 정리 완료"); return

    if a.force_entry:
        if a.mode == "live":
            print("--force-entry 는 실전에서 못 쓴다."); sys.exit(1)
        e = next(x for x in engines if x.A.key == a.force_strategy)
        sym = a.force_entry
        e.refresh([sym, "BTC/USDT"]); e.compute()
        if sym not in e.daily:
            logging.error(f"{sym} 일봉을 못 받았다"); sys.exit(1)
        i = len(e.daily[sym]) - 1
        amt = a.force_usdt or max(e.equity() * e.A.ticker_pct, 15.0)
        logging.warning(f"[강제 진입] 신호 무시하고 {sym} 를 {amt:.2f} USDT — 주문 경로 점검용")
        ok = e.enter(sym, i, amt)
        logging.info("주문 경로 점검 " + ("성공" if ok else "실패") + f" / 상태: {e.state.path}")
        return

    # 시드 점검
    logging.info("=" * 64)
    for e in engines:
        eq = e.equity()
        c = sizing.check(e.A.name, eq, e.A.ticker_pct, e.A.max_concurrent, e.A.sell_fracs)
        sizing.report(c)
    logging.info("=" * 64)

    univ = engines[0].universe()
    for e in engines:
        e.daily_tick(univ)
    if a.once:
        return

    nxt = next_utc_midnight()
    while True:
        try:
            time.sleep(a.fast)
            for e in engines:
                e.fast_tick()
            if datetime.now(timezone.utc) >= nxt:
                shared.clear()
                univ = engines[0].universe()
                for e in engines:
                    e.daily_tick(univ)
                nxt = next_utc_midnight()
        except KeyboardInterrupt:
            logging.info("중단"); break
        except Exception:
            logging.error("루프 예외:\n" + traceback.format_exc())
            time.sleep(30)


if __name__ == "__main__":
    main()
