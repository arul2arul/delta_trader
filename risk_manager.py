"""
Risk Manager – The bot is a "Risk Manager" first, "Trader" second.
Kill switch, PayDay exit, leg-breach detection, stop-loss enforcement.
"""

import logging

import config
from config import RiskAction

logger = logging.getLogger("risk_manager")


class RiskManager:
    """Evaluates risk and returns the appropriate action."""

    def __init__(self):
        self.daily_pnl = 0.0
        self.positions_premium = {}  # product_id → premium collected
        self._kill_triggered = False
        self._payday_triggered = False

    def reset_daily(self):
        """Reset counters for a new trading day."""
        self.daily_pnl = 0.0
        self.positions_premium.clear()
        self._kill_triggered = False
        self._payday_triggered = False
        logger.info("Risk manager reset for new trading day")

    def register_premium(self, product_id: int, premium: float):
        """Record premium collected for a short leg (for stop-loss calculations)."""
        self.positions_premium[product_id] = premium
        logger.info(
            f"Registered premium: product {product_id} = {premium:.4f}"
        )

    # ──────────────────────────────────────────
    # Individual Checks
    # ──────────────────────────────────────────
    def check_kill_switch(self, unrealized_pnl: float) -> bool:
        """
        Kill Switch: If total unrealized PnL hits -₹3,000,
        instantly market-close all positions.
        """
        if unrealized_pnl <= config.KILL_SWITCH_LOSS:
            logger.critical(
                f"🚨 KILL SWITCH TRIGGERED! "
                f"Unrealized PnL: ₹{unrealized_pnl:,.2f} "
                f"≤ ₹{config.KILL_SWITCH_LOSS:,}"
            )
            self._kill_triggered = True
            return True
        return False

    def check_payday(self, unrealized_pnl: float, max_profit: float = 0.0, hours_to_expiry: float = 24.0) -> bool:
        """
        Adaptive PayDay: If Unrealized Profit > 60% of Max Profit AND Time < 90 mins to expiry, Close All.
        """
        if max_profit > 0 and hours_to_expiry < 1.5:  # Changed to 90m for Gamma edge
            target = max_profit * 0.60  # Changed to 60% Early Harvest
            if unrealized_pnl >= target:
                logger.info(
                    f"💰 EARLY HARVEST! Profit: ₹{unrealized_pnl:,.2f} "
                    f"≥ 60% of Max Profit (₹{max_profit:,.2f}) with < 90m to expiry. Locking profits to avoid Gamma risk!"
                )
                self._payday_triggered = True
                return True

        return False

    def check_flash_crash(self, recent_candle: dict) -> bool:
        """
        Flash Crash Protection: 
        If price drops > 0.5% in a single 5-minute candle, close all Put Spreads!
        """
        if not recent_candle:
            return False
            
        open_px = float(recent_candle.get("open", 0))
        close_px = float(recent_candle.get("close", 0))
        
        if open_px > 0:
            drop = (open_px - close_px) / open_px
            if drop > 0.005:  # 0.5%
                logger.critical(f"📉 FLASH CRASH DETECTED: {drop*100:.2f}% drop in 5m candle. Closing Put Spreads!")
                return True
        return False

    def check_leg_breach(
        self,
        positions: list[dict],
        current_price: float,
    ) -> list[int]:
        """
        Check if any short leg has been breached
        (Price hits the Strike of a short option).

        Returns list of breached product_ids.
        """
        breached = []
        for pos in positions:
            size = int(pos.get("size", 0))
            if size >= 0:  # Only short positions (negative size) can be breached
                continue

            strike_price = float(pos.get("strike_price", 0))
            contract_type = str(pos.get("contract_type", "")).lower()

            if strike_price == 0:
                continue

            # Call breached if price >= strike
            if "call" in contract_type and current_price >= strike_price:
                product_id = pos.get("product_id")
                logger.warning(
                    f"⚠️ SHORT CALL BREACHED! "
                    f"Price {current_price:.2f} >= Strike {strike_price:.2f} "
                    f"(product {product_id})"
                )
                breached.append(product_id)

            # Put breached if price <= strike
            elif "put" in contract_type and current_price <= strike_price:
                product_id = pos.get("product_id")
                logger.warning(
                    f"⚠️ SHORT PUT BREACHED! "
                    f"Price {current_price:.2f} <= Strike {strike_price:.2f} "
                    f"(product {product_id})"
                )
                breached.append(product_id)

        return breached

    def check_stop_loss(
        self,
        product_id: int,
        current_option_price: float,
    ) -> bool:
        """
        Per-Leg Stop Loss: If option price > 2.5× the premium collected,
        close that leg.
        """
        premium = self.positions_premium.get(product_id, 0)
        if premium <= 0:
            return False

        stop_level = premium * config.STOPLOSS_MULTIPLIER

        if current_option_price >= stop_level:
            logger.warning(
                f"🛑 STOP LOSS on product {product_id}: "
                f"Option price {current_option_price:.4f} "
                f">= {config.STOPLOSS_MULTIPLIER}× premium "
                f"({stop_level:.4f})"
            )
            return True
        return False

    # ──────────────────────────────────────────
    # Master Evaluation
    # ──────────────────────────────────────────
    def evaluate(
        self,
        positions: list[dict],
        unrealized_pnl: float,
        realized_pnl: float,
        current_price: float,
        max_profit: float = 0.0,
        hours_to_expiry: float = 24.0,
        recent_5m_candle: dict = None,
    ) -> tuple[RiskAction, dict]:
        """
        Master risk evaluation. Checks all conditions in priority order:
        1. Kill Switch (highest priority)
        2. PayDay Exit
        3. Leg Breach
        4. Per-Leg Stop Loss
        5. Hold

        Returns:
            (RiskAction, details_dict)
        """
        total_pnl = unrealized_pnl + realized_pnl

        # 1. Kill Switch – HIGHEST PRIORITY
        if self.check_kill_switch(unrealized_pnl):
            return RiskAction.KILL, {
                "reason": "Kill switch triggered",
                "unrealized_pnl": unrealized_pnl,
            }

        # 1.5 Flash Crash Protection
        if recent_5m_candle and self.check_flash_crash(recent_5m_candle):
            # Flash crash affects Put spreads typically, but acting as a kill switch
            return RiskAction.KILL, {
                "reason": "Flash crash detected (price drop > 0.5% in 5m)",
                "unrealized_pnl": unrealized_pnl,
            }

        # 2. PayDay Exit (Adaptive)
        if self.check_payday(total_pnl, max_profit=max_profit, hours_to_expiry=hours_to_expiry):
            return RiskAction.PAYDAY, {
                "reason": "Early Harvest profit target reached",
                "total_pnl": total_pnl,
            }

        # 3. Leg Breach
        breached = self.check_leg_breach(positions, current_price)
        if breached:
            # Check for Patience Timer
            patience_active = False
            for pos in positions:
                size = int(pos.get("size", 0))
                strike = float(pos.get("strike_price", 0))
                if size < 0 and strike > 0 and abs(current_price - strike) > 600 and unrealized_pnl < 0:
                    patience_active = True
            
            if patience_active:
                logger.info("⏳ Patience Timer Active: Drawdown < 0 but price is >$600 from short strike. Waiting...")
                return RiskAction.HOLD, {"reason": "Patience timer active"}
            
            return RiskAction.KILL, {
                "reason": "Anti-Legging Logic: Leg breached, closing ALL baskets atomically.",
                "total_pnl": total_pnl,
            }

        # 4. Per-Leg Stop Loss -> Upgraded to Basket Stop Loss (Anti-Legging logic)
        for pos in positions:
            product_id = pos.get("product_id")
            option_price = float(pos.get("mark_price", 0))
            size = int(pos.get("size", 0))

            if size < 0 and self.check_stop_loss(product_id, option_price):
                return RiskAction.KILL, {
                    "reason": "Anti-Legging Logic: Per-leg stop loss hit, closing ALL baskets atomically.",
                    "total_pnl": total_pnl,
                }

        # 5. All clear
        return RiskAction.HOLD, {
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "total_pnl": total_pnl,
        }

    def get_current_profit_target(self) -> float:
        """
        Calculates the dynamic profit target based on the Gradual Growth plan.
        - Days 1-5: ₹500
        - Days 6-10: ₹1000 (if profitable)
        - Days 11+: ₹2000 (if profitable)
        """
        import os
        import pandas as pd
        
        target = config.STARTING_PROFIT_TARGET
        
        if not os.path.exists(config.TRADE_LOG_FILE):
            return target
            
        try:
            df = pd.read_csv(config.TRADE_LOG_FILE)
            if df.empty:
                return target
                
            # Count unique days where a trade was CLOSED (completed cycle)
            closed_trades = df[df['action'] == 'CLOSE']
            if closed_trades.empty:
                return target
                
            unique_days = pd.to_datetime(df['timestamp']).dt.date.nunique()
            total_realized = df[df['realized_pnl'].notnull()]['realized_pnl'].sum()
            
            if unique_days >= 11 and total_realized > 0:
                target = config.ULTIMATE_PROFIT_TARGET
            elif unique_days >= 6 and total_realized > 0:
                target = 1000
            
            logger.info(f"📈 Gradual Growth Check: Day {unique_days} | Total Realized: ₹{total_realized:,.2f} | Current Target: ₹{target}")
        except Exception as e:
            logger.warning(f"⚠️ Error checking trade history for Gradual Growth: {e}. Defaulting to ₹{target}")
            
        return target

    def calculate_safe_dynamic_lots(self, available_balance_usd: float, net_premium_per_btc: float, spot_price: float) -> int:
        """
        Implements the 'Safety-First' lot calculation across 3 constraints:
        1. Available Margin (Broker Constraint)
        2. Profit Target (User Goal)
        3. Hard Capital Risk (Safety Constraint)
        
        Returns the SMALLEST (safest) of the three.
        """
        # 1. Usable Margin calculation (with buffer and fee reserve)
        usable_margin_usd = available_balance_usd * (1 - config.MARGIN_BUFFER_PCT)
        fee_reserve_usd = config.RESERVE_FOR_FEES_INR / config.USD_INR_RATE
        usable_margin_usd = max(0, usable_margin_usd - fee_reserve_usd)
        
        # CONSTRAINT A: Margin Cap
        # Margin per lot ≈ Spot * 0.001 * Initial Margin Rate
        margin_per_lot = spot_price * 0.001 * config.EST_INITIAL_MARGIN_PCT
        max_lots_by_margin = int(usable_margin_usd / margin_per_lot) if margin_per_lot > 0 else 0
        
        # CONSTRAINT B: Profit Target Cap
        current_target_inr = self.get_current_profit_target()
        current_target_usd = current_target_inr / config.USD_INR_RATE
        # Lots = Target / (Premium per BTC * 0.001)
        lots_for_target = int(current_target_usd / (net_premium_per_btc * 0.001)) if net_premium_per_btc > 0 else 0
        
        # CONSTRAINT C: Hard Safety Cap
        hard_cap = 1000
        
        # The Selection: Picking the most conservative
        final_lots = min(max_lots_by_margin, lots_for_target, hard_cap)
        # Minimum floored to 1 lot if any calculation resulted in 0 but we have margin
        if final_lots <= 0 and max_lots_by_margin > 0:
            final_lots = 1
            
        # LOGGING FOR QE ANALYST
        logger.info(f"--- Dynamic Lot Calculation (Safety First) ---")
        logger.info(f"Available: ${available_balance_usd:.2f} | Usable: ${usable_margin_usd:.2f}")
        logger.info(f"Margin Cap: {max_lots_by_margin} | Target Cap: {lots_for_target} (Target: ₹{current_target_inr})")
        logger.info(f"Decision: Final Lots = {final_lots}")
        
        return final_lots

    @property
    def is_day_done(self) -> bool:
        """Check if the trading day is over (kill or payday triggered)."""
        return self._kill_triggered or self._payday_triggered
