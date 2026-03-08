#!/usr/bin/env python3
"""
Post-Trade Logging & Feedback Loop
Fetches Realized PnL and Trading Fees from Delta Exchange, calculates Actual Profit,
and saves the daily summary to a TinyDB/JSON structured log.
"""

import os
import json
import logging
from datetime import datetime
import pytz
from tinydb import TinyDB, Query

from exchange_client import ExchangeClient
import config

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger("post_trade_logger")

DB_FILE = "trading_history.json"
LESSONS_FILE = "lessons_learned.md"

def fetch_daily_pnl_and_fees(exchange: ExchangeClient):
    """
    Fetch all fills for the current day to calculate Realized PnL and accurate Trading Fees.
    """
    ist = pytz.timezone(config.TIMEZONE)
    now = datetime.now(ist)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    payload = {
        "start": int(start_of_day.timestamp()),
        "end": int(now.timestamp()),
        "page_size": 500
    }

    try:
        # Fetch directly via REST pass-through
        resp = exchange.client.request("GET", "/v2/fills", payload, auth=True)
        fills = resp.get("result", []) if isinstance(resp, dict) else []
        
        realized_pnl = sum(float(f.get("realized_pnl", 0)) for f in fills)
        total_fees = sum(float(f.get("fee", 0)) for f in fills)
        
        return realized_pnl, total_fees

    except Exception as e:
        logger.error(f"Failed to fetch fills for PnL/Fee calculation: {e}")
        return 0.0, 0.0

def read_lessons_learned():
    """Reads the lessons_learned.md file to append to the log."""
    if not os.path.exists(LESSONS_FILE):
        return "No manual lessons recorded today."
    
    with open(LESSONS_FILE, "r") as f:
        content = f.read().strip()
        return content if content else "No manual lessons recorded today."

def log_daily_summary(strategy_used: str):
    """
    Core function to calculate Actual Profit and log to TinyDB (JSON).
    """
    exchange = ExchangeClient()
    logger.info("Fetching daily fills for Profit & Fee calculation...")
    
    realized_pnl, fees = fetch_daily_pnl_and_fees(exchange)
    actual_profit = realized_pnl - fees
    
    ist = pytz.timezone(config.TIMEZONE)
    today_str = datetime.now(ist).strftime("%Y-%m-%d")
    
    # TinyDB setup
    db = TinyDB(DB_FILE)
    
    log_entry = {
        "date": today_str,
        "strategy_used": strategy_used,
        "realized_pnl": round(realized_pnl, 4),
        "fees_paid": round(fees, 4),
        "actual_profit": round(actual_profit, 4),
        "net_roi": f"{(actual_profit / config.CAPITAL) * 100:.2f}%",
        "lessons_learned": read_lessons_learned()
    }
    
    # Upsert logic (Update if today already exists, else Insert)
    LogQuery = Query()
    existing = db.search(LogQuery.date == today_str)
    if existing:
        db.update(log_entry, LogQuery.date == today_str)
        logger.info(f"Updated existing entry for {today_str}.")
    else:
        db.insert(log_entry)
        logger.info(f"Created new log entry for {today_str}.")
        
    logger.info("--- 📊 Daily Trading Summary ---")
    logger.info(f"Gross PnL     : ${realized_pnl:.4f}")
    logger.info(f"Trading Fees  : ${fees:.4f}")
    logger.info(f"Actual Profit : ${actual_profit:.4f}")
    logger.info("----------------------------------")

if __name__ == "__main__":
    # Example trigger for 5:20 PM expiry execution
    log_daily_summary(strategy_used="Iron Condor / Adaptive Auto")
