# ============================================================
# metrics.py — Logging, P&L tracking, trade journal
# ============================================================
import json
import logging
import os
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

class Metrics:
    def __init__(self):
        self.trade_log   = []
        self.scan_count  = 0
        self.opps_found  = 0
        self.trades_exec = 0
        self.started_at  = datetime.now(timezone.utc).isoformat()

    def record_scan(self, opportunities_found: int):
        self.scan_count += 1
        self.opps_found += opportunities_found

    def record_trade(self, market_id: str, question: str, category: str,
                     price: float, fair_value: float, edge: float,
                     size: float, confidence: float):
        self.trades_exec += 1
        entry = {
            "ts":          datetime.now(timezone.utc).isoformat(),
            "market_id":   market_id,
            "question":    question[:80],
            "category":    category,
            "price":       price,
            "fair_value":  fair_value,
            "edge":        round(edge, 4),
            "size":        size,
            "confidence":  confidence,
            "expected_pnl": round(edge * size, 2),
        }
        self.trade_log.append(entry)
        self._append_to_file(config.TRADE_LOG_FILE, entry)
        logger.info(
            f"📊 Trade #{self.trades_exec}: {question[:50]}... "
            f"edge={edge:.3f} size=${size:.2f} exp_pnl=${edge*size:.2f}"
        )

    def save_summary(self, position_summary: dict):
        summary = {
            "generated_at":    datetime.now(timezone.utc).isoformat(),
            "started_at":      self.started_at,
            "scans_completed": self.scan_count,
            "opps_found":      self.opps_found,
            "trades_executed": self.trades_exec,
            **position_summary,
        }
        with open(config.METRICS_LOG_FILE, "w") as f:
            json.dump(summary, f, indent=2)

    def _append_to_file(self, filepath: str, entry: dict):
        try:
            existing = []
            if os.path.exists(filepath):
                with open(filepath) as f:
                    existing = json.load(f)
            existing.append(entry)
            with open(filepath, "w") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not write to {filepath}: {e}")
