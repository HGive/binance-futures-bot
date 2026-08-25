#!/usr/bin/env python3
"""
시드로 이 전략을 그대로 돌릴 수 있는가 — 최소 주문금액 검사.

바이낸스 현물 USDT 페어 484종 중 455종이 최소 주문 5 USDT 다.
2단 익절/부분 익절은 포지션을 쪼개서 팔기 때문에 **파는 쪽이 먼저 막힌다.**
검증한 대로 못 돌릴 거면 조용히 다르게 돌리지 말고 여기서 막는다.

  poetry run python live/sizing.py --equity 44
"""
import os, sys, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIN_NOTIONAL = 5.0          # 바이낸스 현물 USDT 페어 대부분
SAFETY = 1.15               # 가격이 흔들려도 통과하도록 여유


def check(name, equity, ticker_pct, max_conc, sell_fracs):
    """sell_fracs: 포지션을 쪼개서 파는 비중들 (예: [0.7, 0.3])"""
    pos = equity * ticker_pct
    need_buy = MIN_NOTIONAL * SAFETY
    smallest = min(sell_fracs) if sell_fracs else 1.0
    out = {
        "strategy": name, "equity": equity, "position": pos,
        "ok_buy": pos >= need_buy,
        "ok_sell": pos * smallest >= need_buy,
        "min_equity_buy": need_buy / ticker_pct,
        "min_equity_sell": need_buy / (ticker_pct * smallest),
        "exposure": ticker_pct * max_conc,
        "slots_affordable": int(equity // need_buy),
    }
    out["ok"] = out["ok_buy"] and out["ok_sell"]
    return out


def report(c):
    ok = "가능" if c["ok"] else "불가"
    print(f"[{c['strategy']}] 시드 {c['equity']:.0f} USDT → {ok}")
    print(f"   1종목 주문금액 {c['position']:.2f} USDT  (최소 {MIN_NOTIONAL} × 여유 {SAFETY:.2f} = {MIN_NOTIONAL*SAFETY:.2f})")
    if not c["ok_buy"]:
        print(f"   ✗ 매수부터 미달 — 이 전략을 그대로 돌리려면 최소 {c['min_equity_buy']:.0f} USDT")
    elif not c["ok_sell"]:
        print(f"   ✗ 쪼개 파는 쪽이 미달 — 최소 {c['min_equity_sell']:.0f} USDT")
    else:
        print(f"   ✓ 매수·분할매도 모두 통과 (동시 {c['slots_affordable']}종목까지 감당)")


SPECS = {}


def load_specs():
    import strategies.hibernate as H
    import strategies.surfer as SF
    return {
        "hibernate": ("HIBERNATE", H.TICKER_MARGIN_PCT, H.MAX_CONCURRENT,
                      [H.SPLIT_AT_FIRST, 1 - H.SPLIT_AT_FIRST]),
        "surfer": ("SURFER", SF.TICKER_PCT, SF.MAX_CONCURRENT, [0.5, 0.5]),
    }


def check_strategy(key, equity):
    name, pct, conc, fr = load_specs()[key]
    return check(name, equity, pct, conc, fr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", type=float, required=True)
    a = ap.parse_args()
    print(f"최소 주문금액 {MIN_NOTIONAL} USDT 기준 (여유 {SAFETY:.0%})\n")
    for k in load_specs():
        report(check_strategy(k, a.equity)); print()
    print("참고 — 전략을 그대로 돌리기 위한 최소 시드")
    for k in load_specs():
        c = check_strategy(k, 100000)
        print(f"  {c['strategy']:<12} {c['min_equity_sell']:>6.0f} USDT")
