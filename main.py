"""
⚠️ 이 파일은 실행이 막혀 있다.

trailing_atr 는 2026-08-24 워크포워드 검증에서 폐기됐다.
  12구간 중 수익 2구간, 총수익 -100%, 파라미터 64조합 전부 마이너스,
  수수료를 0 으로 놓아도 -93.5% (포지션 승률 25%).
  자세한 내용: docs/backtest_trailing_atr.md

지금 검증을 통과한 전략은 HIBERNATE 하나이고, 실행 파일은 live/hibernate_bot.py 다.
"""
import sys
from config import exchange, logging
import asyncio
from strategies.trailing_atr import TrailingAtrStrategy

logging.error("trailing_atr 는 폐기된 전략이다 (docs/backtest_trailing_atr.md).")
logging.error("실행할 전략: poetry run python live/hibernate_bot.py --mode paper")
sys.exit(1)

# === 심볼 설정 ===
SYMBOLS = [
    "CHR/USDT:USDT",
    "CRV/USDT:USDT",
    "ACT/USDT:USDT",
    "DEXE/USDT:USDT",
    "QTUM/USDT:USDT",
    "KAVA/USDT:USDT",
    "AR/USDT:USDT",
]
INTERVAL = 60  # 1분 (15분봉 전략이라 자주 체크해도 됨)


async def main():
    logging.info("=" * 50)
    logging.info("Trailing ATR Strategy - PRODUCTION")
    logging.info(f"Symbols: {SYMBOLS}")
    logging.info(f"Interval: {INTERVAL}s")
    logging.info("=" * 50)

    await exchange.load_markets()
    strategies = [TrailingAtrStrategy(exchange, symbol) for symbol in SYMBOLS]

    for s in strategies:
        await s.setup()
    logging.info("=== All strategies initialized ===")

    while True:
        for s in strategies:
            await s.run_once()

        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("\nStopped by user")
