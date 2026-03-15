# ============================================================
# opportunity_engine.py — Ranks and validates opportunities
# ============================================================
import logging
from typing import List
from market_scanner import MarketRecord
import config

logger = logging.getLogger(__name__)

class OpportunityEngine:

    def rank(self, markets: List[MarketRecord]) -> List[MarketRecord]:
        """
        Score and rank opportunities.
        Higher score = trade first.
        """
        scored = []
        for m in markets:
            score = self._score(m)
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        ranked = [m for _, m in scored]

        logger.info(f"Top opportunity: {ranked[0].question[:60]} | "
                    f"edge={ranked[0].edge:.3f} conf={ranked[0].confidence:.3f}"
                    if ranked else "No opportunities")
        return ranked

    def _score(self, m: MarketRecord) -> float:
        """
        Composite score weighting:
          - Edge (40%) — bigger mispricing = better
          - Confidence (40%) — higher certainty = better
          - Time urgency (20%) — closer to expiry = more valuable
        """
        from datetime import datetime, timezone
        hours_left = (m.expiry - datetime.now(timezone.utc)).total_seconds() / 3600

        # Normalise time urgency: 1.0 at <1hr, 0.0 at 24hr
        time_score  = max(0.0, 1.0 - (hours_left / 24.0))

        score = (
            0.40 * m.edge +
            0.40 * m.confidence +
            0.20 * time_score
        )
        return score

    def validate(self, m: MarketRecord, current_exposure: float) -> tuple[bool, str]:
        """
        Final pre-trade validation.
        Returns (is_valid, reason_if_rejected).
        """
        from datetime import datetime, timezone

        hours_left = (m.expiry - datetime.now(timezone.utc)).total_seconds() / 3600

        if hours_left <= 0:
            return False, "Market already expired"

        if m.edge < config.MIN_EDGE:
            return False, f"Edge too small: {m.edge:.3f}"

        if m.confidence < config.CATEGORY_CONFIDENCE.get(m.category, config.MIN_CONFIDENCE):
            return False, f"Confidence too low: {m.confidence:.3f}"

        if m.liquidity < config.MIN_LIQUIDITY:
            return False, f"Insufficient liquidity: {m.liquidity:.1f}"

        if current_exposure >= config.MAX_TOTAL_EXPOSURE:
            return False, f"Max exposure reached: {current_exposure:.1f}"

        return True, "ok"

    def calculate_position_size(self, m: MarketRecord,
                                  available_capital: float) -> float:
        """
        Kelly-inspired position sizing, capped by config limits.
        """
        # Fractional Kelly: f = (p*b - q) / b
        # where p = confidence, b = (1/price - 1), q = 1 - p
        b = (1.0 / m.current_price) - 1.0
        p = m.confidence
        q = 1.0 - p

        if b <= 0:
            return 0.0

        kelly_fraction = (p * b - q) / b
        half_kelly      = kelly_fraction * 0.5   # use half-Kelly for safety

        raw_size = available_capital * half_kelly
        capped   = min(raw_size, config.MAX_POSITION_PER_MARKET)
        capped   = min(capped, m.liquidity * 0.5)  # don't take >50% of liquidity
        capped   = max(capped, 0.0)

        return round(capped, 2)
