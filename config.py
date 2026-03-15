# ============================================================
# config.py - Resolution Arbitrage Bot Configuration
# ============================================================
import os

# --- Polymarket API ---
POLYMARKET_API_KEY      = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_WALLET       = os.getenv("POLYMARKET_WALLET", "")
POLYMARKET_PRIVATE_KEY  = os.getenv("POLYMARKET_PRIVATE_KEY", "")

# --- Trade Filters (core edge criteria) ---
MIN_CONFIDENCE          = 0.90   # loosened for paper trading (was 0.97)
MIN_EDGE                = 0.03   # loosened for paper trading (was 0.05)
MAX_TIME_REMAINING_HRS  = 48     # expanded window (was 24)
MIN_LIQUIDITY           = 20     # loosened for paper trading (was 50)

# --- Position Sizing ---
MAX_POSITION_PER_MARKET = 0      # PAPER MODE - set to 500 when going live
MAX_TOTAL_EXPOSURE      = 5000   # max USDC deployed at any time
ORDER_SPLIT_LEVELS      = 3      # split orders across price levels

# --- Scanning ---
SCAN_INTERVAL_SECONDS   = 30     # how often to scan for opportunities
MARKETS_PER_PAGE        = 100    # Polymarket API page size

# --- Asset Coverage ---
CRYPTO_ASSETS           = ["BTC", "ETH", "SOL"]

# --- Categories to scan ---
ACTIVE_CATEGORIES = [
    "crypto",
    "sports",
    "politics",
    "economics",
]

# --- Confidence Thresholds by Category ---
CATEGORY_CONFIDENCE = {
    "crypto":    0.90,   # loosened for paper trading (was 0.97)
    "sports":    0.88,   # loosened for paper trading (was 0.95)
    "politics":  0.85,   # loosened for paper trading (was 0.93)
    "economics": 0.88,   # loosened for paper trading (was 0.95)
}

# --- Risk Controls ---
MAX_DRAWDOWN_PCT         = 0.10  # stop trading if account drops 10%
MAX_TRADES_PER_HOUR      = 20    # circuit breaker

# --- Metrics ---
METRICS_LOG_FILE         = "metrics.json"
TRADE_LOG_FILE           = "trades.json"