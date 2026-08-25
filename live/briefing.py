#!/usr/bin/env python3
"""
매일 아침 시장 브리핑 — AI(또는 사람)가 판단하는 데 필요한 숫자만 뽑는다.

  poetry run python live/briefing.py            # 오늘 브리핑 출력
  poetry run python live/briefing.py --save     # logs/briefing_YYYYMMDD.md 로 저장

이 파일은 판단하지 않는다. 판단은 ai_gate.py 로 기록한다.
"""
import os, sys, json, argparse
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.hibernate as H
import strategies.surfer as SF

LOGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ohlcv")
DAY = 86_400_000


def load_daily(min_rows=150):
    import glob
    out = {}
    for p in glob.glob(os.path.join(CACHE, "spot_*_1d.csv")):
        try:
            d = pd.read_csv(p)
        except Exception:
            continue
        if len(d) < min_rows:
            continue
        sym = os.path.basename(p)[len("spot_"):-len("_1d.csv")].replace("_", "/")
        out[sym] = d.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return out


def market_state(daily=None):
    daily = daily or load_daily()
    btc = daily.get("BTC/USDT")
    if btc is None:
        raise RuntimeError("BTC 일봉이 없다")
    # 오늘의 미완성 봉 제외
    now = int(pd.Timestamp.utcnow().timestamp() * 1000)
    def trim(d):
        return d.iloc[:-1] if int(d["timestamp"].iloc[-1]) + DAY > now else d
    btc = trim(btc)
    c = btc["close"].astype(float)

    rets, above = [], []
    for sym, d in daily.items():
        d = trim(d)
        if len(d) < 110:
            continue
        cc = d["close"].astype(float)
        rets.append(float(cc.iloc[-1] / cc.iloc[-61] - 1))
        above.append(float(cc.iloc[-1]) > float(cc.rolling(100).mean().iloc[-1]))

    return dict(
        asof=str(pd.Timestamp(int(btc["timestamp"].iloc[-1]), unit="ms").date()),
        btc=float(c.iloc[-1]),
        btc_ma50=float(c.rolling(50).mean().iloc[-1]),
        btc_ma100=float(c.rolling(100).mean().iloc[-1]),
        btc_ma200=float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else float("nan"),
        btc_ret20=float(c.iloc[-1] / c.iloc[-21] - 1),
        btc_ret60=float(c.iloc[-1] / c.iloc[-61] - 1),
        btc_vol30=float(c.pct_change().rolling(30).std().iloc[-1] * np.sqrt(365)),
        trendiness=SF.trendiness(c),
        alt_median60=float(np.median(rets)) if rets else float("nan"),
        breadth=float(np.mean(above)) if above else float("nan"),
        n_symbols=len(rets),
    )


def render(st, positions=None):
    L = []
    A = L.append
    A(f"# 시장 브리핑 {st['asof']}  (생성 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)")
    A("")
    A("## 숫자")
    A(f"- BTC {st['btc']:,.0f}  |  50일선 {st['btc_ma50']:,.0f} "
      f"({'위' if st['btc'] > st['btc_ma50'] else '아래'})  "
      f"100일선 {st['btc_ma100']:,.0f} ({'위' if st['btc'] > st['btc_ma100'] else '아래'})  "
      f"200일선 {st['btc_ma200']:,.0f} ({'위' if st['btc'] > st['btc_ma200'] else '아래'})")
    A(f"- BTC 20일 {st['btc_ret20']*100:+.1f}%  60일 {st['btc_ret60']*100:+.1f}%  "
      f"30일 변동성(연율) {st['btc_vol30']*100:.0f}%")
    A(f"- **BTC 추세성 {st['trendiness']:.3f}**  (1=한 방향, 0=횡보 / SURFER 기준 >{SF.TREND_MIN})")
    A(f"- **알트 60일 수익 중앙값 {st['alt_median60']*100:+.1f}%**  "
      f"(SURFER 기준 >{SF.ALT_MEDIAN_MIN*100:.0f}%)  — {st['n_symbols']}종")
    A(f"- 알트 폭(100일선 위 비율) {st['breadth']*100:.0f}%")
    A("")
    A("## 전략별 자동 판정")
    hib = st["btc"] > st["btc_ma100"]
    srf = SF.market_gate(st["alt_median60"], st["trendiness"])
    A(f"- HIBERNATE 신규 진입: **{'허용' if hib else '금지'}** (BTC {H.REGIME_MA_DAYS}일선 "
      f"{'위' if hib else '아래'})")
    A(f"- SURFER 신규 진입: **{'허용' if srf else '금지'}** "
      f"(알트중앙값 {st['alt_median60']*100:+.1f}% / 추세성 {st['trendiness']:.2f})")
    if positions:
        A("")
        A("## 보유")
        for sym, p in positions.items():
            A(f"- {sym}  진입 {p.get('entry')}  목표 +{p.get('target_gain',0)*100:.0f}%"
              f"{' (러너)' if p.get('runner') else ''}")
    A("")
    A("## AI 판단이 필요한 것")
    A("- 위 자동 판정을 **더 조일** 이유가 있는가? (뉴스, 규제, 거래소 사고, 대형 청산, 스테이블 디페그 등)")
    A("- 있다면 `live/ai_gate.py` 로 기록한다. **줄이는 방향만 가능하다** (막기 / 비중 축소).")
    A("- 없으면 아무것도 하지 않는다 — 검증된 자동 판정이 그대로 돈다.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    st = market_state()
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2)); return
    txt = render(st)
    print(txt)
    if a.save:
        os.makedirs(LOGS, exist_ok=True)
        p = os.path.join(LOGS, f"briefing_{st['asof'].replace('-','')}.md")
        open(p, "w", encoding="utf-8").write(txt)
        print(f"\n저장: {p}")


if __name__ == "__main__":
    main()
