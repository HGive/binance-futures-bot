#!/bin/bash
# SWING_DCA launchd 진입점.
# 프로젝트가 ~/Desktop 아래라 macOS TCC 가 launchd 의 쓰기를 막는다(읽기는 된다).
# 로그와 상태만 보호되지 않는 이 폴더로 보낸다.
STATE="$HOME/Library/Application Support/swingdca"
P=/Users/gimhyeongjun/Desktop/DevProjects/binance-futures-bot
export SWING_LOG_DIR="$STATE/logs"
mkdir -p "$SWING_LOG_DIR"
cd "$P" || { echo "$(date '+%F %T') cd 실패" >> "$STATE/logs/cron.log"; exit 1; }
MODE="${SWING_MODE:-paper}"
EQ="${SWING_PAPER_EQUITY:-1000}"
# 선물 데모 계정을 TREND_VT 와 같이 쓴다.
#   CAPITAL  자기 시드를 금액으로 못 박는다. 이게 없으면 계좌 전체(TREND_VT 몫 포함)를
#            자기 자본으로 세서 그만큼 더 산다.
#   RESERVED TREND_VT 가 쓰는 종목. 후보에서 빼고 그 포지션은 건드리지 않는다.
CAP="${SWING_CAPITAL:-0}"
RSV="${SWING_RESERVED:-}"
{
  echo "===== $(date '+%F %T %Z') 시작 (mode=$MODE, 시드=$CAP, 제외=$RSV) ====="
  ./.venv/bin/python live/swing_bot.py --mode "$MODE" --once \
      --paper-equity "$EQ" --capital "$CAP" --reserved "$RSV" 2>&1
  echo "===== $(date '+%F %T %Z') 종료 code=$? ====="
} >> "$STATE/logs/cron.log"
