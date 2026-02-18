"""
Monitor – Adaptive PnL monitoring with WebSocket primary, REST fallback.
Strategy-based polling intervals: Iron Condor 90s, Credit Spread 45s.
"""

import time
import logging
from datetime import datetime

import pytz

import config
from config import RiskAction, StrategyType

logger = logging.getLogger("monitor")


class Monitor:
    """
    Dual-mode position monitor.
    - WebSocket mode: instant risk evaluation on price push
    - REST fallback: polls at strategy-adaptive intervals
    """

    def __init__(
        self,
        exchange_client,
        market_data,
        ws_client,
        risk_manager,
        order_manager,
        notifier,
        trade_logger,
        scheduler,
    ):
        self.client = exchange_client
        self.market_data = market_data
        self.ws_client = ws_client
        self.risk_manager = risk_manager
        self.order_manager = order_manager
        self.notifier = notifier
        self.trade_logger = trade_logger
        self.scheduler = scheduler
        self.ist = pytz.timezone(config.TIMEZONE)

        self._running = False
        self._strategy_type: StrategyType = StrategyType.IRON_CONDOR
        self._last_check = ""
        self._ws_mode = False

    def start_monitoring_loop(
        self,
        strategy_type: StrategyType = StrategyType.IRON_CONDOR
    ):
        """
        Start the monitoring loop. Adapts polling interval to strategy type.

        WebSocket mode: registers callbacks for real-time evaluation.
        REST mode: polls at strategy-appropriate intervals.
        """
        self._strategy_type = strategy_type
        self._running = True

        # Try WebSocket first
        if config.USE_WEBSOCKET and self.ws_client and self.ws_client.is_connected:
            self._ws_mode = True
            logger.info("📡 Monitoring via WebSocket (real-time)")
            self.ws_client.on_price_update(self._on_price_update)
            self.ws_client.on_pnl_update(self._on_pnl_update)

            # Still run a slower REST check as a safety net
            self._run_rest_loop(interval=config.PNL_POLL_IRON_CONDOR * 2)
        else:
            self._ws_mode = False
            poll_interval = self.scheduler.get_poll_interval(strategy_type)
            logger.info(
                f"📊 Monitoring via REST polling "
                f"(interval: {poll_interval}s for {strategy_type.value})"
            )
            self._run_rest_loop(interval=poll_interval)

    def stop(self):
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Monitor stopped")

    def _run_rest_loop(self, interval: int):
        """REST polling loop with adaptive interval."""
        logger.info(f"REST poll loop started (interval: {interval}s)")

        while self._running:
            try:
                # Check if risk manager has ended the day
                if self.risk_manager.is_day_done:
                    logger.info("Day is done (kill/payday). Stopping monitor.")
                    break

                # Check weekend blackout
                if self.scheduler.is_weekend_blackout():
                    logger.info("Weekend blackout – pausing monitor")
                    break

                # Run the risk check
                self._check_and_act()

                # Update last check timestamp
                self._last_check = datetime.now(self.ist).strftime("%H:%M:%S IST")

            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                self.notifier.send_error_alert(f"Monitor error: {e}")

            time.sleep(interval)

    def _check_and_act(self):
        """Core risk check: fetch positions, evaluate, act."""
        try:
            # Fetch positions
            positions = self.client.get_positions()
            if not positions:
                logger.debug("No open positions")
                return

            # Filter active positions (non-zero size)
            active_positions = [
                p for p in positions
                if abs(int(p.get("size", 0))) > 0
            ]

            if not active_positions:
                logger.debug("No active positions")
                return

            # Calculate PnL
            unrealized_pnl = sum(
                float(p.get("unrealized_pnl", 0))
                for p in active_positions
            )
            realized_pnl = sum(
                float(p.get("realized_pnl", 0))
                for p in active_positions
            )

            # Get current price
            current_price = self.market_data.get_spot_price()

            # Evaluate risk
            action, details = self.risk_manager.evaluate(
                positions=active_positions,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=realized_pnl,
                current_price=current_price,
            )

            logger.info(
                f"Monitor check: PnL=₹{unrealized_pnl + realized_pnl:,.2f} "
                f"(unrealized=₹{unrealized_pnl:,.2f}), "
                f"positions={len(active_positions)}, "
                f"action={action.value}"
            )

            # Execute risk actions
            self._execute_action(action, details)

        except Exception as e:
            logger.error(f"Risk check failed: {e}")
            raise

    def _execute_action(self, action: RiskAction, details: dict):
        """Execute the risk action returned by the risk manager."""

        if action == RiskAction.HOLD:
            return  # All clear

        elif action == RiskAction.KILL:
            # 🚨 KILL SWITCH
            logger.critical("🚨 Executing KILL SWITCH")
            self.notifier.send_kill_switch_alert(details.get("unrealized_pnl", 0))
            self.order_manager.close_all_positions()
            self.trade_logger.log_event(
                action="KILL_SWITCH",
                pnl=details.get("unrealized_pnl", 0),
                notes="Kill switch activated",
            )
            self._running = False

        elif action == RiskAction.PAYDAY:
            # 💰 PAY DAY
            logger.info("💰 Executing PAY DAY exit")
            self.notifier.send_payday_alert(details.get("total_pnl", 0))
            self.order_manager.close_all_positions()
            self.trade_logger.log_event(
                action="PAYDAY",
                pnl=details.get("total_pnl", 0),
                notes="PayDay profit target reached",
            )
            self._running = False

        elif action == RiskAction.ROLL_LEG:
            # ⚠️ Leg breach – close the breached leg
            breached = details.get("breached_products", [])
            for product_id in breached:
                logger.warning(f"Rolling breached leg: product {product_id}")
                self.notifier.send_leg_breach_alert(
                    product_id=product_id,
                    price=details.get("current_price", 0),
                    strike=0,
                )
                try:
                    self.order_manager.close_position(product_id)
                    self.trade_logger.log_trade(
                        action="BREACH_CLOSE",
                        product_id=product_id,
                        notes="Leg breached – closed",
                    )
                except Exception as e:
                    logger.error(f"Failed to close breached leg {product_id}: {e}")

        elif action == RiskAction.STOP_LEG:
            # 🛑 Per-leg stop loss
            product_id = details.get("product_id")
            logger.warning(f"Stop loss on leg: product {product_id}")
            try:
                self.order_manager.close_position(product_id)
                self.trade_logger.log_trade(
                    action="STOP_LOSS",
                    product_id=product_id,
                    price=details.get("option_price", 0),
                    notes="Per-leg stop loss triggered",
                )
            except Exception as e:
                logger.error(f"Failed to close stopped leg {product_id}: {e}")

    # ──────────────────────────────────────────
    # WebSocket Callbacks
    # ──────────────────────────────────────────
    def _on_price_update(self, symbol: str, data: dict):
        """Handle real-time price update from WebSocket."""
        try:
            price = float(data.get("mark_price", data.get("last_price", 0)))
            if price > 0:
                # Run risk check on significant price movements
                self._check_and_act()
        except Exception as e:
            logger.error(f"WS price callback error: {e}")

    def _on_pnl_update(self, data: dict):
        """Handle real-time PnL update from WebSocket."""
        try:
            self._check_and_act()
        except Exception as e:
            logger.error(f"WS PnL callback error: {e}")

    def get_status(self) -> dict:
        """Get current monitor status for heartbeat."""
        try:
            positions = self.client.get_positions() or []
            active = [p for p in positions if abs(int(p.get("size", 0))) > 0]
            pnl = sum(
                float(p.get("unrealized_pnl", 0)) + float(p.get("realized_pnl", 0))
                for p in active
            )
            return {
                "pnl": pnl,
                "positions": len(active),
                "strategy": self._strategy_type.value,
                "last_check": self._last_check,
                "ws_connected": self._ws_mode and self.ws_client.is_connected,
            }
        except Exception:
            return {
                "pnl": 0,
                "positions": 0,
                "strategy": self._strategy_type.value,
                "last_check": self._last_check,
                "ws_connected": False,
            }
