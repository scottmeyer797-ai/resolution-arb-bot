class OpportunityEngine:

    def __init__(self):
        pass

    # ---------- SWING DETECTION ----------
    def detect_swings(self, candles, lookback=2):
        if len(candles) < (lookback * 2 + 1):
            return [], []

        swings_high = []
        swings_low = []

        for i in range(lookback, len(candles) - lookback):
            high = candles[i]["high"]
            low = candles[i]["low"]

            is_swing_high = all(high > candles[i - j]["high"] for j in range(1, lookback + 1)) and \
                            all(high > candles[i + j]["high"] for j in range(1, lookback + 1))

            is_swing_low = all(low < candles[i - j]["low"] for j in range(1, lookback + 1)) and \
                           all(low < candles[i + j]["low"] for j in range(1, lookback + 1))

            if is_swing_high:
                swings_high.append((i, high))
            if is_swing_low:
                swings_low.append((i, low))

        return swings_high, swings_low


    # ---------- TREND DETECTION ----------
    def get_trend(self, swings_high, swings_low):
        if len(swings_high) < 2 or len(swings_low) < 2:
            return None

        h1, h2 = swings_high[-2][1], swings_high[-1][1]
        l1, l2 = swings_low[-2][1], swings_low[-1][1]

        if h2 > h1 and l2 > l1:
            return "bullish"
        elif h2 < h1 and l2 < l1:
            return "bearish"

        return None


    # ---------- 50% RETRACEMENT ----------
    def valid_retracement(self, last_low, last_high, current_price, trend):
        if last_low is None or last_high is None:
            return False

        if trend == "bullish":
            fib_50 = last_low + (last_high - last_low) * 0.5
            return current_price <= fib_50

        if trend == "bearish":
            fib_50 = last_high - (last_high - last_low) * 0.5
            return current_price >= fib_50

        return False


    # ---------- BOS DETECTION ----------
    def detect_bos(self, swings_high, swings_low, trend, current_price):

        if trend == "bullish":
            if len(swings_high) == 0:
                return False
            last_high = swings_high[-1][1]
            return current_price > last_high

        if trend == "bearish":
            if len(swings_low) == 0:
                return False
            last_low = swings_low[-1][1]
            return current_price < last_low

        return False


    # ---------- MAIN ENTRY LOGIC ----------
    def find_opportunity(self, h4_candles, m15_candles, polymarket_price):

        if not h4_candles or not m15_candles:
            return None

        try:
            # H4 structure
            h4_highs, h4_lows = self.detect_swings(h4_candles)
            trend = self.get_trend(h4_highs, h4_lows)

            if trend is None or len(h4_highs) == 0 or len(h4_lows) == 0:
                return None

            last_high = h4_highs[-1][1]
            last_low = h4_lows[-1][1]

            current_price = m15_candles[-1]["close"]

            # 50% retracement
            if not self.valid_retracement(last_low, last_high, current_price, trend):
                return None

            # M15 structure
            m15_highs, m15_lows = self.detect_swings(m15_candles)

            bos = self.detect_bos(m15_highs, m15_lows, trend, current_price)

            if not bos:
                return None

            # Polymarket filter
            if trend == "bullish" and polymarket_price < 0.6:
                return {
                    "type": "BUY_YES",
                    "trend": trend,
                    "entry_price": polymarket_price
                }

            if trend == "bearish" and polymarket_price > 0.4:
                return {
                    "type": "BUY_NO",
                    "trend": trend,
                    "entry_price": polymarket_price
                }

            return None

        except Exception as e:
            print(f"OpportunityEngine Error: {e}")
            return None
