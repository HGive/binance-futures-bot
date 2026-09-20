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
{
  echo "===== $(date '+%F %T %Z') 시작 (mode=$MODE) ====="
  ./.venv/bin/python live/swing_bot.py --mode "$MODE" --once --paper-equity "$EQ" 2>&1
  echo "===== $(date '+%F %T %Z') 종료 code=$? ====="
} >> "$STATE/logs/cron.log"
