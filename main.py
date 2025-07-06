from config import exchange, logging
import asyncio
from strategies.hour_3p_strategy import Hour3PStrategy

SYMBOLS = ["CHR/USDT:USDT",
    "CRV/USDT:USDT",
    # "ETH/USDT:USDT",
    # "LTC/USDT:USDT",
    "ATU/USDT:USDT",]
INTERVAL = 3600

async def main():
    await exchange.load_markets()
    strategies = [Hour3PStrategy(exchange, symbol, 3) for symbol in SYMBOLS]
    await asyncio.gather(*(s.setup() for s in strategies))
    logging.info("=== All strategies initialized ===")

    while True:
        for s in strategies:
            await s.test()
        await asyncio.sleep(INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
