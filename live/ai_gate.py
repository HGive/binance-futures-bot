#!/usr/bin/env python3
"""
AI 개입 장치 — 브레이크만 있고 액셀은 없다.

왜 이렇게 만드는가:
  AI 의 장 판단은 백테스트로 검증할 수 없다. 검증 안 된 판단이 매매를 **늘리면**
  전략 성적이 무의미해진다. 그래서 구조적으로 줄이는 것만 가능하게 만든다.

    - size_mult 는 0.0 ~ 1.0 으로 강제 (1.0 초과 불가)
    - allow_new 는 False 로만 걸 수 있다 (코드가 금지한 것을 허용으로 못 뒤집는다)
    - 기한이 지나면 자동으로 무효 → 검증된 기본 동작으로 돌아간다
      (반대로 하면 껐다는 걸 잊고 영영 안 사게 된다)
    - 모든 결정은 사유와 함께 남는다. 나중에 "AI 개입이 도움이 됐나" 를 잴 수 있다.

쓰기:
  poetry run python live/ai_gate.py set --strategy hibernate --block \
      --reason "USDT 디페그 진행 중" --days 2
  poetry run python live/ai_gate.py set --strategy surfer --size 0.5 \
      --reason "미국 CPI 발표 대기" --days 1
  poetry run python live/ai_gate.py clear --strategy hibernate
  poetry run python live/ai_gate.py show
"""
import os, sys, json, argparse
from datetime import datetime, timezone, timedelta

LOGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
GATE = os.path.join(LOGS, "ai_gate.json")
AUDIT = os.path.join(LOGS, "ai_gate_audit.jsonl")
STRATEGIES = ("hibernate", "surfer", "scalp")
MAX_DAYS = 7


def _now():
    return datetime.now(timezone.utc)


def load():
    if not os.path.exists(GATE):
        return {}
    try:
        return json.load(open(GATE, encoding="utf-8"))
    except Exception:
        return {}


def read(strategy: str) -> dict:
    """봇이 부르는 함수. 항상 안전한 값을 돌려준다.

    반환: {"allow_new": bool, "size_mult": float, "reason": str, "active": bool}
    파일이 없거나, 깨졌거나, 기한이 지났으면 → 개입 없음(검증된 기본 동작).
    """
    safe = {"allow_new": True, "size_mult": 1.0, "reason": "", "active": False}
    g = load().get(strategy)
    if not isinstance(g, dict):
        return safe
    exp = g.get("expires")
    try:
        if exp and datetime.fromisoformat(exp) < _now():
            return safe                      # 기한 만료 → 기본으로 복귀
    except Exception:
        return safe
    allow = bool(g.get("allow_new", True))
    try:
        size = float(g.get("size_mult", 1.0))
    except Exception:
        size = 1.0
    size = max(0.0, min(1.0, size))          # 절대 1.0 을 못 넘는다
    return {"allow_new": allow, "size_mult": size,
            "reason": str(g.get("reason", ""))[:200],
            "active": (not allow) or size < 1.0}


def write(strategy, allow_new=True, size_mult=1.0, reason="", days=1, by="ai"):
    if strategy not in STRATEGIES:
        raise SystemExit(f"전략 이름이 틀렸다: {strategy} (가능: {', '.join(STRATEGIES)})")
    if not reason.strip():
        raise SystemExit("사유(--reason)를 반드시 적어라. 나중에 이 판단이 옳았는지 재야 한다.")
    days = max(0.05, min(MAX_DAYS, float(days)))
    size_mult = max(0.0, min(1.0, float(size_mult)))
    os.makedirs(LOGS, exist_ok=True)
    g = load()
    rec = {"allow_new": bool(allow_new), "size_mult": size_mult, "reason": reason.strip(),
           "set_at": _now().isoformat(), "expires": (_now() + timedelta(days=days)).isoformat(),
           "by": by}
    g[strategy] = rec
    tmp = GATE + ".tmp"
    json.dump(g, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, GATE)
    with open(AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"strategy": strategy, **rec}, ensure_ascii=False) + "\n")
    return rec


def clear(strategy):
    g = load()
    if strategy in g:
        del g[strategy]
        json.dump(g, open(GATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"strategy": strategy, "cleared_at": _now().isoformat()},
                               ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("--strategy", required=True, choices=STRATEGIES)
    s.add_argument("--block", action="store_true", help="신규 진입 금지")
    s.add_argument("--size", type=float, default=1.0, help="비중 배수 0.0~1.0")
    s.add_argument("--reason", required=True)
    s.add_argument("--days", type=float, default=1)
    s.add_argument("--by", default="ai")
    c = sub.add_parser("clear"); c.add_argument("--strategy", required=True, choices=STRATEGIES)
    sub.add_parser("show")
    a = ap.parse_args()

    if a.cmd == "set":
        r = write(a.strategy, allow_new=not a.block, size_mult=a.size,
                  reason=a.reason, days=a.days, by=a.by)
        print(f"[{a.strategy}] 신규진입 {'금지' if a.block else '허용'} / 비중 x{r['size_mult']:.2f} "
              f"/ {r['expires'][:16]} 까지\n  사유: {r['reason']}")
    elif a.cmd == "clear":
        clear(a.strategy); print(f"[{a.strategy}] 개입 해제 — 검증된 기본 동작으로 복귀")
    else:
        g = load()
        if not g:
            print("개입 없음 — 모든 전략이 검증된 기본 동작으로 돈다"); return
        for k in STRATEGIES:
            r = read(k)
            raw = g.get(k)
            if not raw:
                continue
            state = "적용 중" if r["active"] else ("기한 만료" if raw else "-")
            print(f"[{k}] {state}  신규진입 {'허용' if r['allow_new'] else '금지'} "
                  f"/ 비중 x{r['size_mult']:.2f}  ({raw.get('expires','')[:16]} 까지)")
            print(f"   사유: {raw.get('reason','')}")


if __name__ == "__main__":
    main()
