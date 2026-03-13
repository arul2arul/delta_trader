#!/usr/bin/env python3
"""
analyze_0dte.py

The main OpenClaw "Brain" script.
Fetches market data, computes indicators (RSI, EMA, ADX, VWAP, ATR),
detects regime (Fear/Greed included), and builds option spread/condor recommendations.
Outputs a clean text summary for OpenClaw to read and relay heavily to Telegram.
This script natively handles its own API Execution and bracket SL/TP placement to prevent LLM errors.
"""

import sys
import os
import logging
import json
from datetime import datetime
import time
import pytz
import io

# Force UTF-8 output to handle emojis on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import config
from exchange_client import ExchangeClient
from market_data import MarketData
from indicators import compute_all
from regime_detector import detect_regime, check_volatility, get_strategy_for_regime
from strategy_engine import build_strategy
from ai_validator import ask_ai_for_second_opinion
from trade_logger import TradeLogger
from order_manager import OrderManager
from notifier import Notifier

logging.basicConfig(
    level=logging.WARNING, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("brain_execution.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("analyze_0dte")
# We want to explicitly log our decision-making heavily to the file
logger.setLevel(logging.INFO)

def log_rejection(reason: str, spot_price: float = 0.0, regime_str: str = "UNKNOWN"):
    """Saves a simple summary of why the bot chose not to trade this cycle."""
    now_ist = datetime.now(pytz.timezone(config.TIMEZONE))
    summary = {
        "timestamp": now_ist.strftime('%Y-%m-%d %H:%M:%S'),
        "spot_price": round(spot_price, 2) if spot_price else 0.0,
        "regime": regime_str,
        "reason": reason,
        "status": "NO_TRADE"
    }
    with open("rejection_log.jsonl", "a") as f:
        f.write(json.dumps(summary) + "\n")
    with open("latest_status.json", "w") as f:
        json.dump(summary, f, indent=4)


TRADE_LOCK_FILE = ".trade_lock"

def set_trade_lock(strategy: str, spot_price: float):
    """Write a lock file immediately before placing orders.
    This prevents duplicate execution if the script crashes and restarts.
    Delete this file manually ONLY after confirming what happened on Delta Exchange.
    """
    now_ist = datetime.now(pytz.timezone(config.TIMEZONE))
    lock_data = {
        "locked_at": now_ist.strftime('%Y-%m-%d %H:%M:%S'),
        "strategy": strategy,
        "spot_price": round(spot_price, 2),
        "note": "Delete this file ONLY after manually verifying Delta Exchange positions are correct."
    }
    with open(TRADE_LOCK_FILE, "w") as f:
        json.dump(lock_data, f, indent=4)
    print(f"\n🔒 Trade lock file written ({TRADE_LOCK_FILE}). Will be cleared on success.")


def check_trade_lock():
    """Check if a lock file exists from a previous execution.
    Returns True if locked (do NOT proceed), False if clear.
    """
    if os.path.exists(TRADE_LOCK_FILE):
        with open(TRADE_LOCK_FILE, "r") as f:
            lock_data = json.load(f)
        print(f"\n🔒 TRADE LOCK DETECTED from {lock_data.get('locked_at')}.")
        print(f"   Strategy attempted: {lock_data.get('strategy')} | Spot: ${lock_data.get('spot_price')}")
        print(f"   ⚠️  A previous execution attempt may have partially placed orders.")
        print(f"   Please verify Delta Exchange positions manually, then delete '{TRADE_LOCK_FILE}' to re-enable.")
        return True
    return False


def main():
    print("🧠 Initiating 0 DTE Brain Analysis (Polling Mode)...\n")
    exchange = ExchangeClient()
    market_data = MarketData(exchange)

    POLL_INTERVAL_SEC = 5 * 60  # 5 minutes
    START_HOUR = 12             # 12:00 PM IST
    CUTOFF_HOUR = 13            # 1:00 PM IST
    CUTOFF_MINUTE = 45          # 1:45 PM IST

    while True:
        now_ist = datetime.now(pytz.timezone(config.TIMEZONE))
        current_spot = 0.0
        current_regime = "UNKNOWN"
        
        # Monitor start time
        if now_ist.hour < START_HOUR:
            print(f"💤 Window not open yet. Waiting {POLL_INTERVAL_SEC // 60}m... (Opens {START_HOUR}:00 IST)")
            time.sleep(POLL_INTERVAL_SEC)
            continue
            
        # Hard cutoff time
        if now_ist.hour > CUTOFF_HOUR or (now_ist.hour == CUTOFF_HOUR and now_ist.minute >= CUTOFF_MINUTE):
            reason = f"Cutoff time reached ({CUTOFF_HOUR}:{CUTOFF_MINUTE} IST). Skipping Day."
            print(f"\n🕘 Criteria Not Met - {reason} Shutting down.")
            log_rejection(reason, current_spot, current_regime)
            sys.exit(0)

        # State Management (Double Entry check)
        # Level 1: Check lock file on disk (guards against crash-restart loops)
        if check_trade_lock():
            print("🛑 Halting to prevent duplicate orders. Manually review and delete '.trade_lock' to continue.")
            sys.exit(0)

        # Level 2: Check live open positions on Delta Exchange API
        open_positions = exchange.get_positions()
        # Filter only positions with non-zero size (actual open trades)
        active_positions = [p for p in (open_positions or []) if abs(int(p.get("size", 0))) > 0]
        if active_positions:
            print(f"\n🛑 State Management: {len(active_positions)} active position(s) found on Delta Exchange. Preventing Double-Entry.")
            sys.exit(0)

        print(f"\n==================================================")
        print(f"🔄 [{now_ist.strftime('%H:%M:%S IST')}] Checking Market Conditions")
        print(f"==================================================")

        # 1. Fetch Spot
        spot_price = market_data.get_spot_price()
        current_spot = spot_price
        print(f"💰 Spot Price: ${spot_price:,.2f}")

        # 2. Fetch Candles & Indicators (1h for regime, 15m for strict entry rules)
        df = market_data.get_candles(resolution="1h")
        df_15m = market_data.get_candles(resolution="15m")
        
        if df.empty or df_15m.empty:
            reason = "No candle data available."
            print(f"❌ Error: {reason}")
            log_rejection(reason, current_spot, current_regime)
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        df = compute_all(df)
        df_15m = compute_all(df_15m)
        
        # 3. Detect Regime
        regime = detect_regime(df)
        current_regime = regime.value
        latest = df.iloc[-1]
        print(f"📊 Market Regimen: {regime.value.upper()}")
        print(f"   RSI: {latest['rsi']:.1f} | ADX: {latest['adx']:.1f} | ATR: {latest['atr']:.1f} | VWAP: {latest['vwap']:.1f}")

        # 4. Strategy Selection based on Regime & Fear Guage
        suggested_strategy = get_strategy_for_regime(regime)
        
        # 5. Fetch Option Chain and Check Volatility
        chain = market_data.get_option_chain()
        if not chain:
            reason = "No option chain available for 0 DTE contracts."
            print(f"❌ Error: {reason}")
            log_rejection(reason, current_spot, current_regime)
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
            
        iv_rank = market_data.get_iv_rank(chain)
        wide_wings = check_volatility(iv_rank)
        print(f"⚡ IV Rank: {iv_rank:.1f}% (Wide Wings: {wide_wings})")
        
        # --- Pre-Entry Logic Check ---
        is_supertrend_red = df_15m.iloc[-1].get("supertrend_dir", 1) < 0
        
        # Momentum Gap Filter (EMA 9)
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        prev_gap = abs(prev["close"] - prev.get("ema_9", prev["close"]))
        curr_gap = abs(curr["close"] - curr.get("ema_9", curr["close"]))
        widening_gap = curr_gap > prev_gap
        
        if is_supertrend_red and suggested_strategy == config.StrategyType.BULL_CREDIT_SPREAD:
            reason = "Red SuperTrend Ban triggered on 15m timeframe! Blocking Bull Put Spread."
            print(f"🛑 ALARM: {reason}")
            print("⚠️ Suggesting NO TRADE instead to protect capital.")
            log_rejection(reason, current_spot, current_regime)
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        
        # ATR Filter (>20% higher than 3-day average)
        current_atr = df['atr'].iloc[-1]
        # 3-day average of hourly ATR = roughly 72 hourly periods
        avg_atr_3d = df['atr'].tail(72).mean()
        if current_atr > 1.20 * avg_atr_3d:
            reason = f"ATR ({current_atr:.2f}) is > 20% higher than 3-day average ({avg_atr_3d:.2f})."
            print(f"🛑 Safe Entry Filter: {reason} Skipping.")
            log_rejection(reason, current_spot, current_regime)
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
            
        # Consolidation Filter (High/Low range of last 60 minutes < $400)
        # 60 mins = 4 x 15-min candles
        high_60m = df_15m['high'].tail(4).max()
        low_60m = df_15m['low'].tail(4).min()
        range_60m = high_60m - low_60m
        if range_60m >= 400.0:
            reason = f"60m Consolidation range is ${range_60m:.2f} (>= $400)."
            print(f"🛑 Safe Entry Filter: {reason} Skipping.")
            log_rejection(reason, current_spot, current_regime)
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
            
        # Trend Anchor Filter (1h chart trend logic, 4h lookback)
        close_now = df['close'].iloc[-1]
        close_4h_ago = df['close'].iloc[-5] if len(df) >= 5 else df['close'].iloc[0]
        # Calculate the 4h momentum
        trend_movement_4h = close_now - close_4h_ago
        if trend_movement_4h <= -250.0 and suggested_strategy == config.StrategyType.BULL_CREDIT_SPREAD:
            reason = f"1H Trend Anchor is strongly Bearish (dropped ${abs(trend_movement_4h):.2f} in 4H). Blocking Bull Spread."
            print(f"🛑 Safe Entry Filter: {reason}")
            log_rejection(reason, current_spot, current_regime)
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        if trend_movement_4h >= 250.0 and suggested_strategy == config.StrategyType.BEAR_CREDIT_SPREAD:
            reason = f"1H Trend Anchor is strongly Bullish (rose ${trend_movement_4h:.2f} in 4H). Blocking Bear Spread."
            print(f"🛑 Safe Entry Filter: {reason}")
            log_rejection(reason, current_spot, current_regime)
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
            
        if widening_gap and regime != config.Regime.SIDEWAYS:
            # If gap is widening against trend... wait, if trend is against, but our strategy is matching regime.
            print(f"⚠️ Momentum Gap Widening (${curr_gap:.2f}). Proceed with caution.")
            
        # 1:30 PM Funding Check
        now_ist = datetime.now(pytz.timezone(config.TIMEZONE))
        # If the time is around 1:00 PM - 2:00 PM IST
        if now_ist.hour == 13:
            funding_rate = market_data.get_funding_rate()
            print(f"🕒 1:30 PM Funding Check triggered. Current Funding Rate: {funding_rate * 100:.4f}%")
            
            # Funding rate > 0 means Longs pay Shorts. Meaning heavily biased bullish sentiment.
            # Too high positive rate = dangerous to go long, dangerous for Bull Spreads.
            # If negative, heavily biased bearish shorting, dangerous for Bear Spreads.
            if funding_rate > 0.0005 and suggested_strategy == config.StrategyType.BULL_CREDIT_SPREAD:
                reason = "Funding Rate extremely positive. Too much long leverage in the market. Blocking Bull Put Spread."
                print(f"🛑 ALARM: {reason}")
                log_rejection(reason, current_spot, current_regime)
                print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
                time.sleep(POLL_INTERVAL_SEC)
                continue
            elif funding_rate < -0.0005 and suggested_strategy == config.StrategyType.BEAR_CREDIT_SPREAD:
                reason = "Funding Rate extremely negative. Too much short leverage in the market. Blocking Bear Call Spread."
                print(f"🛑 ALARM: {reason}")
                log_rejection(reason, current_spot, current_regime)
                print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
                time.sleep(POLL_INTERVAL_SEC)
                continue
        
        # 6. Build Strategy and get exact order specs (Strikes / Legs)
        print(f"\n⚙️  Building orders for: {suggested_strategy.value.replace('_', ' ').title()} ({config.BASE_LOT_SIZE} Lots)")
        try:
            strategy_type, order_specs = build_strategy(
                regime=regime,
                chain=chain,
                spot_price=spot_price,
                wide_wings=wide_wings,
                lot_size=config.BASE_LOT_SIZE
            )
        except Exception as e:
            reason = f"Error building strategy: {e}"
            print(f"❌ {reason}")
            log_rejection(reason, current_spot, current_regime)
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        if not order_specs:
            reason = "No valid strikes found passing Greek & Slippage Guard criteria."
            print(f"⚠️ {reason}")
            log_rejection(reason, current_spot, current_regime)
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        print("\n" + "="*50)
        print(f"🤖 OPENCLAW RECOMMENDATION: {strategy_type.value.upper()}")
        print("="*50)
        
        premium_collected = 0.0
        premium_paid = 0.0

        for leg in order_specs:
            action = leg.side.upper()
            strike = leg.strike_price
            opt_type = leg.option_type.replace("_options", "").upper()
            price = leg.limit_price
            
            if leg.side == "sell":
                premium_collected += price
                direction = "Short"
            else:
                premium_paid += price
                direction = "Long "
                
            role = leg.role.upper()
            print(f"  [{direction}] {action} {opt_type} @ {strike:,.0f} | Premium: ${price:.4f} | Product: {leg.product_id}")

        net_credit = premium_collected - premium_paid
        print(f"\n💵 Est. Net Credit: ${net_credit:.4f}")
        
        # 7. Fee-Aware Exit Check
        # Configurable via config.MIN_NET_CREDIT to avoid 'working for the exchange'
        if net_credit < config.MIN_NET_CREDIT:
            reason = f"Fee-Aware Exit Triggered. Net Credit ${net_credit:.2f} is < ${config.MIN_NET_CREDIT}. Trade rejected."
            print(f"🛑 ALARM: {reason}")
            log_rejection(reason, current_spot, current_regime)
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        
        # Dump raw JSON at the end for OpenClaw to parse programmatically if needed
        api_payload = []
        for leg in order_specs:
            leg_dict = {
                "product_id": leg.product_id,
                "side": leg.side,
                "size": leg.size,
                "order_type": leg.order_type,
                "limit_price": leg.limit_price
            }
            # Only attach Stop Loss and Take Profit bounds for the naked Short Legs
            if leg.side == "sell":
                leg_dict["stop_loss_price"] = round(leg.limit_price * config.STOPLOSS_MULTIPLIER, 2)
                leg_dict["take_profit_price"] = round(leg.limit_price * 0.10, 2)
                
            api_payload.append(leg_dict)

        # Prepare Trade Decision Context
        trade_context = {
            "date": now_ist.strftime('%Y-%m-%d'),
            "entry_time": now_ist.strftime('%H:%M:%S'),
            "spot_price": spot_price,
            "regime": regime.value,
            "atr_at_entry": current_atr,
            "atr_3d_avg": avg_atr_3d,
            "trend_4h_movement": trend_movement_4h,
            "consolidation_range_60m": range_60m,
            "suggested_strategy": strategy_type.value,
            "net_credit_expected": net_credit,
            "funding_rate": funding_rate if now_ist.hour == 13 else 0, # Pass from above if available
            "recommended_orders": api_payload
        }

        # --- AI Second Opinion Check ---
        ai_rationale = ""
        if getattr(config, "USE_AI_VALIDATION", False):
            print("\n🤖 Consulting AI Model (Gemini) for Second Opinion...")
            ai_result = ask_ai_for_second_opinion(trade_context)
            confidence = ai_result.get("confidence_score", 10)
            ai_rationale = ai_result.get("rationale", "")
            
            print(f"🧠 AI Assessment:\n{ai_rationale}")
            
            if confidence <= 5:
                reason = f"AI Validation Failed (Confidence {confidence}/10). The mathematical setup looks poor to the AI."
                print(f"\n🛑 ALARM: {reason} Trade rejected.")
                log_rejection(reason, current_spot, current_regime)
                print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
                time.sleep(POLL_INTERVAL_SEC)
                continue
            else:
                print(f"✅ AI Validation Passed (Confidence {confidence}/10). Proceeding to execution payload.")

        # ==========================================================
        # 🔥 NATIVE EXECUTION BLOCK (Replaces LLM Middleman)
        # ==========================================================
        print("\n💥 INITIALIZING LIVE EXECUTION 💥")
        trade_logger = TradeLogger()
        order_manager = OrderManager(exchange, trade_logger)
        
        # Fetch true margin to ensure safety
        wallet_balance = exchange.get_wallet_balance()
        if not order_manager.validate_margin(order_specs, wallet_balance):
            reason = "Margin Validation Failed. Not enough capital to safely place Iron Condor / Spread."
            print(f"🛑 ALARM: {reason} Trade rejected.")
            log_rejection(reason, current_spot, current_regime)
            sys.exit(0)
            
        try:
            # Write lock file FIRST, before ANY API call to Delta - prevents duplicate on crash/restart
            set_trade_lock(strategy=strategy_type.value, spot_price=spot_price)
            
            # 1. Fire limit & market orders for the wings natively
            print("🚀 Routing Multi-Leg Order to Delta Exchange...")
            order_manager.place_batch_orders(order_specs)
            
            # 2. Fire immediate Hard Stop-Loss and Take-Profit bounds
            print("🛡️ Placing Exchange-Side Stop Loss & Take Profit Guards...")
            order_manager.place_protective_orders(order_specs, net_credit)
            
            print("\n✅ Execution Fully Successful!")
            
            # SUCCESS: Remove lock file — execution was clean
            if os.path.exists(TRADE_LOCK_FILE):
                os.remove(TRADE_LOCK_FILE)
                print("🔓 Trade lock released.")
            
            # Send Notification natively
            notifier = Notifier()
            msg = (
                f"✅ *TRADE SUCCESSFULLY EXECUTED*\n\n"
                f"📈 *Strategy*: {strategy_type.value.upper()}\n"
                f"💰 *Spot Price*: ${spot_price:,.2f}\n"
                f"💵 *Net Credit*: ${net_credit:.4f}\n\n"
                f"🛡️ Hard Stop-Loss & Take-Profit brackets have been securely placed natively on Delta Exchange servers."
            )
            # Add AI Rationale if exists
            if ai_rationale:
                msg += f"\n\n🤖 *AI Rationale*:\n{ai_rationale}"
                
            notifier.send_alert(msg)
            
        except Exception as e:
            reason = f"FATAL ERROR during native Order Routing: {e}"
            print(f"🛑 ALARM: {reason}")
            print(f"🔒 Lock file '{TRADE_LOCK_FILE}' has been PRESERVED. Manual review required before next run.")
            log_rejection(reason, current_spot, current_regime)
            
            # Alert user of fatal crash
            Notifier().send_error_alert(
                f"{reason}\n\n⚠️ Lock file preserved. Check Delta Exchange positions MANUALLY before deleting '.trade_lock'."
            )
            sys.exit(0)

        print("\n--- OPENCLAW JSON PAYLOAD (SUCCESSFULLY EXECUTED) ---")
        payload_dict = {
            "strategy": strategy_type.value,
            "underlying": spot_price,
            "net_credit": net_credit,
            "orders": api_payload,
            "execution_status": "SUCCESS - POSITIONS OPENED"
        }
        if ai_rationale:
            payload_dict["ai_assessment"] = ai_rationale
            
        payload_str = json.dumps(payload_dict, indent=2)
        print(payload_str)
        print("-----------------------------------------------------")
        
        logger.info(f"Generated Payload: {payload_str}")
        
        # Save Trade Decision Context for Post-Trade Logger to evaluate EOD
        with open("daily_trade_context.json", "w") as f:
            json.dump(trade_context, f, indent=4)
        logger.info("Saved 'daily_trade_context.json' for EOD verification.")
        
        
        # Exit successfully to OpenClaw execution after rendering a valid payload
        sys.exit(0)

if __name__ == "__main__":
    main()
