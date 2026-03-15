# ============================================================
# position_manager.py — Tracks exposure and drawdown
# ============================================================
import logging
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

class PositionManager:
    def __init__(self, starting_balance: float = 0.0):
        self.starting_balance   = starting_balance
        self.current_balance    = starting_balance
        self.peak_balance       = starting_balance
        self.total_pnl          = 0.0
        self.winning_trades     = 0
        self.losing_trades      = 0

    def update_balance(self, new_balance: float):
        self.current_balance = new_balance
        if new_balance > self.peak_balance:
            self.peak_balance = new_balance

    def record_trade_result(self, pnl: float):
        self.total_pnl += pnl
        if pnl >= 0:
            self.winning_trades += 1
        else:
            self.losing_trades  += 1

    def is_drawdown_breached(self) -> bool:
        """Stop trading if account drops MAX_DRAWDOWN_PCT from peak."""
        if self.peak_balance <= 0:
            return False
        drawdown = (self.peak_balance - self.current_balance) / self.peak_balance
        if drawdown >= config.MAX_DRAWDOWN_PCT:
            logger.critical(
                f"DRAWDOWN BREACHED: {drawdown:.1%} from peak ${self.peak_balance:.2f}. "
                f"Halting trading."
            )
            return True
        return False

    def get_available_capital(self, current_exposure: float) -> float:
        """Capital available for new trades."""
        available = self.current_balance - current_exposure
        return max(0.0, available)

    def summary(self) -> dict:
        total_trades = self.winning_trades + self.losing_trades
        win_rate     = (self.winning_trades / total_trades) if total_trades else 0.0
        monthly_roi  = (self.total_pnl / self.starting_balance) if self.starting_balance else 0.0

        return {
            "starting_balance": self.starting_balance,
            "current_balance":  self.current_balance,
            "total_pnl":        round(self.total_pnl, 2),
            "monthly_roi_pct":  round(monthly_roi * 100, 2),
            "win_rate_pct":     round(win_rate * 100, 2),
            "winning_trades":   self.winning_trades,
            "losing_trades":    self.losing_trades,
            "peak_balance":     round(self.peak_balance, 2),
        }
