"""
PreFlightValidator – The final gatekeeper before execution.
Ensures Capital, Liquidity, AI confidence, and System Sync are all green.
"""
import logging
import config
from ai_validator import ask_ai_for_second_opinion

logger = logging.getLogger("preflight")

class PreFlightValidator:
    def __init__(self, exchange, market_data, risk_manager):
        self.exchange = exchange
        self.market_data = market_data
        self.risk_manager = risk_manager

    def run_all_checks(self, order_specs, trade_context):
        """
        Final verification before routing orders to Delta Exchange.
        Returns (is_ready: bool, message: str)
        """
        print("\n🛠️  Running Pre-Flight Checks...")

        # 1. Capital & Drawdown Protection
        # Check against the absolute TOTAL_CAPITAL_INR from config
        wallet_balance = self.exchange.get_wallet_balance() # Returns INR
        if wallet_balance < config.TOTAL_CAPITAL_INR * 0.90: # Allow 10% drawdown
            return False, f"CRITICAL: Capital too low (₹{wallet_balance:,.0f}). Minimum required: ₹{config.TOTAL_CAPITAL_INR:,.0f}"

        # 2. Delta Server Sync Check (Prevent signature/timestamp errors)
        # If the clock is too far off, the order WILL be rejected by Delta anyway.
        is_synced = self.exchange.check_time_sync(max_drift_sec=2.0)
        if not is_synced:
             return False, "Clock drift too high (>2s). Order would likely be rejected."

        # 3. Real-time Slippage Guard (L2 Orderbook Check)
        for leg in order_specs:
            symbol = leg.symbol if hasattr(leg, 'symbol') and leg.symbol else f"Product ID {leg.product_id}"
            # Fetch real-time L1/L2 data
            ticker = self.exchange.get_ticker(leg.product_id)
            if not ticker:
                return False, f"Could not fetch real-time ticker for {symbol}"
            
            bid = float(ticker.get("best_bid", 0))
            ask = float(ticker.get("best_ask", 0))
            mark = float(ticker.get("mark_price", 0))

            if mark > 0:
                spread_pct = (ask - bid) / mark
                # Dynamic slippage guard: No more than 5% spread at moment of entry
                if spread_pct > 0.05:
                    return False, f"Liquidity gap on {symbol}: Spread {spread_pct:.2%} exceeds 5% limit."
            else:
                return False, f"Invalide mark price (0) for {symbol}. Liquidity vanished?"

        # 4. AI Confidence Floor
        # Only run if not already validated or if we want a 'fresher' 1-second-before opinion
        if getattr(config, "USE_AI_VALIDATION", False):
            # The trade_context already contains the decision. 
            # We check the confidence score from our recent AI call.
            ai_result = trade_context.get("ai_result", {})
            confidence = ai_result.get("confidence_score", 0)
            if confidence < 6: # Threshold from config/main
                return False, f"AI Veto: Confidence {confidence}/10 is too low."

        print("✅ Pre-Flight: ALL SYSTEMS GO.")
        return True, "Ready for 💥 Execution"
