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
from wakepy import keep
from logging.handlers import RotatingFileHandler

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
from risk_manager import RiskManager
from monitor import Monitor
from scheduler import Scheduler
from ws_client import WebSocketClient
from config import StrategyType

logging.basicConfig(
    level=logging.WARNING, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler("brain_execution.log", maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("analyze_0dte")
# We want to explicitly log our decision-making heavily to the file
logger.setLevel(logging.INFO)

def log_rejection(reason: str, spot_price: float = 0.0, regime_str: str = "UNKNOWN", context: dict = None):
    """Saves a simple summary of why the bot chose not to trade this cycle."""
    now_ist = datetime.now(pytz.timezone(config.TIMEZONE))
    summary = {
        "timestamp": now_ist.strftime('%Y-%m-%d %H:%M:%S'),
        "spot_price": round(spot_price, 2) if spot_price else 0.0,
        "regime": regime_str,
        "reason": reason,
        "status": "NO_TRADE"
    }
    if context:
        summary["context"] = context
        
    with open("rejection_log.jsonl", "a") as f:
        f.write(json.dumps(summary) + "\n")
    with open("latest_status.json", "w") as f:
        json.dump(summary, f, indent=4)


def get_market_liquidity_context(chain: list[dict]) -> dict:
    """Extracts liquidity stats (spreads) for QE verification."""
    if not chain:
        return {"error": "empty_chain"}
    
    spreads = []
    for opt in chain:
        bid = float(opt.get("best_bid", 0))
        ask = float(opt.get("best_ask", 0))
        mark = float(opt.get("mark_price", 0))
        if mark > 0:
            spreads.append((ask - bid) / mark)
    
    if not spreads:
        return {"error": "no_valid_quotes"}
        
    spreads.sort()
    return {
        "median_spread_pct": round(spreads[len(spreads)//2], 4),
        "max_spread_pct": round(max(spreads), 4),
        "min_spread_pct": round(min(spreads), 4),
        "quote_count": len(spreads)
    }


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
    
    # Check Time Sync at startup to avoid "Delta Drift"
    print("🕒 Checking clock synchronization with Delta servers...")
    exchange.check_time_sync()

    risk_manager = RiskManager()

    POLL_INTERVAL_SEC = 5 * 60  # 5 minutes
    START_HOUR = 12             # 12:00 PM IST
    CUTOFF_HOUR = 13            # 1:00 PM IST
    CUTOFF_MINUTE = 45          # 1:45 PM IST

    while True:
        try:
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
            
            # Initialize Monitoring Components
            trade_logger = TradeLogger()
            scheduler = Scheduler()
            notifier = Notifier()
            order_manager = OrderManager(exchange, trade_logger)
            
            if active_positions:
                print(f"\n🔄 STATE RECOVERY: {len(active_positions)} active position(s) found on Delta Exchange.")
                print("   Resuming monitoring loop to protect capital...")
                
                # Determine strategy
                strategy = StrategyType.IRON_CONDOR if len(active_positions) >= 4 else StrategyType.BULL_CREDIT_SPREAD
                
                # Register premiums for risk tracking
                for p in active_positions:
                    risk_manager.register_premium(int(p.get("product_id", 0)), float(p.get("avg_entry_price", 0)))
                
                # Initialize Monitor and Handoff
                ws_client = WebSocketClient(exchange)
                monitor = Monitor(exchange, market_data, ws_client, risk_manager, order_manager, notifier, trade_logger, scheduler)
                
                notifier.send_alert(f"🔄 *Recovery Mode*: Bot restarted and found an active {strategy.value.upper()} trade. Resuming monitoring.")
                monitor.start_monitoring_loop(strategy)
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
            
            # IV Rank Floor Filter for Iron Condors (Vega Risk Protection)
            if suggested_strategy == config.StrategyType.IRON_CONDOR and iv_rank < config.IV_ENTRY_MIN:
                reason = f"IV Rank ({iv_rank:.1f}%) is below minimum threshold ({config.IV_ENTRY_MIN}%). Risk of IV expansion is too high for Iron Condor."
                print(f"🛑 Safe Entry Filter: {reason} Skipping.")
                log_rejection(reason, current_spot, current_regime)
                print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
                time.sleep(POLL_INTERVAL_SEC)
                continue
            
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
                print(f"⚠️ Momentum Gap Widening (${curr_gap:.2f}). Proceed with caution.")
                
            # 6. Fetch Alternative Data (Sentiment & Liquidity)
            funding_rate = market_data.get_funding_rate()
            ob_imbalance = market_data.get_orderbook_imbalance("BTCUSD")
            
            # Immediate Math Blocking on Sentiment Extremes
            if funding_rate > 0.0005 and suggested_strategy == config.StrategyType.BULL_CREDIT_SPREAD:
                reason = f"Funding Rate ({funding_rate*100:.4f}%) extremely positive. Market overheated. Blocking Bull Spread."
                print(f"🛑 Safe Entry Filter: {reason}")
                log_rejection(reason, current_spot, current_regime)
                print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
                time.sleep(POLL_INTERVAL_SEC)
                continue
            elif funding_rate < -0.0005 and suggested_strategy == config.StrategyType.BEAR_CREDIT_SPREAD:
                reason = f"Funding Rate ({funding_rate*100:.4f}%) extremely negative. Squeeze risk. Blocking Bear Spread."
                print(f"🛑 Safe Entry Filter: {reason}")
                log_rejection(reason, current_spot, current_regime)
                print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
                time.sleep(POLL_INTERVAL_SEC)
                continue
            
            # 6. Build Strategy and get exact order specs (Strikes / Legs)
            # DYNAMIC LOT CALCULATION: Fetch balance and perform dry-run build first
            print("\n⚙️  Calculating Dynamic Lot Size (Safety First)...")
            wallet_balance = exchange.get_wallet_balance()
            # Convert INR balance to USD for the risk manager's constraints
            balance_usd = wallet_balance / config.USD_INR_RATE if wallet_balance > 1000 else wallet_balance
            
            # Dry-run with 1 lot to find the net premium per BTC
            try:
                _, dry_run_specs = build_strategy(
                    regime=regime,
                    chain=chain,
                    spot_price=spot_price,
                    wide_wings=wide_wings,
                    lot_size=1
                )
                if not dry_run_specs:
                    raise ValueError("No valid strikes for dry-run")
                
                # Calculate net premium for 1 lot (0.001 BTC)
                prem_coll = sum(leg.limit_price for leg in dry_run_specs if leg.side == "sell")
                prem_paid = sum(leg.limit_price for leg in dry_run_specs if leg.side == "buy")
                net_prem_1_lot = prem_coll - prem_paid
                # Convert to per 1.0 BTC for the risk manager's formula
                net_premium_per_btc = net_prem_1_lot / 0.001
                
                final_lots = risk_manager.calculate_safe_dynamic_lots(
                    available_balance_usd=balance_usd,
                    net_premium_per_btc=net_premium_per_btc,
                    spot_price=spot_price
                )
                
                if final_lots <= 0:
                    reason = f"Calculated lot size is 0. Safety constraints blocked trade."
                    print(f"🛑 {reason}")
                    log_rejection(reason, current_spot, current_regime)
                    print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
                    time.sleep(POLL_INTERVAL_SEC)
                    continue
                    
            except Exception as e:
                reason = f"Error during dry-run lot calculation: {e}"
                print(f"❌ {reason}")
                log_rejection(reason, current_spot, current_regime)
                print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
                time.sleep(POLL_INTERVAL_SEC)
                continue

            print(f"⚙️  Building final orders for: {suggested_strategy.value.replace('_', ' ').title()} ({final_lots} Lots)")
            try:
                strategy_type, order_specs = build_strategy(
                    regime=regime,
                    chain=chain,
                    spot_price=spot_price,
                    wide_wings=wide_wings,
                    lot_size=final_lots
                )
            except Exception as e:
                reason = f"Error building final strategy: {e}"
                print(f"❌ {reason}")
                log_rejection(reason, current_spot, current_regime)
                print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
                time.sleep(POLL_INTERVAL_SEC)
                continue
    
            if not order_specs:
                reason = "No valid strikes found passing Greek & Slippage Guard criteria."
                print(f"⚠️ {reason}")
                liquidity_context = get_market_liquidity_context(chain)
                log_rejection(reason, current_spot, current_regime, context=liquidity_context)
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
                log_rejection(reason, current_spot, current_regime, context={"net_credit": net_credit, "min_required": config.MIN_NET_CREDIT})
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
                "funding_rate": funding_rate,
                "ob_imbalance": ob_imbalance,
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
                
                # If AI was skipped due to quota/error, notify user via Telegram
                if ai_rationale.startswith("\u26a0"):
                    logger.warning(f"AI validation skipped - proceeding on math alone. ({ai_rationale[:100]})")
                    Notifier().send_alert(
                        f"\u26a0\ufe0f *AI Validation Skipped*\n\n"
                        f"Gemini API was unavailable (quota/token exhausted).\n"
                        f"Trade will proceed using *math criteria only*.\n\n"
                        f"_Stop the bot now if you want AI approval before trades._"
                    )
                
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
                
                # 1. Fire limit orders for all 4 legs natively
                print("🚀 Routing Multi-Leg Order to Delta Exchange...")
                order_manager.place_batch_orders(order_specs)
                
                # 2. CRITICAL: Wait for short legs to be confirmed filled before placing SL/TP
                # Delta Exchange REJECTS bracket orders on positions that don't exist yet
                print("⏳ Waiting for fill confirmation from Delta Exchange...")
                fills_confirmed = order_manager.wait_for_fills(order_specs, timeout_sec=60, poll_interval=3)
                if not fills_confirmed:
                    logger.warning("Fill confirmation timed out. Attempting SL/TP anyway — may fail if not filled.")
                    Notifier().send_alert(
                        "⚠️ *Fill Confirmation Timeout*\n\n"
                        "Short legs not confirmed after 60s. SL/TP placement attempted but may need manual check on Delta Exchange."
                    )
                else:
                    print("✅ Fills confirmed! Proceeding to place protective orders...")
    
                # 3. Fire Hard Stop-Loss and Take-Profit brackets now that position is confirmed open
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
            # Instead of exiting, enter the monitoring loop to watch the trade natively
            print("\n📈 ENTERING MONITORING LOOP... (AI & Risk Guards Active)")
            ws_client = WebSocketClient(exchange)
            monitor = Monitor(exchange, market_data, ws_client, risk_manager, order_manager, notifier, trade_logger, scheduler)
            monitor.start_monitoring_loop(strategy_type)
            
            sys.exit(0)
        except (ConnectionError, TimeoutError) as e:
            print(f"📡 Network Glitch: {e}. Retrying in 10s...")
            time.sleep(10)
            continue
        except Exception as e:
            logger.error(f"💥 Fatal Brain Error: {e}")
            Notifier().send_error_alert(f"Bot Crashed on Local Machine: {e}")
            sys.exit(1)

if __name__ == "__main__":
    with keep.presenting():
        main()
