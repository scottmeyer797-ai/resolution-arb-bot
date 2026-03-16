# ============================================================
# resolution_tracker.py — Simulated trade outcome tracker
# No real money — tracks what WOULD have happened
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
        self.pending:  dict  = {}   # market_id → simulated trade record
        self.resolved: list  = []   # completed simulated trade records
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
        """
        Register a SIMULATED trade for outcome tracking.
        No real money is placed — we track what would have happened.
        """
        if market_id in self.pending:
            return  # already tracking this market

        self.pending[market_id] = {
            "market_id":    market_id,
            "question":     question[:100],
            "category":     category,
            "entry_price":  entry_price,
            "fair_value":   fair_value,
            "edge":         round(edge, 4),
            "confidence":   confidence,
            "simulated_size": size,   # what we WOULD have traded
            "expiry":       expiry.isoformat(),
            "registered_at":datetime.now(timezone.utc).isoformat(),
            "resolved":     None,
            "actual_pnl":   None,
            "was_correct":  None,
            "simulated":    True,     # clearly marked as simulation
        }

    def check_resolutions(self):
        """
        Check all pending simulated trades.
        Looks up actual Polymarket resolution and calculates
        what our P&L would have been.
        """
        now      = datetime.now(timezone.utc)
        to_check = []

        for mid, trade in self.pending.items():
            expiry = datetime.fromisoformat(trade["expiry"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if (now - expiry).total_seconds() > 300:
                to_check.append(mid)

        if not to_check:
            return

        logger.info(f"Checking {len(to_check)} simulated trade outcomes...")

        for mid in to_check:
            outcome = self._fetch_resolution(mid)
            if outcome is not None:
                self._record_resolution(mid, outcome)
            time.sleep(0.3)

    def get_pending_count(self) -> int:
        return len(self.pending)

    def get_summary(self) -> dict:
        if not self.resolved:
            return {
                "total_resolved":    0,
                "correct":           0,
                "incorrect":         0,
                "win_rate":          0.0,
                "total_actual_pnl":  0.0,
                "avg_edge_accuracy": 0.0,
                "pending_trades":    len(self.pending),
                "by_category":       {},
                "by_confidence":     {},
                "by_time_to_expiry": {},
                "by_edge_size":      {},
            }

        correct   = [t for t in self.resolved if t.get("was_correct")]
        incorrect = [t for t in self.resolved if t.get("was_correct") is False]
        win_rate  = len(correct) / len(self.resolved) if self.resolved else 0.0
        total_pnl = sum(t.get("actual_pnl") or 0 for t in self.resolved)

        edge_errors = [
            abs(t["confidence"] - (1.0 if t["was_correct"] else 0.0))
            for t in self.resolved if t.get("confidence") is not None
        ]
        avg_accuracy = 1.0 - (sum(edge_errors)/len(edge_errors)) if edge_errors else 0.0

        return {
            "total_resolved":    len(self.resolved),
            "correct":           len(correct),
            "incorrect":         len(incorrect),
            "win_rate":          round(win_rate * 100, 2),
            "total_actual_pnl":  round(total_pnl, 2),
            "avg_edge_accuracy": round(avg_accuracy * 100, 2),
            "pending_trades":    len(self.pending),
            "by_category":       self._breakdown_by("category"),
            "by_confidence":     self._breakdown_by_confidence(),
            "by_time_to_expiry": self._breakdown_by_time(),
            "by_edge_size":      self._breakdown_by_edge(),
        }

    # ----------------------------------------------------------
    # Resolution fetching
    # ----------------------------------------------------------

    def _fetch_resolution(self, market_id: str) -> Optional[str]:
        """Fetch actual market resolution from Polymarket API."""
        try:
            resp = self.session.get(
                f"{GAMMA_API}/markets/{market_id}",
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            closed  = data.get("closed", False)
            outcome = data.get("outcome") or data.get("resolution")

            if not closed:
                return None

            if outcome:
                o = str(outcome).upper()
                if o in ("YES", "TRUE", "1", "WIN"):
                    return "YES"
                if o in ("NO", "FALSE", "0", "LOSS"):
                    return "NO"

            # Fallback — use outcomePrices
            prices = data.get("outcomePrices")
            if prices:
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except Exception:
                        pass
                if isinstance(prices, list) and prices:
                    return "YES" if float(prices[0]) >= 0.99 else "NO"

            return None

        except Exception as e:
            logger.debug(f"Resolution fetch error {market_id[:12]}: {e}")
            return None

    # ----------------------------------------------------------
    # Recording
    # ----------------------------------------------------------

    def _record_resolution(self, market_id: str, outcome: str):
        """Record simulated outcome and calculate hypothetical P&L."""
        trade = self.pending.pop(market_id)

        entry       = trade["entry_price"]
        size        = trade["simulated_size"]
        was_correct = outcome == "YES"

        # What our P&L WOULD have been
        if was_correct:
            actual_pnl = size * (1.0 - entry)
        else:
            actual_pnl = -size * entry

        expected_pnl = trade["edge"] * size
        conf_error   = abs(trade["confidence"] - (1.0 if was_correct else 0.0))

        reg_at = datetime.fromisoformat(trade["registered_at"])
        expiry = datetime.fromisoformat(trade["expiry"])
        if reg_at.tzinfo is None:
            reg_at = reg_at.replace(tzinfo=timezone.utc)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        hours_held = (expiry - reg_at).total_seconds() / 3600

        resolved_trade = {
            **trade,
            "resolved":        outcome,
            "was_correct":     was_correct,
            "actual_pnl":      round(actual_pnl, 2),
            "expected_pnl":    round(expected_pnl, 2),
            "pnl_vs_expected": round(actual_pnl - expected_pnl, 2),
            "conf_error":      round(conf_error, 4),
            "hours_held":      round(hours_held, 1),
            "resolved_at":     datetime.now(timezone.utc).isoformat(),
        }

        self.resolved.append(resolved_trade)
        self._save_resolved()

        logger.info(
            f"Simulated outcome: {market_id[:12]} → {outcome} | "
            f"Would have made ${actual_pnl:.2f} "
            f"(expected ${expected_pnl:.2f}) | correct={was_correct}"
        )

    # ----------------------------------------------------------
    # Breakdowns
    # ----------------------------------------------------------

    def _breakdown_by(self, field: str) -> dict:
        groups = {}
        for t in self.resolved:
            key = t.get(field, "unknown")
            if key not in groups:
                groups[key] = {"correct": 0, "total": 0, "pnl": 0.0}
            groups[key]["total"] += 1
            groups[key]["pnl"]   += t.get("actual_pnl") or 0
            if t.get("was_correct"):
                groups[key]["correct"] += 1
        return {
            k: {
                "win_rate": round(g["correct"]/g["total"]*100, 1) if g["total"] else 0,
                "total":    g["total"],
                "pnl":      round(g["pnl"], 2),
            }
            for k, g in groups.items()
        }

    def _breakdown_by_confidence(self) -> dict:
        bands = {"0.85-0.89":[],"0.90-0.94":[],"0.95-0.97":[],"0.98-1.00":[]}
        for t in self.resolved:
            c = t.get("confidence", 0)
            if   c >= 0.98: bands["0.98-1.00"].append(t)
            elif c >= 0.95: bands["0.95-0.97"].append(t)
            elif c >= 0.90: bands["0.90-0.94"].append(t)
            elif c >= 0.85: bands["0.85-0.89"].append(t)
        return {
            b: {"win_rate": round(sum(1 for t in ts if t.get("was_correct"))/len(ts)*100,1),
                "total": len(ts),
                "pnl": round(sum(t.get("actual_pnl") or 0 for t in ts),2)}
            for b, ts in bands.items() if ts
        }

    def _breakdown_by_time(self) -> dict:
        bands = {"<2h":[],"2-6h":[],"6-12h":[],"12-24h":[],"24-48h":[],">48h":[]}
        for t in self.resolved:
            h = t.get("hours_held", 0)
            if   h < 2:  bands["<2h"].append(t)
            elif h < 6:  bands["2-6h"].append(t)
            elif h < 12: bands["6-12h"].append(t)
            elif h < 24: bands["12-24h"].append(t)
            elif h < 48: bands["24-48h"].append(t)
            else:        bands[">48h"].append(t)
        return {
            b: {"win_rate": round(sum(1 for t in ts if t.get("was_correct"))/len(ts)*100,1),
                "total": len(ts),
                "pnl": round(sum(t.get("actual_pnl") or 0 for t in ts),2)}
            for b, ts in bands.items() if ts
        }

    def _breakdown_by_edge(self) -> dict:
        bands = {"0.03-0.05":[],"0.05-0.10":[],"0.10-0.20":[],">0.20":[]}
        for t in self.resolved:
            e = t.get("edge", 0)
            if   e > 0.20: bands[">0.20"].append(t)
            elif e > 0.10: bands["0.10-0.20"].append(t)
            elif e > 0.05: bands["0.05-0.10"].append(t)
            else:          bands["0.03-0.05"].append(t)
        return {
            b: {"win_rate": round(sum(1 for t in ts if t.get("was_correct"))/len(ts)*100,1),
                "total": len(ts),
                "pnl": round(sum(t.get("actual_pnl") or 0 for t in ts),2)}
            for b, ts in bands.items() if ts
        }

    # ----------------------------------------------------------
    # Persistence
    # ----------------------------------------------------------

    def _save_resolved(self):
        try:
            with open(self.resolved_file, "w") as f:
                json.dump(self.resolved, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save: {e}")

    def _load_existing(self):
        try:
            if os.path.exists(self.resolved_file):
                with open(self.resolved_file) as f:
                    self.resolved = json.load(f)
                logger.info(f"Loaded {len(self.resolved)} resolved simulated trades")
        except Exception as e:
            logger.warning(f"Could not load resolved trades: {e}")
