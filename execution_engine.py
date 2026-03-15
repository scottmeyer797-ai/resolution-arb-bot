# ============================================================
# execution_engine.py — Places and tracks orders
# ============================================================
import time
import logging
from datetime import datetime, timezone
from typing import List, Optional

from polymarket_client import PolymarketClient
from market_scanner import MarketRecord
import config

logger = logging.getLogger(__name__)

class ExecutionEngine:
    def __init__(self):
        self.client             = PolymarketClient()
        self.open_positions:    dict = {}   # market_id → position info
        self.trades_this_hour:  int  = 0
        self.hour_window_start: float = time.time()

    # ----------------------------------------------------------
    # Public interface
    # ----------------------------------------------------------

    def execute(self, market: MarketRecord, size: float) -> bool:
        """
        Execute a trade on a single market using order splitting.
        Returns True if at least one order was placed successfully.
        """
        if not self._circuit_breaker_ok():
            return False

        if market.market_id in self.open_positions:
            logger.info(f"Already in position: {market.market_id[:12]}")
            return False

        orders = self._build_split_orders(market, size)
        placed = 0

        for price, order_size in orders:
            result = self.client.place_order(
                token_id = market.yes_token_id,
                side     = "BUY",
                price    = price,
                size     = order_size,
            )
            if result:
                placed += 1
                self.trades_this_hour += 1

        if placed > 0:
            self.open_positions[market.market_id] = {
                "question":    market.question,
                "entry_price": market.current_price,
                "fair_value":  market.fair_value,
                "size":        size,
                "expiry":      market.expiry,
                "opened_at":   datetime.now(timezone.utc).isoformat(),
                "orders_placed": placed,
            }
            logger.info(
                f"✅ Traded: {market.question[:55]}... "
                f"| {placed}/{len(orders)} orders | size=${size:.2f} "
                f"| edge={market.edge:.3f}"
            )
            return True

        logger.warning(f"All orders failed for {market.market_id[:12]}")
        return False

    def get_exposure(self) -> float:
        """Total USDC currently deployed."""
        return sum(p["size"] for p in self.open_positions.values())

    def cleanup_expired(self):
        """Remove positions that have passed their expiry."""
        now    = datetime.now(timezone.utc)
        to_del = [
            mid for mid, pos in self.open_positions.items()
            if pos["expiry"] < now
        ]
        for mid in to_del:
            logger.info(f"Position resolved/expired: {mid[:12]}")
            del self.open_positions[mid]

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    def _build_split_orders(self, market: MarketRecord,
                             total_size: float) -> List[tuple]:
        """
        Split total_size across ORDER_SPLIT_LEVELS price levels.
        Example: buy at 0.82, 0.83, 0.84 for smoother fills.
        """
        levels = config.ORDER_SPLIT_LEVELS
        size_per_level = round(total_size / levels, 2)
        orders = []

        for i in range(levels):
            # spread bids across a 2% range above current price
            price = round(market.current_price + (i * 0.01), 4)
            price = min(price, market.fair_value - 0.01)   # never pay fair value
            if price >= 1.0:
                break
            orders.append((price, size_per_level))

        return orders

    def _circuit_breaker_ok(self) -> bool:
        """Reset hourly counter and check trade frequency limit."""
        now = time.time()
        if now - self.hour_window_start > 3600:
            self.trades_this_hour  = 0
            self.hour_window_start = now

        if self.trades_this_hour >= config.MAX_TRADES_PER_HOUR:
            logger.warning("Circuit breaker: max trades/hour reached")
            return False

        return True
