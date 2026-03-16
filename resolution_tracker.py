# ============================================================
# resolution_tracker.py — Tracks actual trade outcomes
# ============================================================
import json
import logging
import os
import requests
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"

class ResolutionTracker:
    def __init__(self):
        self.session         = requests.Session()
        self.pending:  dict  = {}   # market_id → trade record
        self.resolved: list  = []   # completed trade records
        self.resolved_file   = "resolved_trades.json"
        self._load_existing()

    # ----------------------------------------------------------
    # Public interface
    # ----------------------------------------------------------

    def register_trade(self, market_id: str, question: str,
                        category: str, entry_price: float,
                        fair_value: float, edge: float,
                        confidence: float, size: float,
                        expiry: datetime):
        """Register a paper trade for outcome tracking."""
        self.pending[market_id] = {
            "market_id":    market_id,
            "question":     question[:100],
            "category":     category,
            "entry_price":  entry_price,
            "fair_value":   fair_value,
            "edge":         round(edge, 4),
            "confidence":   confidence,
            "size":         size,
            "expiry":       expiry.isoformat(),
            "registered_at":datetime.now(timezone.utc).isoformat(),
            "resolved":     None,
            "actual_pnl":   None,
            "was_correct":  None,
        }
        logger.info(f"Registered trade for tracking: {market_id[:12]}")

    def check_resolutions(self):
        """
        Check all pending trades to see if they've resolved.
        Call this periodically from main loop.
        """
        now       = datetime.now(timezone.utc)
        to_check  = []

        for mid, trade in self.pending.items():
            expiry = datetime.fromisoformat(trade["expiry"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            # Check trades that expired more than 5 minutes ago
            if (now - expiry).total_seconds() > 300:
                to_check.append(mid)

        if not to_check:
            return

        logger.info(f"Checking resolutions for {len(to_check)} trades...")

        for mid in to_check:
            outcome = self._fetch_resolution(mid)
            if outcome is not None:
                self._record_resolution(mid, outcome)
            time.sleep(0.5)  # gentle rate limiting

    def get_summary(self) -> dict:
        """Return summary stats from resolved trades."""
        if not self.resolved:
            return {
                "total_resolved":   0,
                "correct":          0,
                "incorrect":        0,
                "win_rate":         0.0,
                "total_actual_pnl": 0.0,
                "avg_edge_accuracy":0.0,
                "by_category":      {},
                "by_confidence":    {},
                "by_time_to_expiry":{},
                "by_edge_size":     {},
            }

        correct   = [t for t in self.resolved if t.get("was_correct")]
        incorrect = [t for t in self.resolved if not t.get("was_correct") and t.get("was_correct") is not None]
        win_rate  = len(correct) / len(self.resolved) if self.resolved else 0.0
        total_pnl = sum(t.get("actual_pnl") or 0 for t in self.resolved)

        # Edge accuracy — how close was our confidence to reality
        edge_errors = [
            abs(t["confidence"] - (1.0 if t["was_correct"] else 0.0))
            for t in self.resolved if t.get("confidence") is not None
        ]
        avg_edge_accuracy = 1.0 - (sum(edge_errors) / len(edge_errors)) if edge_errors else 0.0

        return {
            "total_resolved":    len(self.resolved),
            "correct":           len(correct),
            "incorrect":         len(incorrect),
            "win_rate":          round(win_rate * 100, 2),
            "total_actual_pnl":  round(total_pnl, 2),
            "avg_edge_accuracy": round(avg_edge_accuracy * 100, 2),
            "by_category":       self._breakdown_by("category"),
            "by_confidence":     self._breakdown_by_confidence(),
            "by_time_to_expiry": self._breakdown_by_time(),
            "by_edge_size":      self._breakdown_by_edge(),
        }

    # ----------------------------------------------------------
    # Resolution fetching
    # ----------------------------------------------------------

    def _fetch_resolution(self, market_id: str) -> Optional[str]:
        """
        Fetch market resolution from Polymarket.
        Returns 'YES', 'NO', or None if not yet resolved.
        """
        try:
            resp = self.session.get(
                f"{GAMMA_API}/markets/{market_id}",
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            # Check resolution fields
            resolved_by = data.get("resolvedBy")
            outcome     = data.get("outcome") or data.get("resolution")
            closed      = data.get("closed", False)

            if not closed:
                return None  # not resolved yet

            # Parse outcome
            if outcome:
                o = str(outcome).upper()
                if o in ("YES", "TRUE", "1", "WIN"):
                    return "YES"
                if o in ("NO", "FALSE", "0", "LOSS"):
                    return "NO"

            # Try outcomePrices as fallback
            prices = data.get("outcomePrices")
            if prices:
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except Exception:
                        pass
                if isinstance(prices, list) and prices:
                    yes_price = float(prices[0])
                    return "YES" if yes_price >= 0.99 else "NO"

            return None

        except Exception as e:
            logger.debug(f"Resolution fetch error for {market_id[:12]}: {e}")
            return None

    # ----------------------------------------------------------
    # Recording
    # ----------------------------------------------------------

    def _record_resolution(self, market_id: str, outcome: str):
        """Record the resolution outcome and calculate actual P&L."""
        trade = self.pending.pop(market_id)

        entry       = trade["entry_price"]
        size        = trade["size"]
        was_correct = outcome == "YES"

        # Actual P&L calculation
        if was_correct:
            # Bought YES at entry_price, resolved at 1.0
            actual_pnl = size * (1.0 - entry)
        else:
            # Bought YES at entry_price, resolved at 0.0
            actual_pnl = -size * entry

        # Expected P&L was edge * size
        expected_pnl = trade["edge"] * size

        # Confidence error
        conf_error = abs(trade["confidence"] - (1.0 if was_correct else 0.0))

        # Time held
        reg_at  = datetime.fromisoformat(trade["registered_at"])
        expiry  = datetime.fromisoformat(trade["expiry"])
        if reg_at.tzinfo is None:
            reg_at = reg_at.replace(tzinfo=timezone.utc)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        hours_held = (expiry - reg_at).total_seconds() / 3600

        resolved_trade = {
            **trade,
            "resolved":       outcome,
            "was_correct":    was_correct,
            "actual_pnl":     round(actual_pnl, 2),
            "expected_pnl":   round(expected_pnl, 2),
            "pnl_vs_expected":round(actual_pnl - expected_pnl, 2),
            "conf_error":     round(conf_error, 4),
            "hours_held":     round(hours_held, 1),
            "resolved_at":    datetime.now(timezone.utc).isoformat(),
        }

        self.resolved.append(resolved_trade)
        self._save_resolved()

        logger.info(
            f"Resolved: {market_id[:12]} → {outcome} | "
            f"P&L=${actual_pnl:.2f} (expected ${expected_pnl:.2f}) | "
            f"correct={was_correct}"
        )

    # ----------------------------------------------------------
    # Breakdown analysis
    # ----------------------------------------------------------

    def _breakdown_by(self, field: str) -> dict:
        """Break down win rate and P&L by a categorical field."""
        groups = {}
        for t in self.resolved:
            key = t.get(field, "unknown")
            if key not in groups:
                groups[key] = {"correct": 0, "total": 0, "pnl": 0.0}
            groups[key]["total"]   += 1
            groups[key]["pnl"]     += t.get("actual_pnl") or 0
            if t.get("was_correct"):
                groups[key]["correct"] += 1

        result = {}
        for key, g in groups.items():
            result[key] = {
                "win_rate": round(g["correct"] / g["total"] * 100, 1) if g["total"] else 0,
                "total":    g["total"],
                "pnl":      round(g["pnl"], 2),
            }
        return result

    def _breakdown_by_confidence(self) -> dict:
        """Break down performance by confidence band."""
        bands = {
            "0.85-0.89": [],
            "0.90-0.94": [],
            "0.95-0.97": [],
            "0.98-1.00": [],
        }
        for t in self.resolved:
            c = t.get("confidence", 0)
            if   c >= 0.98: bands["0.98-1.00"].append(t)
            elif c >= 0.95: bands["0.95-0.97"].append(t)
            elif c >= 0.90: bands["0.90-0.94"].append(t)
            elif c >= 0.85: bands["0.85-0.89"].append(t)

        result = {}
        for band, trades in bands.items():
            if not trades:
                continue
            correct = sum(1 for t in trades if t.get("was_correct"))
            result[band] = {
                "win_rate": round(correct / len(trades) * 100, 1),
                "total":    len(trades),
                "pnl":      round(sum(t.get("actual_pnl") or 0 for t in trades), 2),
            }
        return result

    def _breakdown_by_time(self) -> dict:
        """Break down performance by hours held."""
        bands = {
            "<2h":   [],
            "2-6h":  [],
            "6-12h": [],
            "12-24h":[],
            ">24h":  [],
        }
        for t in self.resolved:
            h = t.get("hours_held", 0)
            if   h < 2:  bands["<2h"].append(t)
            elif h < 6:  bands["2-6h"].append(t)
            elif h < 12: bands["6-12h"].append(t)
            elif h < 24: bands["12-24h"].append(t)
            else:        bands[">24h"].append(t)

        result = {}
        for band, trades in bands.items():
            if not trades:
                continue
            correct = sum(1 for t in trades if t.get("was_correct"))
            result[band] = {
                "win_rate": round(correct / len(trades) * 100, 1),
                "total":    len(trades),
                "pnl":      round(sum(t.get("actual_pnl") or 0 for t in trades), 2),
            }
        return result

    def _breakdown_by_edge(self) -> dict:
        """Break down performance by edge size."""
        bands = {
            "0.03-0.05": [],
            "0.05-0.10": [],
            "0.10-0.20": [],
            ">0.20":     [],
        }
        for t in self.resolved:
            e = t.get("edge", 0)
            if   e > 0.20: bands[">0.20"].append(t)
            elif e > 0.10: bands["0.10-0.20"].append(t)
            elif e > 0.05: bands["0.05-0.10"].append(t)
            else:          bands["0.03-0.05"].append(t)

        result = {}
        for band, trades in bands.items():
            if not trades:
                continue
            correct = sum(1 for t in trades if t.get("was_correct"))
            result[band] = {
                "win_rate": round(correct / len(trades) * 100, 1),
                "total":    len(trades),
                "pnl":      round(sum(t.get("actual_pnl") or 0 for t in trades), 2),
            }
        return result

    # ----------------------------------------------------------
    # Persistence
    # ----------------------------------------------------------

    def _save_resolved(self):
        try:
            with open(self.resolved_file, "w") as f:
                json.dump(self.resolved, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save resolved trades: {e}")

    def _load_existing(self):
        try:
            if os.path.exists(self.resolved_file):
                with open(self.resolved_file) as f:
                    self.resolved = json.load(f)
                logger.info(f"Loaded {len(self.resolved)} resolved trades from disk")
        except Exception as e:
            logger.warning(f"Could not load resolved trades: {e}")
