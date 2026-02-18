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

    def check_payday(self, net_profit: float) -> bool:
        """
        PayDay Exit: Once net profit hits ₹1,200 (Gross),
        close the day and lock profits.
        """
        if net_profit >= config.PAYDAY_GROSS:
            logger.info(
                f"💰 PAY DAY! Net profit: ₹{net_profit:,.2f} "
                f"≥ ₹{config.PAYDAY_GROSS:,}. Locking profits!"
            )
            self._payday_triggered = True
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

        # 2. PayDay Exit
        if self.check_payday(total_pnl):
            return RiskAction.PAYDAY, {
                "reason": "Daily profit target reached",
                "total_pnl": total_pnl,
            }

        # 3. Leg Breach
        breached = self.check_leg_breach(positions, current_price)
        if breached:
            return RiskAction.ROLL_LEG, {
                "reason": "Short leg breached",
                "breached_products": breached,
                "current_price": current_price,
            }

        # 4. Per-Leg Stop Loss
        for pos in positions:
            product_id = pos.get("product_id")
            option_price = float(pos.get("mark_price", 0))
            size = int(pos.get("size", 0))

            if size < 0 and self.check_stop_loss(product_id, option_price):
                return RiskAction.STOP_LEG, {
                    "reason": "Per-leg stop loss triggered",
                    "product_id": product_id,
                    "option_price": option_price,
                }

        # 5. All clear
        return RiskAction.HOLD, {
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "total_pnl": total_pnl,
        }

    @property
    def is_day_done(self) -> bool:
        """Check if the trading day is over (kill or payday triggered)."""
        return self._kill_triggered or self._payday_triggered
