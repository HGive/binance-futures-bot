#!/bin/bash
# TREND_VT launchd 진입점.
# 프로젝트가 ~/Desktop 아래라 macOS TCC 로 launchd 가 그 안에 쓰지 못한다.
# 코드·.env 읽기는 되므로, 로그와 상태만 보호되지 않는 이 폴더로 보낸다.
STATE="$HOME/Library/Application Support/trendvt"
P=/Users/gimhyeongjun/Desktop/DevProjects/binance-futures-bot
export TREND_LOG_DIR="$STATE/logs"
mkdir -p "$TREND_LOG_DIR"
cd "$P" || { echo "$(date '+%F %T') cd 실패" >> "$STATE/logs/cron.log"; exit 1; }
MODE="${TREND_MODE:-testnet}"
{
  echo "===== $(date '+%F %T %Z') 시작 (mode=$MODE) ====="
  ./.venv/bin/python live/trend_bot.py --mode "$MODE" --once 2>&1
  echo "===== $(date '+%F %T %Z') 종료 code=$? ====="
} >> "$STATE/logs/cron.log"
