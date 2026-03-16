# ============================================================
# diagnostics.py — Performance analysis and filter tuning
# ============================================================
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class Diagnostics:
    """
    Analyses resolved trade data to identify filter improvements.
    Generates specific, actionable recommendations.
    """

    def __init__(self, resolution_tracker):
        self.tracker = resolution_tracker
        self.diag_file = "diagnostics.json"

    def run(self) -> dict:
        """Run full diagnostic analysis and save results."""
        summary = self.tracker.get_summary()

        if summary["total_resolved"] < 5:
            report = {
                "generated_at":   datetime.now(timezone.utc).isoformat(),
                "status":         "insufficient_data",
                "message":        f"Need at least 5 resolved trades. Have {summary['total_resolved']}.",
                "recommendations":[],
                "summary":        summary,
            }
            self._save(report)
            return report

        recommendations = []
        flaws            = []

        # --- Analyse by confidence band ---
        conf_breakdown = summary.get("by_confidence", {})
        for band, stats in conf_breakdown.items():
            wr    = stats["win_rate"]
            total = stats["total"]
            if total < 3:
                continue

            # Extract lower bound of band
            lower = float(band.split("-")[0])

            if wr < 70:
                flaws.append({
                    "type":    "confidence_miscalibration",
                    "band":    band,
                    "win_rate":wr,
                    "trades":  total,
                    "severity":"HIGH" if wr < 50 else "MEDIUM",
                })
                recommendations.append({
                    "priority": "HIGH" if wr < 50 else "MEDIUM",
                    "area":     "Confidence Filter",
                    "finding":  f"Confidence band {band} only winning {wr:.1f}% (expected ~{lower*100:.0f}%+)",
                    "action":   f"Raise MIN_CONFIDENCE to {min(lower + 0.05, 0.99):.2f} or remove this band",
                    "impact":   f"Estimated {total} fewer bad trades per cycle",
                })

        # --- Analyse by category ---
        cat_breakdown = summary.get("by_category", {})
        worst_cat     = None
        worst_wr      = 100.0
        best_cat      = None
        best_wr       = 0.0

        for cat, stats in cat_breakdown.items():
            wr    = stats["win_rate"]
            total = stats["total"]
            if total < 3:
                continue
            if wr < worst_wr:
                worst_wr  = wr
                worst_cat = cat
            if wr > best_wr:
                best_wr  = wr
                best_cat = cat

        if worst_cat and worst_wr < 70:
            flaws.append({
                "type":     "weak_category",
                "category": worst_cat,
                "win_rate": worst_wr,
                "severity": "HIGH" if worst_wr < 50 else "MEDIUM",
            })
            recommendations.append({
                "priority": "HIGH" if worst_wr < 50 else "MEDIUM",
                "area":     "Category Filter",
                "finding":  f"'{worst_cat}' markets only winning {worst_wr:.1f}%",
                "action":   f"Increase CATEGORY_CONFIDENCE['{worst_cat}'] by 0.03-0.05 or disable category",
                "impact":   "Removes lowest performing market segment",
            })

        if best_cat and best_wr > 90:
            recommendations.append({
                "priority": "LOW",
                "area":     "Category Opportunity",
                "finding":  f"'{best_cat}' markets winning {best_wr:.1f}% — strong edge",
                "action":   f"Consider increasing MAX_POSITION_PER_MARKET for '{best_cat}' category",
                "impact":   "Captures more value from strongest edge source",
            })

        # --- Analyse by time to expiry ---
        time_breakdown = summary.get("by_time_to_expiry", {})
        for band, stats in time_breakdown.items():
            wr    = stats["win_rate"]
            total = stats["total"]
            if total < 3:
                continue
            if wr < 70 and band in (">24h", "12-24h"):
                flaws.append({
                    "type":     "time_window_too_wide",
                    "band":     band,
                    "win_rate": wr,
                    "severity": "MEDIUM",
                })
                recommendations.append({
                    "priority": "MEDIUM",
                    "area":     "Time Window",
                    "finding":  f"Trades with {band} to expiry only winning {wr:.1f}%",
                    "action":   f"Reduce MAX_TIME_REMAINING_HRS from current value",
                    "impact":   "Focuses bot on higher-certainty near-expiry markets",
                })

        # --- Analyse by edge size ---
        edge_breakdown = summary.get("by_edge_size", {})
        for band, stats in edge_breakdown.items():
            wr    = stats["win_rate"]
            total = stats["total"]
            if total < 3:
                continue
            if wr < 65 and band == "0.03-0.05":
                flaws.append({
                    "type":     "edge_too_small",
                    "band":     band,
                    "win_rate": wr,
                    "severity": "MEDIUM",
                })
                recommendations.append({
                    "priority": "MEDIUM",
                    "area":     "Edge Filter",
                    "finding":  f"Small edge trades (0.03-0.05) only winning {wr:.1f}%",
                    "action":   "Raise MIN_EDGE from 0.03 to 0.05 or 0.07",
                    "impact":   "Removes marginal trades that aren't reliably profitable",
                })

        # --- Overall win rate check ---
        overall_wr = summary["win_rate"]
        if overall_wr < 80 and summary["total_resolved"] >= 10:
            recommendations.append({
                "priority": "HIGH",
                "area":     "Overall Filters",
                "finding":  f"Overall win rate {overall_wr:.1f}% is below 80% target",
                "action":   "Tighten MIN_CONFIDENCE by 0.02 and reduce MAX_TIME_REMAINING_HRS by 6",
                "impact":   "Should improve win rate toward 85%+",
            })
        elif overall_wr >= 90 and summary["total_resolved"] >= 10:
            recommendations.append({
                "priority": "LOW",
                "area":     "Opportunity",
                "finding":  f"Win rate {overall_wr:.1f}% exceeds target — filters may be too tight",
                "action":   "Consider loosening MIN_CONFIDENCE by 0.01 to increase trade frequency",
                "impact":   "More trades at still-profitable confidence levels",
            })

        # Sort by priority
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        recommendations.sort(key=lambda x: priority_order.get(x["priority"], 3))

        report = {
            "generated_at":    datetime.now(timezone.utc).isoformat(),
            "status":          "complete",
            "total_resolved":  summary["total_resolved"],
            "overall_win_rate":summary["win_rate"],
            "total_actual_pnl":summary["total_actual_pnl"],
            "flaws_detected":  len(flaws),
            "flaws":           flaws,
            "recommendations": recommendations,
            "breakdowns":      {
                "by_category":    summary.get("by_category", {}),
                "by_confidence":  summary.get("by_confidence", {}),
                "by_time":        summary.get("by_time_to_expiry", {}),
                "by_edge":        summary.get("by_edge_size", {}),
            },
            "summary":         summary,
        }

        self._save(report)

        if recommendations:
            logger.info(
                f"Diagnostics: {len(flaws)} flaws detected, "
                f"{len(recommendations)} recommendations generated"
            )
            for r in recommendations[:3]:
                logger.info(
                    f"[{r['priority']}] {r['area']}: {r['finding'][:60]}"
                )

        return report

    def _save(self, report: dict):
        try:
            with open(self.diag_file, "w") as f:
                json.dump(report, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save diagnostics: {e}")
