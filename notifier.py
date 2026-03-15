"""
Notifier – Telegram bot integration for heartbeat and alerts.
Sends "I am alive" messages every hour + instant alerts for risk events.
"""

import os
import logging
import threading
import time
from datetime import datetime

import pytz
from dotenv import load_dotenv

import config

logger = logging.getLogger("notifier")

load_dotenv()


class Notifier:
    """Telegram notifications for trading bot status."""

    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)
        self.ist = pytz.timezone(config.TIMEZONE)

        self._heartbeat_thread: threading.Thread = None
        self._heartbeat_running = False

        if self.enabled:
            logger.info("✅ Telegram notifications enabled")
        else:
            logger.warning(
                "⚠️ Telegram not configured. "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"
            )

    def _send_message(self, message: str):
        """Send a message via Telegram Bot API."""
        if not self.enabled:
            logger.debug(f"Telegram disabled. Would send: {message[:100]}...")
            return

        try:
            import requests

            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.debug("Telegram message sent successfully")
            else:
                logger.warning(
                    f"Telegram send failed (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}"
                )
        except Exception as e:
            logger.warning(f"Failed to send Telegram message: {e}")

    # ──────────────────────────────────────────
    # Alerts (Instant)
    # ──────────────────────────────────────────
    def send_alert(self, message: str):
        """Send an instant alert notification."""
        now = datetime.now(self.ist).strftime("%H:%M:%S IST")
        alert = f"🚨 *ALERT* [{now}]\n{message}"
        self._send_message(alert)
        logger.info(f"Alert sent: {message[:80]}")

    def send_kill_switch_alert(self, pnl: float):
        """Alert for kill switch activation."""
        self.send_alert(
            f"🔴 *KILL SWITCH ACTIVATED*\n"
            f"Unrealized PnL: ₹{pnl:,.2f}\n"
            f"All positions being closed!"
        )

    def send_payday_alert(self, pnl: float):
        """Alert for PayDay profit lock."""
        self.send_alert(
            f"💰 *PAY DAY!*\n"
            f"Net Profit: ₹{pnl:,.2f}\n"
            f"Profits locked. Trading done for today! 🎉"
        )

    def send_deployment_alert(self, strategy: str, regime: str, legs: int):
        """Alert for new strategy deployment."""
        now = datetime.now(self.ist).strftime("%H:%M:%S IST")
        self._send_message(
            f"📈 *Strategy Deployed* [{now}]\n"
            f"Regime: {regime}\n"
            f"Strategy: {strategy}\n"
            f"Legs: {legs}"
        )

    def send_error_alert(self, error: str):
        """Alert for critical errors."""
        self.send_alert(f"❌ *ERROR*\n{error}")

    def send_leg_breach_alert(self, product_id: int, price: float, strike: float):
        """Alert for short leg breach."""
        self.send_alert(
            f"⚠️ *LEG BREACHED*\n"
            f"Product: {product_id}\n"
            f"Price: {price:.2f} hit Strike: {strike:.2f}\n"
            f"Rolling/closing immediately!"
        )

    def send_heartbeat(
        self,
        pnl: float = 0,
        positions: int = 0,
        strategy: str = "None",
        last_check: str = "",
    ):
        """
        'I am alive' message every hour.
        Shows current PnL, open positions, active strategy.
        """
        now = datetime.now(self.ist).strftime("%Y-%m-%d %H:%M IST")
        self.last_check = last_check or now
        
        heartbeat = (
            f"💓 *Heartbeat* [{now}]\n"
            f"Status: ✅ Running\n"
            f"PnL: ₹{pnl:,.2f}\n"
            f"Open Positions: {positions}\n"
            f"Active Strategy: {strategy.replace('_', ' ')}\n"
            f"Last Check: {self.last_check}"
        )
        self._send_message(heartbeat)
        logger.info("Heartbeat sent")

    def send_performance_report(self, trades: list):
        """Sends a summary of recent trade performance."""
        if not trades:
            return
            
        report = "📊 *Recent Performance (Last 5)*\n"
        for t in trades:
            status_emoji = "✅" if t['final_pnl_inr'] > 0 else "❌"
            date_str = t['timestamp'].split(' ')[0]
            # Telegram markdown fix: underscores in strategy names break formatting
            strat_clean = t['strategy'].replace("_", " ")
            report += f"{status_emoji} {date_str} | {strat_clean} | PnL: ₹{t['final_pnl_inr']:,.0f}\n"
            
        self._send_message(report)

    def start_heartbeat(
        self,
        get_status_fn=None,
        interval: int = config.HEARTBEAT_INTERVAL,
    ):
        """
        Start the background heartbeat thread.
        Sends status every `interval` seconds.

        Args:
            get_status_fn: Callable returning dict with keys:
                pnl, positions, strategy, last_check.
        """
        if self._heartbeat_running:
            return

        self._heartbeat_running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(get_status_fn, interval),
            daemon=True,
            name="heartbeat",
        )
        self._heartbeat_thread.start()
        logger.info(
            f"Heartbeat thread started (interval: {interval}s / "
            f"{interval // 60} min)"
        )

    def _heartbeat_loop(self, get_status_fn, interval: int):
        """Background loop sending periodic heartbeats."""
        while self._heartbeat_running:
            try:
                if get_status_fn:
                    status = get_status_fn()
                    self.send_heartbeat(
                        pnl=status.get("pnl", 0),
                        positions=status.get("positions", 0),
                        strategy=status.get("strategy", "None"),
                        last_check=status.get("last_check", ""),
                    )
                else:
                    self.send_heartbeat()
            except Exception as e:
                logger.warning(f"Heartbeat error: {e}")

            time.sleep(interval)

    def stop_heartbeat(self):
        """Stop the heartbeat thread."""
        self._heartbeat_running = False
        logger.info("Heartbeat stopped")

    # ──────────────────────────────────────────
    # Daily Summary
    # ──────────────────────────────────────────
    def send_daily_summary(self, summary: dict):
        """End-of-day P&L report via Telegram."""
        now = datetime.now(self.ist).strftime("%Y-%m-%d")
        msg = (
            f"📊 *Daily Summary* [{now}]\n"
            f"Gross PnL: ₹{summary.get('gross_pnl', 0):,.2f}\n"
            f"Fees: ₹{summary.get('fees', 0):,.2f}\n"
            f"Net PnL: ₹{summary.get('net_pnl', 0):,.2f}\n"
            f"Trades: {summary.get('total_trades', 0)}\n"
            f"Strategy: {summary.get('strategy', 'N/A')}\n"
            f"Regime: {summary.get('regime', 'N/A')}"
        )
        self._send_message(msg)
        logger.info("Daily summary sent via Telegram")
