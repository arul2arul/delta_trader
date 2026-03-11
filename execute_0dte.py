#!/usr/bin/env python3
"""
execute_0dte.py

Reads the daily_trade_context.json and executes the recommended orders
using the Delta Exchange API.
"""

import json
import logging
import sys
import os

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from config import OrderSpec
from exchange_client import ExchangeClient
from trade_logger import TradeLogger
from order_manager import OrderManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("execution_trigger.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("execute_0dte")

def main():
    context_file = "daily_trade_context.json"
    
    if not os.path.exists(context_file):
        logger.error(f"Error: {context_file} not found. No trade to execute.")
        sys.exit(1)
        
    with open(context_file, "r") as f:
        context = json.load(f)
        
    recommended_orders = context.get("recommended_orders", [])
    if not recommended_orders:
        logger.error("No recommended orders found in context.")
        sys.exit(1)
        
    logger.info(f"🚀 Initializing Execution for Strategy: {context.get('suggested_strategy')}")
    
    exchange = ExchangeClient()
    trade_logger = TradeLogger()
    order_manager = OrderManager(exchange, trade_logger)
    
    # Pre-execution balance check
    balance = exchange.get_wallet_balance()
    logger.info(f"💰 Current Balance: ₹{balance:,.2f}")
    
    # 1. Prepare OrderSpec objects
    order_specs = []
    for o in recommended_orders:
        spec = OrderSpec(
            product_id=o["product_id"],
            side=o["side"],
            size=o["size"],
            order_type=o["order_type"],
            limit_price=o.get("limit_price", 0.0)
        )
        order_specs.append(spec)
        
    # 2. Place Primary Orders (One by One for better reliability)
    logger.info(f"📡 Placing {len(order_specs)} primary legs...")
    for spec in order_specs:
        try:
            logger.info(f"Placing {spec.side} {spec.order_type} for product {spec.product_id} (size={spec.size})...")
            result = exchange.place_order(
                product_id=spec.product_id,
                size=spec.size,
                side=spec.side,
                order_type=spec.order_type,
                limit_price=spec.limit_price
            )
            logger.info(f"✅ Order for {spec.product_id} placed successfully.")
            
            # Log to trade_log.csv
            trade_logger.log_trade(
                action="OPEN",
                product_id=spec.product_id,
                side=spec.side,
                quantity=spec.size,
                price=spec.limit_price,
                notes=f"0-DTE Execution, role={spec.role}"
            )
        except Exception as e:
            logger.error(f"❌ Failed to place leg {spec.product_id}: {e}")
            # Depending on risk, we might want to panic/close others if one fails
            # For now, we continue and let the user handle it
            
    # 3. Place Protective Orders (SL/TP) for Short Legs
    logger.info("📡 Placing protective exchange-side SL/TP for short legs...")
    try:
        # Calculate premium collected
        premium_collected = sum(o.limit_price for o in order_specs if o.side == "sell")
        order_manager.place_protective_orders(order_specs, premium_collected)
        logger.info("✅ Protective orders submitted successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to place protective orders: {e}")
        
    logger.info("🎉 Execution sequence complete. Monitor the trade in Delta Exchange.")

if __name__ == "__main__":
    main()
