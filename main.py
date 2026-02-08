from config import exchange, logging
import asyncio
from strategies.trailing_atr import TrailingAtrStrategy

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
