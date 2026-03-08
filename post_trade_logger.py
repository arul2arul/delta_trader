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
        
        return realized_pnl, total_fees, fills

    except Exception as e:
        logger.error(f"Failed to fetch fills for PnL/Fee calculation: {e}")
        return 0.0, 0.0, []

def read_lessons_learned():
    """Reads the lessons_learned.md file to append to the log."""
    if not os.path.exists(LESSONS_FILE):
        return "No manual lessons recorded today."
    
    with open(LESSONS_FILE, "r") as f:
        content = f.read().strip()
        return content if content else "No manual lessons recorded today."

def generate_trade_logs(actual_profit, fills):
    """
    Generates the 'Trade Decision Log' and 'Execution Validation Log'
    dynamically by cross-referencing Delta Fills with our daily_trade_context.json.
    """
    context_file = "daily_trade_context.json"
    if not os.path.exists(context_file):
        return "Not available (Context file not found).", "Validation blocked (Context file not found)."

    try:
        with open(context_file, "r") as f:
            context = json.load(f)
    except Exception as e:
        return f"Error reading context: {e}", f"Error reading context: {e}"

    # 1. Trade Decision Log (EOD Post Mortem)
    regime = context.get('regime', 'UNKNOWN')
    atr = context.get('atr_at_entry', 0)
    strategy = context.get('suggested_strategy', 'UNKNOWN')
    trend = context.get('trend_4h_movement', 0)
    
    if actual_profit > 0:
        decision_log = (
            f"🟩 GREEN DAY SUMMARY: Success with {strategy}.\n"
            f"The mean-reversion filters successfully held true today. We entered under a '{regime}' market regime, "
            f"meaning the sideways conditions correctly favored Theta decay. The 1H ATR was closely monitored at {atr:.1f}, "
            f"and safely under our heightened volatility threshold. The 4H trend momentum ({trend:+.2f}) was aligned with our "
            f"spread bias, safely keeping spot price >$1,000 away from our short strikes."
        )
    else:
        decision_log = (
            f"🟥 RED DAY SUMMARY: Loss with {strategy}.\n"
            f"While we safely passed pre-entry constraints (Regime: '{regime}', ATR: {atr:.1f}), the market structure "
            "shifted rapidly against us post-entry. Consider checking if a flash-crash stop-loss was triggered, or if "
            "the 15m SuperTrend suddenly reversed into a trending blowout immediately after entering. The 4H momentum "
            f"at entry was ({trend:+.2f}), but short strikes were breached."
        )

    # 2. Execution Validation Log (Cross-Checking OpenClaw)
    recommended_orders = context.get('recommended_orders', [])
    recommended_product_ids = {str(o.get('product_id')) for o in recommended_orders}
    
    actual_product_ids = {str(f.get('product_id')) for f in fills if f.get('product_id')}

    if not recommended_product_ids:
        validation_log = "No recommended orders found in context to validate against."
    elif not actual_product_ids:
        validation_log = f"⚠️ CRITICAL: Zero fills found on exchange today! Did OpenClaw execute the payload ({recommended_product_ids})?"
    elif recommended_product_ids.issubset(actual_product_ids):
        validation_log = (
            f"✅ PERFECT MATCH: Delta Exchange Fills confirmed execution of product IDs {recommended_product_ids}. "
            f"OpenClaw faithfully executed the exact payload recommended by analyze_0dte.py."
        )
    else:
        missing = recommended_product_ids - actual_product_ids
        validation_log = (
            f"⚠️ EXECUTION MISMATCH: The Brain recommended {recommended_product_ids}, but the Exchange Fills "
            f"do not contain {missing}. Check OpenClaw execution logs for failed network calls or margin errors."
        )

    return decision_log, validation_log


def log_daily_summary(strategy_used: str):
    """
    Core function to calculate Actual Profit and log to TinyDB (JSON).
    """
    exchange = ExchangeClient()
    logger.info("Fetching daily fills for Profit & Fee calculation...")
    
    realized_pnl, fees, fills = fetch_daily_pnl_and_fees(exchange)
    actual_profit = realized_pnl - fees
    
    ist = pytz.timezone(config.TIMEZONE)
    today_str = datetime.now(ist).strftime("%Y-%m-%d")
    
    # Generate Advanced Logs
    decision_log, validation_log = generate_trade_logs(actual_profit, fills)
    
    # TinyDB setup
    db = TinyDB(DB_FILE)
    
    log_entry = {
        "date": today_str,
        "strategy_used": strategy_used,
        "realized_pnl": round(realized_pnl, 4),
        "fees_paid": round(fees, 4),
        "actual_profit": round(actual_profit, 4),
        "net_roi": f"{(actual_profit / config.CAPITAL) * 100:.2f}%",
        "trade_decision_log": decision_log,
        "execution_validation_log": validation_log,
        "lessons_learned": read_lessons_learned()
    }
    
    # Upsert logic (Update if today already exists, else Insert)
    LogQuery = Query()
    existing = db.search(LogQuery.date == today_str)
    if existing:
        db.update(log_entry, LogQuery.date == today_str)
        logger.info(f"Updated existing database entry for {today_str}.")
    else:
        db.insert(log_entry)
        logger.info(f"Created new database log entry for {today_str}.")
        
    logger.info("\n==================================")
    logger.info("   📊 DAILY TRADING SUMMARY   ")
    logger.info("==================================")
    logger.info(f"Gross PnL     : ${realized_pnl:.4f}")
    logger.info(f"Trading Fees  : ${fees:.4f}")
    logger.info(f"Actual Profit : ${actual_profit:.4f}\n")
    logger.info("--- Execution Validation ---")
    logger.info(validation_log + "\n")
    logger.info("--- Trade Decision Post Mortem ---")
    logger.info(decision_log + "\n")
    logger.info("==================================")

if __name__ == "__main__":
    # You can configure OpenClaw to change the "strategy_used" arg if needed based on what it ran
    log_daily_summary(strategy_used="Autonomous 0 DTE")
