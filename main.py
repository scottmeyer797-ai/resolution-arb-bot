# ============================================================
# main.py — Resolution Arbitrage Bot
# Simulates trades for diagnostics — no real money placed
# ============================================================
import time
import logging
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify
from flask_cors import CORS

import config
from market_scanner      import MarketScanner
from opportunity_engine  import OpportunityEngine
from execution_engine    import ExecutionEngine
from position_manager    import PositionManager
from metrics             import Metrics
from resolution_tracker  import ResolutionTracker
from diagnostics         import Diagnostics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log"),
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

@app.route("/health")
def health():
    return jsonify({"status": "ok",
                    "ts": datetime.now(timezone.utc).isoformat()})

@app.route("/metrics")
def metrics_endpoint():
    import json, os
    if os.path.exists(config.METRICS_LOG_FILE):
        with open(config.METRICS_LOG_FILE) as f:
            return jsonify(json.load(f))
    return jsonify({"status": "no data yet"})

@app.route("/diagnostics")
def diagnostics_endpoint():
    import json, os
    if os.path.exists("diagnostics.json"):
        with open("diagnostics.json") as f:
            return jsonify(json.load(f))
    return jsonify({"status": "no diagnostics yet"})

@app.route("/resolved")
def resolved_endpoint():
    import json, os
    if os.path.exists("resolved_trades.json"):
        with open("resolved_trades.json") as f:
            data = json.load(f)
            return jsonify(data[-50:])
    return jsonify([])

def run_bot():
    scanner   = MarketScanner()
    engine    = OpportunityEngine()
    executor  = ExecutionEngine()
    metrics   = Metrics()
    tracker   = ResolutionTracker()
    diag      = Diagnostics(tracker)

    balance   = scanner.client.get_balance()
    positions = PositionManager(starting_balance=balance)
    logger.info(f"Bot started | Balance: ${balance:.2f} | "
                f"Scan interval: {config.SCAN_INTERVAL_SECONDS}s | "
                f"Paper mode: MAX_POSITION=${config.MAX_POSITION_PER_MARKET}")

    scan_count = 0

    while True:
        try:
            loop_start  = time.time()
            scan_count += 1

            current_balance = scanner.client.get_balance()
            positions.update_balance(current_balance)

            if positions.is_drawdown_breached():
                logger.critical("Drawdown breached — bot paused.")
                time.sleep(300)
                continue

            # Check resolutions every scan
            tracker.check_resolutions()

            # Run diagnostics every 10 scans
            if scan_count % 10 == 0:
                report = diag.run()
                if report.get("recommendations"):
                    logger.info(
                        f"Diagnostics: {report['flaws_detected']} flaws, "
                        f"{len(report['recommendations'])} recommendations"
                    )

            executor.cleanup_expired()

            opportunities = scanner.scan()
            metrics.record_scan(len(opportunities))

            if not opportunities:
                logger.info("No opportunities this scan.")
            else:
                ranked = engine.rank(opportunities)

                # Register TOP opportunities for simulation tracking
                # even if MAX_POSITION = 0 (no real trades placed)
                sim_count = 0
                for market in ranked[:50]:  # track top 50 per scan
                    if market.market_id not in tracker.pending:
                        size = engine.calculate_position_size(
                            market,
                            positions.get_available_capital(
                                executor.get_exposure()
                            )
                        )
                        sim_size = max(size, 10.0)  # simulate at least $10
                        tracker.register_trade(
                            market_id   = market.market_id,
                            question    = market.question,
                            category    = market.category,
                            entry_price = market.current_price,
                            fair_value  = market.fair_value,
                            edge        = market.edge,
                            confidence  = market.confidence,
                            size        = sim_size,
                            expiry      = market.expiry,
                        )
                        sim_count += 1

                if sim_count > 0:
                    logger.info(
                        f"Registered {sim_count} simulated trades "
                        f"({tracker.get_pending_count()} pending resolution)"
                    )

                # Execute real trades only if MAX_POSITION > 0
                if config.MAX_POSITION_PER_MARKET > 0:
                    for market in ranked:
                        exposure  = executor.get_exposure()
                        available = positions.get_available_capital(exposure)
                        valid, reason = engine.validate(market, exposure)
                        if not valid:
                            continue
                        size = engine.calculate_position_size(market, available)
                        if size < 10:
                            continue
                        success = executor.execute(market, size)
                        if success:
                            metrics.record_trade(
                                market_id  = market.market_id,
                                question   = market.question,
                                category   = market.category,
                                price      = market.current_price,
                                fair_value = market.fair_value,
                                edge       = market.edge,
                                size       = size,
                                confidence = market.confidence,
                            )

            resolution_summary = tracker.get_summary()
            metrics.save_summary({
                **positions.summary(),
                "resolution":         resolution_summary,
                "pending_simulation": tracker.get_pending_count(),
            })

            elapsed = time.time() - loop_start
            sleep   = max(0, config.SCAN_INTERVAL_SECONDS - elapsed)
            logger.info(f"Scan complete in {elapsed:.1f}s. Next scan in {sleep:.0f}s.")
            time.sleep(sleep)

        except KeyboardInterrupt:
            logger.info("Bot stopped.")
            break
        except Exception as e:
            logger.error(f"Bot loop error: {e}", exc_info=True)
            time.sleep(30)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    app.run(host="0.0.0.0", port=8080)
