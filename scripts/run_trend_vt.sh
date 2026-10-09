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
# 선물 데모 계정을 SWING_DCA 와 같이 쓴다. 남의 종목은 가드에서, 남의 시드는
# 자본에서 뺀다. 실전은 전용 서브계정이므로 이 둘이 비어 있어야 한다.
RSV="${TREND_RESERVED:-}"
RSVE="${TREND_RESERVE_EQUITY:-0}"
EXTRA=()
[[ "${TREND_MAINTENANCE:-0}" == "1" ]] && EXTRA+=(--maintenance)
{
  echo "===== $(date '+%F %T %Z') 시작 (mode=$MODE, 제외=$RSV/$RSVE) ====="
  ./.venv/bin/python live/trend_bot.py --mode "$MODE" --once \
      --reserved "$RSV" --reserve-equity "$RSVE" "${EXTRA[@]}" 2>&1
  bot_rc=$?
  echo "===== $(date '+%F %T %Z') 종료 code=$bot_rc ====="
} >> "$STATE/logs/cron.log"

exit "$bot_rc"
