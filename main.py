from config import binance_exchange, logging
import asyncio
from strategies.hour_3p_strategy import Hour3PStrategy

BINANCE_SYMBOLS = ["CHR/USDT:USDT",
    "CRV/USDT:USDT",
    "ACT/USDT:USDT",
    "DEXE/USDT:USDT",
    "QTUM/USDT:USDT",
    "KAVA/USDT:USDT",
    "AR/USDT:USDT",]
INTERVAL = 3600

async def main():
    await binance_exchange.load_markets()
    strategies = [Hour3PStrategy(binance_exchange, symbol, 3) for symbol in BINANCE_SYMBOLS]
    
    for s in strategies:
        await s.setup()
    logging.info("=== All strategies initialized ===")

    while True:
        for s in strategies:
            await s.run_once()
        await asyncio.sleep(INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
