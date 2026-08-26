#!/bin/bash
# 한 시간에 한 번 Claude 가 거래 상태를 감독한다.
#
#   crontab -e 에 이 줄:
#     7 * * * * /Users/gimhyeongjun/Desktop/DevProjects/binance-futures-bot/live/supervise.sh
#
# Claude 는 **브레이크만** 밟을 수 있다 (진입 금지 / 비중 축소).
# 새로 사라고 시키지 못한다 — 검증된 규칙이 진입을 정한다.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
mkdir -p logs
LOG="logs/supervise.log"
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

BRIEF=$(.venv/bin/python live/briefing.py 2>&1 || echo "브리핑 실패")
STATE=$(.venv/bin/python live/runner.py --mode "${MODE:-testnet}" --status 2>&1 || echo "상태 조회 실패")
GATE=$(cat logs/ai_gate.json 2>/dev/null || echo "{}")

PROMPT=$(cat <<EOF
너는 이 거래봇의 감독자다. 지금은 $STAMP.

## 시장 브리핑
$BRIEF

## 봇 상태
$STATE

## 현재 게이트
$GATE

## 네가 할 수 있는 것 — 브레이크뿐이다
검증된 규칙이 이미 진입/청산을 정했다. 너는 그걸 **더 조이는 것만** 할 수 있다.
새로 사라고 하거나, 손절을 늦추거나, 게이트를 푸는 것은 금지다.

조일 이유가 있을 때만 이 명령을 써라:
  .venv/bin/python live/ai_gate.py set --strategy <hibernate|surfer|rebound> \\
      [--block] [--size 0.5] --reason "<한 줄 사유>" --days <1~7>

조일 이유의 예 (자동 판정이 못 보는 것들만):
  - 거래소 사고, 출금 중단, 대규모 청산 연쇄
  - 스테이블코인 디페그
  - 보유 종목의 상장폐지 예고 / 토큰 사고
  - 봇 자체 이상 (주문 실패 반복, 포지션 불일치, 데이터 정체)

**대부분의 시간에는 아무것도 하지 않는 것이 정답이다.**
BTC 가 좀 빠졌다, 변동성이 크다 — 이건 이미 규칙에 들어있다. 손대지 마라.

이미 걸린 게이트가 있는데 사유가 사라졌으면 clear 해도 된다.

마지막에 한 줄로 결론만 적어라: "이상 없음" 또는 "<전략> <조치> — <사유>"
EOF
)

{
  echo "───────── $STAMP ─────────"
  echo "$PROMPT" | claude -p \
      --allowedTools "Bash(.venv/bin/python live/ai_gate.py:*),Read,Grep" \
      2>&1 || echo "claude 호출 실패"
  echo
} >> "$LOG"

tail -3 "$LOG"
