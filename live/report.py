#!/usr/bin/env python3
"""
성과 보고 — 세 전략을 한 장으로.

  poetry run python live/report.py                 # 전체
  poetry run python live/report.py --mode testnet
  poetry run python live/report.py --since 7       # 최근 7일만
  poetry run python live/report.py --save          # docs/report_YYYY-MM-DD.md 로 저장

매일 자동으로 받으려면 crontab 에:
  5 9 * * * cd <프로젝트> && .venv/bin/python live/report.py --save
"""
import os, sys, json, argparse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOGS = "logs"
# 손으로 낸 정리·주문경로 점검은 전략 성과가 아니다
PROBE_REASONS = {"MANUAL", "PROBE", "NO_STOP", "STOP_GONE"}
START = os.path.join(LOGS, "started.json")

SPECS = [
    # (표시이름, 상태파일, 시장, 레버리지)
    ("HIBERNATE", "hibernate_{mode}.json", "spot", 1),
    ("SURFER", "surfer_{mode}.json", "spot", 1),
    ("REBOUND", "rebound_state.json", "futures", 3),
]


def now():
    return datetime.now(timezone.utc)


def parse_ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v if v < 1e11 else v / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def started(key, fallback):
    """첫 가동일. 없으면 만들어 둔다 — 나중에 '며칠째'를 정확히 세려면 기록이 필요하다."""
    d = {}
    if os.path.exists(START):
        try:
            d = json.load(open(START, encoding="utf-8"))
        except Exception:
            d = {}
    if key not in d:
        d[key] = (fallback or now()).isoformat()
        os.makedirs(LOGS, exist_ok=True)
        json.dump(d, open(START, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return parse_ts(d[key])


def norm(h, lev):
    """전략마다 필드 이름이 다르다. 하나로 맞춘다."""
    sym = h.get("symbol") or h.get("sym")
    roi = h.get("roi")
    if roi is None:
        roi = h.get("ret")
    closed = parse_ts(h.get("closed") or h.get("at"))
    opened = parse_ts(h.get("opened"))
    days = (closed - opened).total_seconds() / 86400 if (closed and opened) else None
    return dict(sym=sym, roi=float(roi or 0), lev_roi=float(roi or 0) * lev,
                pnl=h.get("pnl"), reason=h.get("reason"), closed=closed, days=days)


def load(spec, mode):
    name, tpl, market, lev = spec
    path = os.path.join(LOGS, tpl.format(mode=mode))
    if not os.path.exists(path):
        return dict(name=name, market=market, lev=lev, path=path,
                    pos={}, hist=[], probes=0, updated=None, missing=True)
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        return dict(name=name, error=f"상태 파일을 못 읽는다: {e}")
    hist = [norm(h, lev) for h in d.get("history", [])]
    hist = [h for h in hist if h["sym"]]
    # 배관 점검으로 낸 주문은 성과가 아니다. 섞이면 보고서가 거짓말을 한다.
    # 사유는 "_GONE" 이 덧붙어 오기도 한다 (STOP_GONE_GONE). 앞자락으로 거른다.
    def _probe(h):
        r = h["reason"]
        return any(r == x or r.startswith(x + "_") for x in PROBE_REASONS)
    probes = [h for h in hist if _probe(h)]
    hist = [h for h in hist if not _probe(h)]
    return dict(name=name, market=market, lev=lev, path=path,
                pos=d.get("positions", {}), hist=hist, probes=len(probes),
                updated=parse_ts(d.get("updated")))


def live_prices(syms, market):
    """미실현 손익을 재려면 현재가가 있어야 한다. 실패해도 보고서는 나온다."""
    out = {}
    if not syms:
        return out
    try:
        import config
        ex = config.public_exchange("spot" if market == "spot" else "futures")
        t = ex.fetch_tickers(list(syms))
        for s, v in t.items():
            if v.get("last"):
                out[s] = float(v["last"])
    except Exception:
        pass
    return out


def block(st, since=None):
    L, A = [], None
    A = L.append
    if st.get("error"):
        A(f"### {st['name']}\n- {st['error']}")
        return L

    hist = st["hist"]
    first = min([h["closed"] for h in hist if h["closed"]] + 
                [parse_ts(p.get("opened")) for p in st["pos"].values() if p.get("opened")]
                or [None]) if (hist or st["pos"]) else None
    s0 = started(st["name"], first)
    days = (now() - s0).days if s0 else 0

    if since:
        cut = now() - timedelta(days=since)
        hist = [h for h in hist if h["closed"] and h["closed"] >= cut]

    A(f"### {st['name']}  ({st['market']}, {st['lev']}배)")
    A(f"- 가동 **{days}일째** (시작 {s0:%Y-%m-%d})"
      + (f" / 마지막 갱신 {st['updated']:%m-%d %H:%M} UTC" if st["updated"] else ""))

    if st.get("probes"):
        A(f"- (배관 점검 {st['probes']}건은 성과에서 제외했다)")
    if not hist and not st["pos"]:
        A("- 아직 거래 없음 — 신호 대기 중")
        return L

    if hist:
        r = [h["lev_roi"] for h in hist]
        win = sum(1 for x in r if x > 0)
        gp = sum(x for x in r if x > 0)
        gl = -sum(x for x in r if x < 0)
        pf = gp / gl if gl > 0 else float("inf")
        A(f"- 청산 {len(r)}건 | 승 {win} 패 {len(r)-win} (승률 {win/len(r)*100:.0f}%) "
          f"| 합산 {sum(r)*100:+.2f}% | PF {pf:.2f}")
        hold = [h["days"] for h in hist if h["days"] is not None]
        if hold:
            A(f"- 평균 보유 {sum(hold)/len(hold):.1f}일")
    else:
        A("- 청산 없음")

    if st["pos"]:
        px = live_prices(set(st["pos"]), st["market"])
        A(f"- 보유 {len(st['pos'])}종:")
        for sym, p in st["pos"].items():
            e = float(p.get("entry") or 0)
            c = px.get(sym)
            roi = ((c / e - 1) * st["lev"] * 100) if (c and e) else None
            op = parse_ts(p.get("opened"))
            held = f"{(now()-op).days}일" if op else "?"
            A(f"    - {sym}  진입 {e:.6g}"
              + (f"  현재 {c:.6g}  **{roi:+.1f}%**" if roi is not None else "  (현재가 조회 실패)")
              + f"  보유 {held}")
    return L


def by_symbol(states, since=None):
    rows = {}
    cut = now() - timedelta(days=since) if since else None
    for st in states:
        if not st or st.get("error"):
            continue
        for h in st["hist"]:
            if cut and (not h["closed"] or h["closed"] < cut):
                continue
            k = (st["name"], h["sym"])
            a = rows.setdefault(k, dict(n=0, sum=0.0, win=0))
            a["n"] += 1; a["sum"] += h["lev_roi"]; a["win"] += 1 if h["lev_roi"] > 0 else 0
    if not rows:
        return ["아직 청산된 거래가 없다."]
    L = ["| 전략 | 종목 | 거래 | 승/패 | 합산 수익 |", "|---|---|---:|---|---:|"]
    for (nm, sym), a in sorted(rows.items(), key=lambda x: -x[1]["sum"]):
        L.append(f"| {nm} | {sym} | {a['n']} | {a['win']}/{a['n']-a['win']} | {a['sum']*100:+.2f}% |")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="testnet", choices=["paper", "testnet", "live"])
    ap.add_argument("--since", type=int, default=None, help="최근 N일만")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    states = [load(s, a.mode) for s in SPECS]
    states = [s for s in states if s]

    out = [f"# 성과 보고 — {now():%Y-%m-%d %H:%M} UTC  (모드 {a.mode}"
           + (f", 최근 {a.since}일" if a.since else "") + ")", ""]

    tot_n = tot_r = 0.0
    for st in states:
        if st.get("error"):
            continue
        for h in st["hist"]:
            tot_n += 1; tot_r += h["lev_roi"]
    out += ["## 한 줄", ""]
    if tot_n:
        out.append(f"청산 {int(tot_n)}건, 거래당 합산 **{tot_r*100:+.2f}%**.")
    else:
        out.append("아직 청산된 거래가 없다. 신호를 기다리는 중이다.")
    out.append("")

    for st in states:
        out += block(st, a.since) + [""]

    out += ["## 종목별", ""] + by_symbol(states, a.since) + [""]
    out += ["---", "",
            "※ 수익률은 **레버리지 반영 후 자본 기준**이다 (REBOUND 는 3배).",
            "※ 보유중 종목은 미실현이라 확정 수치가 아니다."]

    text = "\n".join(out)
    print(text)
    if a.save:
        os.makedirs("docs", exist_ok=True)
        p = f"docs/report_{now():%Y-%m-%d}.md"
        open(p, "w", encoding="utf-8").write(text + "\n")
        print(f"\n저장: {p}")


if __name__ == "__main__":
    main()
