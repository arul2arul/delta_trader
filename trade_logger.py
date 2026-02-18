"""
Trade Logger – CSV logging for ITR-3 (Speculative Business Income) reporting.
Every trade, fee, and result is recorded.
"""

import os
import csv
import logging
from datetime import datetime

import pytz

import config

logger = logging.getLogger("trade_logger")


class TradeLogger:
    """Logs all trades and events to a CSV file."""

    CSV_HEADERS = [
        "timestamp",
        "action",
        "product_id",
        "strike",
        "type",
        "side",
        "quantity",
        "price",
        "fee",
        "pnl",
        "notes",
    ]

    def __init__(self, filepath: str = None):
        self.filepath = filepath or config.TRADE_LOG_FILE
        self.ist = pytz.timezone(config.TIMEZONE)
        self._ensure_file()

    def _ensure_file(self):
        """Create the CSV file with headers if it doesn't exist."""
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.CSV_HEADERS)
            logger.info(f"Created trade log: {self.filepath}")
        else:
            logger.info(f"Using existing trade log: {self.filepath}")

    def _timestamp(self) -> str:
        """Get current IST timestamp string."""
        return datetime.now(self.ist).strftime("%Y-%m-%d %H:%M:%S")

    def log_trade(
        self,
        action: str,
        product_id: int = 0,
        strike: float = 0,
        option_type: str = "",
        side: str = "",
        quantity: int = 0,
        price: float = 0,
        fee: float = 0,
        pnl: float = 0,
        notes: str = "",
    ):
        """Log a trade entry to the CSV file."""
        row = [
            self._timestamp(),
            action,
            product_id,
            strike,
            option_type,
            side,
            quantity,
            f"{price:.6f}" if price else "",
            f"{fee:.4f}" if fee else "",
            f"{pnl:.2f}" if pnl else "",
            notes,
        ]
        try:
            with open(self.filepath, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(row)
            logger.debug(f"Logged trade: {action} {side} {quantity}x product {product_id}")
        except Exception as e:
            logger.error(f"Failed to log trade: {e}")

    def log_event(
        self,
        action: str,
        notes: str = "",
        pnl: float = 0,
    ):
        """Log a system event (not a trade) to the CSV."""
        self.log_trade(action=action, notes=notes, pnl=pnl)

    def get_daily_summary(self, date_str: str = None) -> dict:
        """
        Calculate daily summary from the CSV for a given date.
        Defaults to today.
        """
        if date_str is None:
            date_str = datetime.now(self.ist).strftime("%Y-%m-%d")

        summary = {
            "date": date_str,
            "total_trades": 0,
            "gross_pnl": 0.0,
            "fees": 0.0,
            "net_pnl": 0.0,
            "actions": {},
        }

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = row.get("timestamp", "")
                    if not ts.startswith(date_str):
                        continue

                    summary["total_trades"] += 1

                    pnl_str = row.get("pnl", "")
                    if pnl_str:
                        summary["gross_pnl"] += float(pnl_str)

                    fee_str = row.get("fee", "")
                    if fee_str:
                        summary["fees"] += float(fee_str)

                    action = row.get("action", "")
                    summary["actions"][action] = summary["actions"].get(action, 0) + 1

            summary["net_pnl"] = summary["gross_pnl"] - summary["fees"]

        except FileNotFoundError:
            logger.warning(f"Trade log not found: {self.filepath}")
        except Exception as e:
            logger.error(f"Error reading trade log: {e}")

        return summary

    def get_all_entries(self, date_str: str = None) -> list[dict]:
        """Get all log entries, optionally filtered by date."""
        entries = []
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if date_str and not row.get("timestamp", "").startswith(date_str):
                        continue
                    entries.append(dict(row))
        except Exception as e:
            logger.error(f"Error reading trade log: {e}")
        return entries
