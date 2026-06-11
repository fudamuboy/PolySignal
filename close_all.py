import asyncio
import time
from app.data_fetcher import DataFetcher
from app.execution_engine import ExecutionEngine
from app.position_manager import PositionManager
from app.logger import logger
from app.config import PAPER_TRADING

async def close_all():
    logger.info("STOPPING BOT & CLOSING ALL POSITIONS...")
    
    data_fetcher = DataFetcher()
    position_manager = PositionManager()
    execution_engine = ExecutionEngine(paper_trading=PAPER_TRADING)
    
    positions = position_manager.get_all_positions()
    if not positions:
        logger.info("No open positions to close.")
        return

    logger.info(f"Foud {len(positions)} positions. Attempting to close...")
    
    for token_id, pos in list(positions.items()):
        logger.info(f"Closing {token_id} (Size: {pos['size']:.2f})...")
        
        # 1. Fetch current orderbook to get a real fill
        orderbook = await data_fetcher.get_orderbook(token_id)
        last_price = pos['avg_price'] # fallback
        
        market_data = {"token_id": token_id, "orderbook": orderbook} if orderbook else {}
        
        # If we have an orderbook, try to find the best bid
        if orderbook:
            bids = orderbook.get('bids', []) if isinstance(orderbook, dict) else getattr(orderbook, 'bids', [])
            if bids:
                last_price = float(bids[0].price if hasattr(bids[0], 'price') else bids[0].get('price', 0))
        
        # 2. Execute SELL
        result = await execution_engine.place_limit_order(
            token_id=token_id,
            price=last_price,
            size=pos['size'],
            side='SELL',
            market_data=market_data,
            is_aggressive=True # Ensure we cross the spread if needed
        )
        
        if result['status'] == "SUCCESS":
            position_manager.update_position(
                token_id=token_id,
                size=result.get('fill_size', pos['size']),
                price=result.get('fill_price', last_price),
                side='SELL',
                strategy='MANUAL_RESET'
            )
            logger.info(f"Successfully closed {token_id}")
        else:
            logger.warning(f"Could not close {token_id} normally: {result.get('error')}. Force clearing from DB.")
            # If we can't sell (no liquidity), just remove from DB to 'reset'
            position_manager.db.update_position(token_id, 0, 0)
            if token_id in position_manager.positions:
                del position_manager.positions[token_id]

    logger.info("Portfolio Reset Complete.")

if __name__ == "__main__":
    asyncio.run(close_all())
