from config import binance_futures, logging
import asyncio
from strategies.spot_simple_strategy import SpotSimpleStrategy

BINANCE_SYMBOLS = ["CHR/USDT:USDT",
    "CRV/USDT:USDT",
    "ACT/USDT:USDT",
    "DEXE/USDT:USDT",
    "QTUM/USDT:USDT",
    "KAVA/USDT:USDT",
    "AR/USDT:USDT",]
INTERVAL = 300  # 5분마다 실행 (스팟은 천천히)

async def main():
    await binance_futures.load_markets()
    strategies = [SpotSimpleStrategy(binance_futures, symbol) for symbol in BINANCE_SYMBOLS]
    
    for s in strategies:
        await s.setup()
    logging.info("=== All strategies initialized ===")

    while True:
        for s in strategies:
            await s.run_once()
        await asyncio.sleep(INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
