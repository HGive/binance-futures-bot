"""
전략 어댑터 — 엔진이 전략을 갈아끼울 수 있게 하는 얇은 층.

STRATEGY_RULES 3.7: 같은 규칙을 두 군데에 구현하지 않는다.
여기서는 판단을 하지 않고 strategies/ 의 함수를 부르기만 한다.
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies.hibernate as H
import strategies.surfer as SF

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ohlcv")
_MKT_CACHE = {}


def market_universe(min_bars=500):
    """시장 상태(알트 중앙값·폭)를 재는 유니버스.

    거래 대상(거래대금 상위 400종)과 **달라야 한다.**
    상위 400종만으로 중앙값을 내면 살아남은 종목만 보게 되어
    백테스트(캐시에 있는 전 종목)보다 낙관적으로 나온다.
    실제로 같은 날 +0.0% 와 +8.1% 로 갈렸다.
    """
    import glob
    key = min_bars
    if key in _MKT_CACHE:
        return _MKT_CACHE[key]
    out = {}
    for path in glob.glob(os.path.join(CACHE, "spot_*_1d.csv")):
        try:
            d = pd.read_csv(path)
        except Exception:
            continue
        if len(d) < min_bars:
            continue
        sym = os.path.basename(path)[len("spot_"):-len("_1d.csv")].replace("_", "/")
        out[sym] = d.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    _MKT_CACHE[key] = out
    return out


class Adapter:
    key = ""
    name = ""
    market = "spot"
    timeframe = "1d"
    min_bars = 430
    ticker_pct = 0.10
    max_concurrent = 8
    sell_fracs = [1.0]          # 포지션을 쪼개서 파는 비중 (최소주문 검사용)
    uses_oco = True             # 익절·손절을 거래소에 미리 걸 수 있는가

    def precompute(self, df): raise NotImplementedError
    def gate(self, daily): raise NotImplementedError
    def rank(self, pre, i): return 0.0
    def signal(self, df, pre, i): raise NotImplementedError
    def plan(self, df, pre, i, entry_px): raise NotImplementedError
    def on_day(self, p, df, pre, i, px): raise NotImplementedError
    def describe(self, p): return ""


# ────────────────────────── HIBERNATE ──────────────────────────
class Hibernate(Adapter):
    key, name = "hibernate", "HIBERNATE(동면)"
    min_bars = 430
    uses_oco = True

    def __init__(self):
        self.ticker_pct = H.TICKER_MARGIN_PCT
        self.max_concurrent = H.MAX_CONCURRENT
        self.sell_fracs = [H.SPLIT_AT_FIRST, 1 - H.SPLIT_AT_FIRST]

    def precompute(self, df):
        return H.precompute(df)

    def gate(self, daily):
        b = daily.get("BTC/USDT")
        if b is None or len(b) < H.REGIME_MA_DAYS + 1:
            return False, "BTC 일봉 부족"
        ma = float(b["close"].rolling(H.REGIME_MA_DAYS).mean().iloc[-1])
        px = float(b["close"].iloc[-1])
        return px > ma, f"BTC {px:,.0f} / {H.REGIME_MA_DAYS}일선 {ma:,.0f}"

    def rank(self, pre, i):
        return float(pre["drought"][i])

    def signal(self, df, pre, i):
        return H.entry_signal(pre, i, float(df["close"].iloc[i]))

    def plan(self, df, pre, i, entry):
        tg = H.target_gain(pre, i)
        return {"target_gain": tg,
                "tp1": entry * (1 + tg), "tp2": entry * (1 + H.DOUBLE_TP),
                "stop": entry * (1 + H.STOP_PCT),
                "split": H.SPLIT_AT_FIRST,
                "note": f"조용 {int(pre['drought'][i])}일 / 목표 +{tg*100:.0f}%"}

    def on_day(self, p, df, pre, i, px):
        """익절은 거래소 OCO 가 처리한다. 여기서는 손절만 본다(OCO 실패 대비)."""
        if px <= p["stop"]:
            return {"action": "sell", "frac": 1.0, "reason": "STOP"}
        return {"action": "hold"}

    def describe(self, p):
        return f"목표 +{p['target_gain']*100:.0f}%{' (러너)' if p.get('runner') else ''}"


# ────────────────────────── SURFER ──────────────────────────
class Surfer(Adapter):
    key, name = "surfer", "SURFER(파도타기)"
    min_bars = 200
    uses_oco = False            # 트레일링이 매일 움직여서 미리 못 건다

    def __init__(self):
        self.ticker_pct = SF.TICKER_PCT
        self.max_concurrent = SF.MAX_CONCURRENT
        self.sell_fracs = [0.5, 0.5]

    def precompute(self, df):
        pre = SF.precompute(df)
        pre["close"] = df["close"].astype(float).values
        pre["high"] = df["high"].astype(float).values
        return pre

    def gate(self, daily):
        # 시장 상태는 거래 유니버스가 아니라 캐시 전 종목으로 잰다 (백테스트와 동일)
        mkt = market_universe()
        btc = mkt.get("BTC/USDT")
        if btc is None:
            btc = daily.get("BTC/USDT")
        if btc is None:
            return False, "BTC 일봉 없음"
        now = int(pd.Timestamp.utcnow().timestamp() * 1000)

        def closed(d):
            return d.iloc[:-1] if int(d["timestamp"].iloc[-1]) + 86_400_000 > now else d

        tn = SF.trendiness(closed(btc)["close"].astype(float))
        rets = []
        for sym, d in mkt.items():
            d = closed(d)
            if len(d) < SF.ALT_MEDIAN_DAYS + 5:
                continue
            c = d["close"].astype(float)
            rets.append(float(c.iloc[-1] / c.iloc[-1 - SF.ALT_MEDIAN_DAYS] - 1))
        am = float(np.median(rets)) if rets else float("nan")
        ok = SF.market_gate(am, tn)
        return ok, (f"알트 {SF.ALT_MEDIAN_DAYS}일 중앙값 {am*100:+.1f}% (>{SF.ALT_MEDIAN_MIN*100:.0f}%, "
                    f"{len(rets)}종) / BTC 추세성 {tn:.2f} (>{SF.TREND_MIN})")

    def rank(self, pre, i):
        return -float(pre["stoch_k"][i])          # 더 과매도인 것부터

    def signal(self, df, pre, i):
        return SF.entry_signal(pre, i)

    def plan(self, df, pre, i, entry):
        a = float(pre["atr"][i])
        return {"atr_at_entry": a,
                "stop": SF.initial_stop(entry, a),
                "partial_tp": SF.partial_tp_price(entry),
                "split": 0.5, "best": entry, "partial": False,
                "note": f"StochRSI {pre['stoch_k'][i]:.0f} / 손절 {SF.initial_stop(entry,a)/entry-1:+.0%}"}

    def on_day(self, p, df, pre, i, px):
        """손절 → 부분익절 → 트레일링 순. 한 봉에 겹치면 손절 먼저 (백테스트와 동일)."""
        if px <= p["stop"]:
            return {"action": "sell", "frac": 1.0,
                    "reason": "TRAIL" if p.get("partial") else "STOP"}
        if not p.get("partial") and px >= p["partial_tp"]:
            return {"action": "sell", "frac": 0.5, "reason": "PARTIAL",
                    "set": {"partial": True, "stop": p["entry"], "best": px}}
        if p.get("partial"):
            best = max(float(p.get("best", px)), px)
            new_stop = max(p["entry"], SF.trail_stop(best, float(pre["atr"][i])))
            if new_stop > p["stop"] + 1e-12 or best > float(p.get("best", 0)):
                return {"action": "hold", "set": {"best": best, "stop": new_stop}}
        return {"action": "hold"}

    def describe(self, p):
        return (f"손절 {p['stop']/p['entry']-1:+.0%}"
                + (f" / 트레일링 (최고 {p.get('best', p['entry']):.6g})" if p.get("partial")
                   else f" / 부분익절 +{SF.PARTIAL_TP*100:.0f}%"))


# ────────────────────────── 스캘핑 ──────────────────────────
class ScalpPlaceholder(Adapter):
    """아직 검증된 진입 규칙이 없다. 매매하지 않고 실시간 데이터만 모은다.

    봉에서 뽑은 신호 14개가 전부 기각됐고(docs/backtest_scalp_screen.md),
    남은 정보원은 호가창·체결 흐름이라 과거 데이터를 살 수 없다.
    live/scalp_collector.py 로 지금부터 쌓는다.
    """
    key, name = "scalp", "스캘핑(데이터 수집만)"
    trades = False


ALL = {"hibernate": Hibernate, "surfer": Surfer, "scalp": ScalpPlaceholder}


def get(key):
    return ALL[key]()
