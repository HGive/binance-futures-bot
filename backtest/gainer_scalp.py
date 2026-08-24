"""
오늘 가장 많이 오른 종목 10개 → 다음 구간 롱 스캘핑.

1단계(이 파일): 일봉으로 전제 검증.
  "오늘 급등한 종목은 다음날에도 위로 갈 확률이 높은가?"
  전제가 깨지면 분봉을 받을 이유가 없다.
"""
import os, glob, sys
import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ohlcv")


def load_daily(min_rows=200):
    out = {}
    for p in glob.glob(os.path.join(CACHE, "spot_*_1d.csv")):
        sym = os.path.basename(p)[len("spot_"):-len("_1d.csv")]
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        if len(df) < min_rows:
            continue
        df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        out[sym] = df
    return out


def build_panel(daily):
    """timestamp 기준으로 종목별 지표를 합친 long-format 표."""
    frames = []
    for sym, df in daily.items():
        d = df.copy()
        d["sym"] = sym
        d["chg"] = d["close"] / d["close"].shift(1) - 1.0
        d["qv"] = d["volume"] * d["close"]            # 대략적 거래대금
        d["qv20"] = d["qv"].rolling(20).mean()
        # 다음날 정보
        d["n_open"] = d["open"].shift(-1)
        d["n_high"] = d["high"].shift(-1)
        d["n_low"] = d["low"].shift(-1)
        d["n_close"] = d["close"].shift(-1)
        frames.append(d[["timestamp", "sym", "close", "chg", "qv", "qv20",
                         "n_open", "n_high", "n_low", "n_close"]])
    p = pd.concat(frames, ignore_index=True)
    return p.dropna(subset=["chg", "n_open", "qv20"])


def main():
    top_n = int(os.environ.get("TOPN", 10))
    min_qv = float(os.environ.get("MINQV", 3e6))
    daily = load_daily()
    print(f"일봉 종목 {len(daily)}개 로드")
    p = build_panel(daily)
    p = p[p["qv20"] >= min_qv]
    print(f"거래대금 {min_qv/1e6:.0f}M 이상 필터 후 관측 {len(p):,}건, "
          f"{pd.to_datetime(p.timestamp.min(),unit='ms').date()} ~ "
          f"{pd.to_datetime(p.timestamp.max(),unit='ms').date()}")

    # 하루마다 상승률 상위 N
    p = p.sort_values(["timestamp", "chg"], ascending=[True, False])
    p["rk"] = p.groupby("timestamp")["chg"].rank(ascending=False, method="first")
    top = p[p["rk"] <= top_n].copy()
    rest = p[p["rk"] > top_n]

    # 다음날 시가 기준 수익률들
    for df, name in ((top, f"급등 상위{top_n}"), (rest, "나머지 전체")):
        oc = df["n_close"] / df["n_open"] - 1.0       # 시가→종가
        up = df["n_high"] / df["n_open"] - 1.0        # 시가→고가 (위로 얼마나)
        dn = df["n_low"] / df["n_open"] - 1.0         # 시가→저가 (아래로 얼마나)
        print(f"\n[{name}] n={len(df):,}  당일상승 중앙값 {df['chg'].median()*100:.1f}%")
        print(f"  다음날 시가→종가  평균 {oc.mean()*100:+.2f}%  중앙값 {oc.median()*100:+.2f}%  승률 {(oc>0).mean()*100:.1f}%")
        print(f"  다음날 최고 도달   중앙값 +{up.median()*100:.2f}%   +1% 이상 {(up>=0.01).mean()*100:.1f}%  +2% 이상 {(up>=0.02).mean()*100:.1f}%")
        print(f"  다음날 최저 도달   중앙값 {dn.median()*100:.2f}%   -1% 이하 {(dn<=-0.01).mean()*100:.1f}%  -3% 이하 {(dn<=-0.03).mean()*100:.1f}%")

    # 스캘핑 근사: 다음날 안에 TP 먼저인지 SL 먼저인지 (일봉은 순서 모름 → 양쪽 다 보고)
    print(f"\n=== 다음날 하루 안 TP/SL 도달 (급등 상위{top_n}) ===")
    up = top["n_high"] / top["n_open"] - 1.0
    dn = top["n_low"] / top["n_open"] - 1.0
    for tp in (0.01, 0.015, 0.02):
        for sl in (0.02, 0.03, 0.05):
            hit_tp, hit_sl = up >= tp, dn <= -sl
            only_tp = (hit_tp & ~hit_sl).mean()
            only_sl = (~hit_tp & hit_sl).mean()
            both = (hit_tp & hit_sl).mean()
            none = (~hit_tp & ~hit_sl).mean()
            # 낙관(둘다면 TP) / 비관(둘다면 SL)
            opt = only_tp + both
            pes = only_tp
            be = sl / (tp + sl)
            print(f"  TP+{tp*100:.1f}% SL-{sl*100:.0f}%  TP만 {only_tp*100:5.1f}%  SL만 {only_sl*100:5.1f}%  "
                  f"둘다 {both*100:5.1f}%  미도달 {none*100:5.1f}%  |  낙관승률 {opt*100:.1f}% / 비관승률 {pes*100:.1f}%  (손익분기 {be*100:.1f}%)")


if __name__ == "__main__":
    main()
