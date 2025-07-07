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
    
    # 순차적으로 setup 실행
    for s in strategies:
        await s.setup()
    logging.info("=== All strategies initialized ===")

    while True:
        # 순차적으로 각 전략 실행 (avbl 충돌 방지)
        for s in strategies:
            await s.run_once()  # test() -> run_once()로 변경
        await asyncio.sleep(INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
