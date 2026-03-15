# ============================================================
# polymarket_client.py — Polymarket API wrapper
# ============================================================
import os
import time
import logging
import requests
from typing import Optional
from eth_account import Account
from eth_account.messages import encode_defunct

logger = logging.getLogger(__name__)

GAMMA_API   = "https://gamma-api.polymarket.com"
CLOB_API    = "https://clob.polymarket.com"

class PolymarketClient:
    def __init__(self):
        self.api_key       = os.getenv("POLYMARKET_API_KEY", "")
        self.private_key   = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        self.wallet        = os.getenv("POLYMARKET_WALLET", "")
        self.session       = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ----------------------------------------------------------
    # Market Data
    # ----------------------------------------------------------

    def get_markets(self, limit: int = 100, offset: int = 0,
                    active: bool = True, closed: bool = False) -> list:
        """Fetch markets from Gamma API with pagination."""
        try:
            params = {
                "limit":  limit,
                "offset": offset,
                "active": str(active).lower(),
                "closed": str(closed).lower(),
            }
            resp = self.session.get(f"{GAMMA_API}/markets", params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("markets", [])
        except Exception as e:
            logger.error(f"get_markets error: {e}")
            return []

    def get_all_markets(self) -> list:
        """Paginate through all active markets."""
        all_markets, offset, page_size = [], 0, 100
        while True:
            page = self.get_markets(limit=page_size, offset=offset)
            if not page:
                break
            all_markets.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
            time.sleep(0.2)   # gentle rate limiting
        logger.info(f"Loaded {len(all_markets)} markets total")
        return all_markets

    def get_orderbook(self, token_id: str) -> Optional[dict]:
        """Fetch current orderbook for a token."""
        try:
            resp = self.session.get(
                f"{CLOB_API}/book",
                params={"token_id": token_id},
                timeout=5
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"get_orderbook error for {token_id}: {e}")
            return None

    def get_best_ask(self, token_id: str) -> Optional[float]:
        """Return best ask price for YES token."""
        book = self.get_orderbook(token_id)
        if not book or not book.get("asks"):
            return None
        asks = sorted(book["asks"], key=lambda x: float(x["price"]))
        return float(asks[0]["price"]) if asks else None

    def get_liquidity(self, token_id: str, max_price: float) -> float:
        """Return total USDC liquidity available below max_price."""
        book = self.get_orderbook(token_id)
        if not book or not book.get("asks"):
            return 0.0
        total = 0.0
        for ask in book["asks"]:
            if float(ask["price"]) <= max_price:
                total += float(ask["size"]) * float(ask["price"])
        return total

    # ----------------------------------------------------------
    # Trade Execution
    # ----------------------------------------------------------

    def _sign_message(self, message: str) -> str:
        """Sign a message with the private key."""
        msg  = encode_defunct(text=message)
        signed = Account.sign_message(msg, private_key=self.private_key)
        return signed.signature.hex()

    def place_order(self, token_id: str, side: str,
                    price: float, size: float) -> Optional[dict]:
        """
        Place a limit order on the CLOB.
        side: 'BUY' or 'SELL'
        price: 0.0 – 1.0
        size: USDC amount
        """
        try:
            order = {
                "token_id": token_id,
                "side":     side,
                "price":    str(round(price, 4)),
                "size":     str(round(size, 2)),
                "type":     "LIMIT",
            }
            nonce     = str(int(time.time() * 1000))
            signature = self._sign_message(nonce)
            headers   = {
                "POLY-ADDRESS":   self.wallet,
                "POLY-SIGNATURE": signature,
                "POLY-TIMESTAMP": nonce,
                "POLY-API-KEY":   self.api_key,
            }
            resp = self.session.post(
                f"{CLOB_API}/order",
                json=order,
                headers=headers,
                timeout=10
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"Order placed: {side} {size} @ {price} → {result.get('orderID')}")
            return result
        except Exception as e:
            logger.error(f"place_order error: {e}")
            return None

    def get_positions(self) -> list:
        """Fetch open positions for the wallet."""
        try:
            resp = self.session.get(
                f"{CLOB_API}/positions",
                params={"user": self.wallet},
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"get_positions error: {e}")
            return []

    def get_balance(self) -> float:
        """Return USDC balance available to trade."""
        try:
            resp = self.session.get(
                f"{CLOB_API}/balance",
                params={"address": self.wallet},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("balance", 0))
        except Exception as e:
            logger.error(f"get_balance error: {e}")
            return 0.0
