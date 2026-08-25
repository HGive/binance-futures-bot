#!/usr/bin/env python3
"""
데모(테스트넷) 키가 제대로 들어갔는지, 어느 쪽 키인지 확인한다.

바이낸스는 데모가 두 개로 나뉜다. 헷갈리면 -2015 오류가 난다.
  현물 데모  https://testnet.binance.vision      ← HIBERNATE / SURFER 가 쓰는 것
  선물 데모  https://testnet.binancefuture.com   ← 지금은 불필요

  poetry run python live/check_keys.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import ccxt

TARGETS = [
    ("현물 데모", "spot", True, "BINANCE_SPOT_TESTNET_KEY", "BINANCE_SPOT_TESTNET_SECRET",
     "https://testnet.binance.vision", "HIBERNATE / SURFER"),
    ("선물 데모", "futures", True, "BINANCE_TESTNET_KEY", "BINANCE_TESTNET_SECRET",
     "https://testnet.binancefuture.com", "스캘핑"),
    ("실전 현물", "spot", False, "BINANCE_API_KEY", "BINANCE_API_SECRET",
     "https://www.binance.com", ""),
]


def probe(market, testnet, key, secret):
    import config
    os.environ["_TMP_K"], os.environ["_TMP_S"] = key, secret
    cls = ccxt.binance if market == "spot" else ccxt.binanceusdm
    ex = cls({"apiKey": key, "secret": secret, "enableRateLimit": True,
              "options": {"adjustForTimeDifference": True, "fetchCurrencies": False}})
    if testnet:
        config._use_testnet(ex, market)      # ccxt 가 선물 sandbox 를 없애서 직접 갈아끼운다
    bal = ex.fetch_balance()
    return ex, bal


def main():
    print("데모/실전 키 점검\n")
    ok_spot_demo = False
    for label, market, testnet, kname, sname, url, needed in TARGETS:
        k, s = os.environ.get(kname, ""), os.environ.get(sname, "")
        tag = f"  ← {needed}" if needed else ""
        if not k or not s:
            print(f"[{label}]{tag}\n   비어 있음 ({kname} / {sname})\n   발급: {url}\n")
            continue
        try:
            ex, bal = probe(market, testnet, k, s)
            free = {a: v for a, v in (bal.get("free") or {}).items() if v and v > 0}
            top = sorted(free.items(), key=lambda x: -x[1])[:6]
            print(f"[{label}]{tag}\n   ✓ 연결 성공 — 잔고: "
                  + (", ".join(f"{a} {v:,.4f}" for a, v in top) if top else "없음"))
            if market == "spot" and testnet:
                ok_spot_demo = True
            if market != "spot" and testnet:
                globals()["ok_fut_demo"] = True
                n = sum(1 for m in ex.load_markets().values()
                        if m.get("spot") and m.get("active") and m.get("quote") == "USDT")
                print(f"   거래 가능한 USDT 페어 {n}종")
            print()
        except ccxt.AuthenticationError as e:
            msg = str(e)
            hint = ""
            if "-2015" in msg:
                hint = ("\n   → 키가 이 서버 것이 아니거나 만료됐다. "
                        f"{url} 에서 새로 발급해라.")
            elif "-1022" in msg or "signature" in msg.lower():
                hint = "\n   → 시크릿이 잘못됐다. 공백이나 줄바꿈이 섞였는지 확인해라."
            print(f"[{label}]{tag}\n   ✗ 인증 실패: {msg[:120]}{hint}\n")
        except Exception as e:
            print(f"[{label}]{tag}\n   ✗ {type(e).__name__}: {str(e)[:150]}\n")

    print("─" * 60)
    if globals().get("ok_fut_demo"):
        print("선물 데모 준비 완료 → 스캘핑 데이터 수집에 쓴다:")
        print("  poetry run python live/scalp_collector.py --market futures --top 20 --hours 24")
        print()
    if ok_spot_demo:
        print("현물 데모 준비 완료. 다음:")
        print("  poetry run python live/runner.py --mode testnet --once")
    else:
        print("현물 데모 키가 필요하다. https://testnet.binance.vision 에서")
        print("GitHub 로그인 → Generate HMAC_SHA256 Key → .env 의")
        print("BINANCE_SPOT_TESTNET_KEY / BINANCE_SPOT_TESTNET_SECRET 에 넣어라.")
        print("\n※ 바이낸스 화면에서 'Demo Trading' 으로 이름이 바뀐 그것이 맞다.")
        print("  단 선물 데모(testnet.binancefuture.com)와 키가 다르다 — 현물 쪽이어야 한다.")


if __name__ == "__main__":
    main()
