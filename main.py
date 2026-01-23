from config import exchange, logging, IS_TESTNET
import asyncio
from strategies.min15_3p_strategy import Min15Strategy3p

# === 심볼 설정 ===
if IS_TESTNET:
    # Testnet 지원 심볼
    SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    INTERVAL = 60  # 1분 (테스트용)
else:
    # Production 심볼
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


async def show_status(strategies):
    """현재 상태 출력 (테스트넷용)"""
    if not IS_TESTNET:
        return
    try:
        balance = await exchange.fetch_balance()
        total = balance["USDT"]["total"]
        free = balance["USDT"]["free"]
        logging.info(f"[BALANCE] Total={total:.2f} USDT, Free={free:.2f} USDT")
        
        for s in strategies:
            positions = await exchange.fetch_positions(symbols=[s.symbol])
            pos = positions[0] if positions and positions[0]["contracts"] > 0 else None
            if pos:
                side = pos["side"]
                entry = float(pos["entryPrice"])
                pnl = float(pos["unrealizedPnl"])
                pnl_pct = float(pos["percentage"])
                logging.info(f"[{s.symbol}] {side.upper()} @ {entry:.2f} | PnL: {pnl:.2f} ({pnl_pct:.2f}%) | AvgDown: {s.avg_down_count}")
            else:
                logging.info(f"[{s.symbol}] No position")
    except Exception as e:
        logging.error(f"Status check failed: {e}")


async def main():
    logging.info("=" * 50)
    logging.info(f"Min15 3% Strategy - {'TESTNET' if IS_TESTNET else 'PRODUCTION'}")
    logging.info(f"Symbols: {SYMBOLS}")
    logging.info(f"Interval: {INTERVAL}s")
    logging.info("=" * 50)

    await exchange.load_markets()
    strategies = [Min15Strategy3p(exchange, symbol) for symbol in SYMBOLS]
    
    for s in strategies:
        await s.setup()
    logging.info("=== All strategies initialized ===")

    iteration = 0
    while True:
        iteration += 1
        if IS_TESTNET:
            logging.info(f"\n--- Iteration #{iteration} ---")
            await show_status(strategies)
        
        for s in strategies:
            await s.run_once()
        
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("\nStopped by user")
