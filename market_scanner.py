# ============================================================
# market_scanner.py — Fetches, filters and indexes markets
# ============================================================
import re
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import config
from polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)

@dataclass
class MarketRecord:
    market_id:      str
    question:       str
    category:       str
    yes_token_id:   str
    expiry:         datetime
    current_price:  float
    fair_value:     float
    edge:           float
    confidence:     float
    liquidity:      float
    crypto_asset:   Optional[str] = None
    threshold:      Optional[float] = None

class MarketScanner:
    def __init__(self):
        self.client  = PolymarketClient()
        self.markets = []

    def scan(self) -> list:
        raw = self.client.get_all_markets()
        logger.info(f"Raw markets fetched: {len(raw)}")

        candidates = []
        for m in raw:
            record = self._process_market(m)
            if record:
                candidates.append(record)

        logger.info(f"Opportunities after filtering: {len(candidates)}")
        self.markets = candidates
        return candidates

    def _process_market(self, m: dict) -> Optional[MarketRecord]:
        try:
            market_id = m.get("id") or m.get("conditionId", "")
            question  = m.get("question", "")

            if not market_id or not question:
                return None

            # --- Skip closed/inactive markets ---
            if m.get("closed") or not m.get("active", True):
                return None

            # --- Expiry using correct field name ---
            expiry = self._parse_expiry(m)
            if not expiry:
                return None

            now = datetime.now(timezone.utc)
            hours_remaining = (expiry - now).total_seconds() / 3600
            if hours_remaining <= 0 or hours_remaining > config.MAX_TIME_REMAINING_HRS:
                return None

            # --- Get YES token ID from clobTokenIds ---
            clob_ids = m.get("clobTokenIds")
            if not clob_ids:
                return None
            # clobTokenIds is a JSON string or list
            if isinstance(clob_ids, str):
                import json
                try:
                    clob_ids = json.loads(clob_ids)
                except Exception:
                    return None
            if not clob_ids or len(clob_ids) == 0:
                return None
            yes_token_id = clob_ids[0]

            # --- Current price from bestAsk or lastTradePrice ---
            current_price = None
            best_ask = m.get("bestAsk")
            last_trade = m.get("lastTradePrice")
            outcome_prices = m.get("outcomePrices")

            if best_ask and float(best_ask) > 0:
                current_price = float(best_ask)
            elif outcome_prices:
                if isinstance(outcome_prices, str):
                    import json
                    try:
                        outcome_prices = json.loads(outcome_prices)
                    except Exception:
                        pass
                if isinstance(outcome_prices, list) and len(outcome_prices) > 0:
                    current_price = float(outcome_prices[0])
            elif last_trade and float(last_trade) > 0:
                current_price = float(last_trade)

            if current_price is None or current_price <= 0 or current_price >= 1.0:
                return None

            # --- Liquidity from liquidityClob ---
            liquidity = float(m.get("liquidityClob") or m.get("liquidity") or 0)
            if liquidity < config.MIN_LIQUIDITY:
                return None

            # --- Category ---
            category = self._classify_category(m, question)
            if category not in config.ACTIVE_CATEGORIES:
                return None

            # --- Confidence & fair value ---
            crypto_asset, threshold = self._extract_crypto_info(question)
            confidence = self._estimate_confidence(
                question, category, hours_remaining,
                current_price=current_price,
                crypto_asset=crypto_asset,
                threshold=threshold
            )

            min_conf = config.CATEGORY_CONFIDENCE.get(category, config.MIN_CONFIDENCE)
            if confidence < min_conf:
                return None

            fair_value = confidence
            edge = fair_value - current_price

            if edge < config.MIN_EDGE:
                return None

            return MarketRecord(
                market_id    = market_id,
                question     = question,
                category     = category,
                yes_token_id = yes_token_id,
                expiry       = expiry,
                current_price= current_price,
                fair_value   = fair_value,
                edge         = edge,
                confidence   = confidence,
                liquidity    = liquidity,
                crypto_asset = crypto_asset,
                threshold    = threshold,
            )

        except Exception as e:
            logger.debug(f"Skipping market: {e}")
            return None

    def _parse_expiry(self, m: dict) -> Optional[datetime]:
        # Try correct field names in order of preference
        for key in ("endDateIso", "endDate", "end_date_iso", "expiration", "end_date"):
            val = m.get(key)
            if val:
                try:
                    dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except Exception:
                    continue
        return None

    def _classify_category(self, m: dict, question: str) -> str:
        q = question.lower()
        events = m.get("events") or []
        tags = ""
        if isinstance(events, list) and events:
            tags = str(events[0]).lower()

        if any(a.lower() in q for a in config.CRYPTO_ASSETS) or "crypto" in tags:
            return "crypto"
        if any(w in q for w in ["game", "match", "score", "win", "championship",
                                  "nfl", "nba", "epl", "league", "cup", "tournament",
                                  "player", "team", "goal", "points"]):
            return "sports"
        if any(w in q for w in ["election", "president", "vote", "senate",
                                  "congress", "prime minister", "poll", "candidate",
                                  "ballot", "party", "democrat", "republican"]):
            return "politics"
        if any(w in q for w in ["fed", "rate", "gdp", "inflation", "unemployment",
                                  "cpi", "recession", "economy", "market"]):
            return "economics"
        # Default to politics for general prediction markets
        return "politics"

    def _extract_crypto_info(self, question: str):
        asset, threshold = None, None
        q = question.upper()

        for a in config.CRYPTO_ASSETS:
            if a in q:
                asset = a
                break

        patterns = [
            r'\$?([\d,]+)[kK]\b',
            r'\$?([\d,]+(?:\.\d+)?)\b',
        ]
        for pat in patterns:
            match = re.search(pat, q)
            if match:
                num_str = match.group(1).replace(",", "")
                val = float(num_str)
                end = match.end()
                if end < len(q) and q[end-1:end+1].upper().endswith('K'):
                    val *= 1000
                elif 'K' in q[match.start():match.end()+1]:
                    val *= 1000
                if val > 100:  # ignore small numbers
                    threshold = val
                    break

        return asset, threshold

    def _estimate_confidence(self, question: str, category: str,
                              hours_remaining: float, current_price: float = 0.5,
                              crypto_asset=None, threshold=None) -> float:
        # If price is already very high, outcome is likely near-certain
        if current_price >= 0.95:
            return 0.97
        if current_price >= 0.90:
            return 0.93
        if current_price >= 0.85:
            return 0.91
        if current_price >= 0.80:
            return 0.89

        if category == "crypto" and crypto_asset and threshold:
            return self._crypto_confidence(crypto_asset, threshold, hours_remaining)

        # Time-based confidence for other categories
        if hours_remaining < 1:
            return 0.93
        if hours_remaining < 4:
            return 0.91
        if hours_remaining < 12:
            return 0.89
        if hours_remaining < 24:
            return 0.87
        return 0.85

    def _crypto_confidence(self, asset: str, threshold: float,
                            hours_remaining: float) -> float:
        try:
            import requests as req
            symbol_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
            cg_id = symbol_map.get(asset, asset.lower())
            r = req.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": cg_id, "vs_currencies": "usd"},
                timeout=5
            )
            price = r.json()[cg_id]["usd"]
            pct_above = (price - threshold) / threshold

            if pct_above >= 0.15:  return 0.99
            if pct_above >= 0.10:  return 0.98
            if pct_above >= 0.07:  return 0.97
            if pct_above >= 0.05:  return 0.96
            if pct_above >= 0.03:  return 0.94
            if pct_above >= 0.01:  return 0.90
            return 0.70
        except Exception as e:
            logger.debug(f"Crypto confidence fetch failed: {e}")
            return 0.85
