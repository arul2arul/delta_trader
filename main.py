"""
Operation Daily Profit – Main Orchestrator
==========================================

The top-level "brain" that ties everything together:
1. Initialize → Check connectivity → Sync time
2. Connect WebSocket + Start Telegram heartbeat
3. Main loop: deploy strategy at 10 AM IST, monitor, manage risk
4. Respect weekend blackout (Fri 5 PM – Mon 9 AM IST)
"""

import sys
import time
import logging
import signal
from datetime import datetime

import pytz
from dotenv import load_dotenv

import config
from config import Regime, StrategyType
from exchange_client import ExchangeClient
from market_data import MarketData
from ws_client import WebSocketClient
from indicators import compute_all
from regime_detector import detect_regime, check_volatility, get_strategy_for_regime
from strategy_engine import build_strategy
from order_manager import OrderManager
from risk_manager import RiskManager
from scheduler import Scheduler
from monitor import Monitor
from notifier import Notifier
from trade_logger import TradeLogger

# ──────────────────────────────────────────
# Logging Setup
# ──────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("delta_trader.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


class DeltaTrader:
    """Main orchestrator for the autonomous trading system."""

    def __init__(self):
        load_dotenv()

        logger.info("=" * 60)
        logger.info("  OPERATION DAILY PROFIT – Starting Up")
        logger.info("=" * 60)

        # Initialize components
        self.exchange = ExchangeClient()
        self.market_data = MarketData(self.exchange)
        self.ws_client = WebSocketClient(self.exchange)
        self.trade_logger = TradeLogger()
        self.order_manager = OrderManager(self.exchange, self.trade_logger)
        self.risk_manager = RiskManager()
        self.scheduler = Scheduler()
        self.notifier = Notifier()
        self.monitor = Monitor(
            exchange_client=self.exchange,
            market_data=self.market_data,
            ws_client=self.ws_client,
            risk_manager=self.risk_manager,
            order_manager=self.order_manager,
            notifier=self.notifier,
            trade_logger=self.trade_logger,
            scheduler=self.scheduler,
        )

        self.ist = pytz.timezone(config.TIMEZONE)
        self._running = False
        self._deployed_today = False
        self._current_strategy: StrategyType = None

        # Register graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def _shutdown_handler(self, signum, frame):
        """Handle Ctrl+C / SIGTERM gracefully."""
        logger.info("\n🛑 Shutdown signal received. Cleaning up...")
        self._running = False
        self.ws_client.disconnect()
        self.notifier.stop_heartbeat()
        self.notifier.send_alert("🛑 Bot shutting down (manual signal)")
        logger.info("Shutdown complete.")
        sys.exit(0)

    def preflight_checks(self) -> bool:
        """
        Phase 1: Verify surroundings before risking capital.
        - Connectivity check
        - Time sync
        - Wallet balance
        """
        logger.info("── PREFLIGHT CHECKS ──")

        # 1. Connectivity
        if not self.exchange.check_connectivity():
            logger.error("Preflight FAILED: No API connectivity")
            self.notifier.send_error_alert("Preflight FAILED: No API connectivity")
            return False

        # 2. Time sync
        self.exchange.check_time_sync()

        # 3. Wallet balance
        balance = self.exchange.get_wallet_balance()
        if balance < config.CAPITAL * 0.1:
            logger.error(
                f"Preflight FAILED: Insufficient balance ₹{balance:,.2f}"
            )
            self.notifier.send_error_alert(
                f"Insufficient balance: ₹{balance:,.2f}"
            )
            return False

        logger.info(f"✅ Preflight checks PASSED (balance: ₹{balance:,.2f})")
        return True

    def deploy_strategy(self):
        """
        Phase 2: Analyze market and deploy the appropriate strategy.
        Called at 10:00 AM IST.
        """
        logger.info("=" * 50)
        logger.info("  📈 DEPLOYING STRATEGY")
        logger.info("=" * 50)

        try:
            # Step 1: Fetch candles
            logger.info("Step 1: Fetching hourly candles...")
            df = self.market_data.get_hourly_candles()
            if df.empty:
                logger.error("No candle data available. Aborting deployment.")
                self.notifier.send_error_alert("No candle data for deployment")
                return False

            # Step 2: Compute indicators
            logger.info("Step 2: Computing technical indicators...")
            df = compute_all(df)

            # Step 3: Detect regime
            logger.info("Step 3: Detecting market regime...")
            regime = detect_regime(df)

            # Step 4: Fetch option chain
            logger.info("Step 4: Fetching option chain...")
            chain = self.market_data.get_option_chain()
            if not chain:
                logger.error("No option chain data. Aborting deployment.")
                self.notifier.send_error_alert("No option chain for deployment")
                return False

            # Step 5: Check IV Rank for wing width
            logger.info("Step 5: Checking IV rank...")
            iv_rank = self.market_data.get_iv_rank(chain)
            wide_wings = check_volatility(iv_rank)

            # Step 6: Build strategy
            logger.info("Step 6: Building strategy...")
            strategy_type, order_specs = build_strategy(
                regime=regime,
                chain=chain,
                wide_wings=wide_wings,
            )

            if not order_specs:
                logger.error("Strategy produced no orders. Aborting.")
                self.notifier.send_error_alert("Strategy produced no orders")
                return False

            # Step 7: Validate margin
            logger.info("Step 7: Validating margin...")
            balance = self.exchange.get_wallet_balance()
            if not self.order_manager.validate_margin(order_specs, balance):
                logger.error("Margin validation failed. Aborting.")
                self.notifier.send_error_alert("Margin validation failed")
                return False

            # Step 8: Place batch orders
            logger.info("Step 8: Placing batch orders...")
            results = self.order_manager.place_batch_orders(order_specs)

            # Step 9: Place exchange-side protective orders
            logger.info("Step 9: Placing exchange-side SL/TP...")
            premium_collected = sum(
                o.limit_price for o in order_specs if o.side == "sell"
            )
            self.order_manager.place_protective_orders(order_specs, premium_collected)

            # Register premiums with risk manager
            for spec in order_specs:
                if spec.side == "sell":
                    self.risk_manager.register_premium(
                        spec.product_id, spec.limit_price
                    )

            # Step 10: Notify and log
            self._current_strategy = strategy_type
            self._deployed_today = True

            self.notifier.send_deployment_alert(
                strategy=strategy_type.value,
                regime=regime.value,
                legs=len(order_specs),
            )

            self.trade_logger.log_event(
                action="DEPLOY",
                notes=(
                    f"Strategy: {strategy_type.value}, "
                    f"Regime: {regime.value}, "
                    f"Legs: {len(order_specs)}, "
                    f"Wide Wings: {wide_wings}"
                ),
            )

            logger.info(
                f"✅ DEPLOYMENT COMPLETE: {strategy_type.value} "
                f"({len(order_specs)} legs)"
            )
            return True

        except Exception as e:
            logger.error(f"❌ Deployment failed: {e}", exc_info=True)
            self.notifier.send_error_alert(f"Deployment failed: {e}")
            return False

    def run(self):
        """
        Main loop: the heart of Operation Daily Profit.
        Runs continuously, deploying at 10 AM, monitoring PnL, managing risk.
        """
        # Preflight
        if not self.preflight_checks():
            logger.error("Preflight checks failed. Exiting.")
            return

        # Connect WebSocket
        if config.USE_WEBSOCKET:
            try:
                self.ws_client.connect(symbols=[config.UNDERLYING_SYMBOL])
                logger.info("WebSocket connected for real-time data")
            except Exception as e:
                logger.warning(f"WebSocket connection failed: {e}. Using REST fallback.")

        # Start Telegram heartbeat
        self.notifier.start_heartbeat(
            get_status_fn=self.monitor.get_status,
            interval=config.HEARTBEAT_INTERVAL,
        )

        self.notifier.send_alert("🟢 *Bot started!* Operation Daily Profit is live.")

        self._running = True
        logger.info("🚀 Main loop started")

        while self._running:
            try:
                now = self.scheduler.now()

                # ── Weekend Blackout ──
                if self.scheduler.is_weekend_blackout(now):
                    logger.info("🔒 Weekend blackout active. Sleeping...")
                    self.ws_client.disconnect()
                    time.sleep(300)  # Check every 5 minutes during blackout
                    continue

                # ── Reset daily flags at midnight ──
                if now.hour == 0 and now.minute < 5:
                    if self._deployed_today:
                        logger.info("🌅 New day – resetting daily flags")
                        self._deployed_today = False
                        self.risk_manager.reset_daily()

                # ── Deploy Strategy ──
                if (
                    self.scheduler.is_trading_day(now)
                    and self.scheduler.is_deploy_time(now)
                    and not self._deployed_today
                    and not self.risk_manager.is_day_done
                ):
                    self.deploy_strategy()

                # ── Monitor Positions ──
                if self._deployed_today and not self.risk_manager.is_day_done:
                    strategy = self._current_strategy or StrategyType.IRON_CONDOR
                    self.monitor.start_monitoring_loop(strategy)

                    # If monitoring loop exits (kill/payday/blackout), log summary
                    if self.risk_manager.is_day_done:
                        self._log_daily_summary()

                # ── Idle: wait for next deploy window ──
                if not self._deployed_today or self.risk_manager.is_day_done:
                    wait_sec = min(60, self.scheduler.seconds_until_deploy(now))
                    time.sleep(max(10, wait_sec))

            except KeyboardInterrupt:
                self._shutdown_handler(None, None)
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                self.notifier.send_error_alert(f"Main loop error: {e}")
                time.sleep(30)

    def _log_daily_summary(self):
        """Log end-of-day summary and send via Telegram."""
        summary = self.trade_logger.get_daily_summary()
        summary["strategy"] = (
            self._current_strategy.value if self._current_strategy else "N/A"
        )

        logger.info(
            f"📊 Daily Summary: "
            f"Net PnL=₹{summary['net_pnl']:,.2f}, "
            f"Trades={summary['total_trades']}"
        )

        self.notifier.send_daily_summary(summary)

        self.trade_logger.log_event(
            action="DAILY_SUMMARY",
            pnl=summary["net_pnl"],
            notes=f"Trades: {summary['total_trades']}, Strategy: {summary['strategy']}",
        )


def main():
    """Entry point."""
    trader = DeltaTrader()
    trader.run()


if __name__ == "__main__":
    main()
