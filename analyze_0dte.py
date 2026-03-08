#!/usr/bin/env python3
"""
analyze_0dte.py

The main OpenClaw "Brain" script.
Fetches market data, computes indicators (RSI, EMA, ADX, VWAP, ATR),
detects regime (Fear/Greed included), and builds option spread/condor recommendations.
Outputs a clean text summary for OpenClaw to read and act upon.
NO direct executions happen here.
"""

import sys
import logging
import json
from datetime import datetime
import time
import pytz

import config
from exchange_client import ExchangeClient
from market_data import MarketData
from indicators import compute_all
from regime_detector import detect_regime, check_volatility, get_strategy_for_regime
from strategy_engine import build_strategy

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
        
        # Monitor start time
        if now_ist.hour < START_HOUR:
            print(f"💤 Window not open yet. Waiting {POLL_INTERVAL_SEC // 60}m... (Opens {START_HOUR}:00 IST)")
            time.sleep(POLL_INTERVAL_SEC)
            continue
            
        # Hard cutoff time
        if now_ist.hour > CUTOFF_HOUR or (now_ist.hour == CUTOFF_HOUR and now_ist.minute >= CUTOFF_MINUTE):
            print(f"\n🕘 Criteria Not Met - Skipping Day. Cutoff time reached ({CUTOFF_HOUR}:{CUTOFF_MINUTE} IST). Shutting down.")
            sys.exit(0)

        # State Management (Double Entry check)
        # Fetch open positions directly from Delta Exchange to ensure we don't double enter
        open_positions = exchange.get_positions()
        if open_positions:
            print(f"\n🛑 State Management: Trade already executed/open. Found {len(open_positions)} positions active. Preventing Double-Entry.")
            sys.exit(0)

        print(f"\n==================================================")
        print(f"🔄 [{now_ist.strftime('%H:%M:%S IST')}] Checking Market Conditions")
        print(f"==================================================")

        # 1. Fetch Spot
        spot_price = market_data.get_spot_price()
        print(f"💰 Spot Price: ${spot_price:,.2f}")

        # 2. Fetch Candles & Indicators (1h for regime, 15m for strict entry rules)
        df = market_data.get_candles(resolution="1h")
        df_15m = market_data.get_candles(resolution="15m")
        
        if df.empty or df_15m.empty:
            print("❌ Error: No candle data available.")
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        df = compute_all(df)
        df_15m = compute_all(df_15m)
        
        # 3. Detect Regime
        regime = detect_regime(df)
        latest = df.iloc[-1]
        print(f"📊 Market Regimen: {regime.value.upper()}")
        print(f"   RSI: {latest['rsi']:.1f} | ADX: {latest['adx']:.1f} | ATR: {latest['atr']:.1f} | VWAP: {latest['vwap']:.1f}")

        # 4. Strategy Selection based on Regime & Fear Guage
        suggested_strategy = get_strategy_for_regime(regime)
        
        # 5. Fetch Option Chain and Check Volatility
        chain = market_data.get_option_chain()
        if not chain:
            print("❌ Error: No option chain available for 0 DTE contracts.")
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
            print("🛑 ALARM: Red SuperTrend Ban triggered on 15m timeframe! Blocking Bull Put Spread...")
            print("⚠️ Suggesting NO TRADE instead to protect capital.")
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        
        # ATR Filter (>20% higher than 3-day average)
        current_atr = df['atr'].iloc[-1]
        # 3-day average of hourly ATR = roughly 72 hourly periods
        avg_atr_3d = df['atr'].tail(72).mean()
        if current_atr > 1.20 * avg_atr_3d:
            print(f"🛑 Safe Entry Filter: ATR ({current_atr:.2f}) is > 20% higher than 3-day average ({avg_atr_3d:.2f}). Skipping.")
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
            
        # Consolidation Filter (High/Low range of last 60 minutes < $400)
        # 60 mins = 4 x 15-min candles
        high_60m = df_15m['high'].tail(4).max()
        low_60m = df_15m['low'].tail(4).min()
        range_60m = high_60m - low_60m
        if range_60m >= 400.0:
            print(f"🛑 Safe Entry Filter: 60m Consolidation range is ${range_60m:.2f} (>= $400). Skipping.")
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
            
        # Trend Anchor Filter (1h chart trend logic, 4h lookback)
        close_now = df['close'].iloc[-1]
        close_4h_ago = df['close'].iloc[-5] if len(df) >= 5 else df['close'].iloc[0]
        # Calculate the 4h momentum
        trend_movement_4h = close_now - close_4h_ago
        if trend_movement_4h <= -250.0 and suggested_strategy == config.StrategyType.BULL_CREDIT_SPREAD:
            print(f"🛑 Safe Entry Filter: 1H Trend Anchor is strongly Bearish (dropped ${abs(trend_movement_4h):.2f} in 4H). Blocking Bull Spread.")
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        if trend_movement_4h >= 250.0 and suggested_strategy == config.StrategyType.BEAR_CREDIT_SPREAD:
            print(f"🛑 Safe Entry Filter: 1H Trend Anchor is strongly Bullish (rose ${trend_movement_4h:.2f} in 4H). Blocking Bear Spread.")
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
                print("🛑 ALARM: Funding Rate extremely positive. Too much long leverage in the market. Blocking Bull Put Spread...")
                print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
                time.sleep(POLL_INTERVAL_SEC)
                continue
            elif funding_rate < -0.0005 and suggested_strategy == config.StrategyType.BEAR_CREDIT_SPREAD:
                print("🛑 ALARM: Funding Rate extremely negative. Too much short leverage in the market. Blocking Bear Call Spread...")
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
            print(f"❌ Error building strategy: {e}")
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        if not order_specs:
            print("⚠️ No valid strikes found passing Greek & Slippage Guard criteria.")
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
        # Average Delta contract fee + slippage buffer ≈ $15 (assuming normal lot sizes)
        if net_credit < 15.0:
            print(f"🛑 ALARM: Fee-Aware Exit Triggered. Net Credit ${net_credit:.2f} is < $15. Trade rejected to avoid 'working for the exchange'.")
            print(f"💤 Waiting {POLL_INTERVAL_SEC // 60}m before next check...")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        
        # Dump raw JSON at the end for OpenClaw to parse programmatically if needed
        api_payload = []
        for leg in order_specs:
            api_payload.append({
                "product_id": leg.product_id,
                "side": leg.side,
                "size": leg.size,
                "order_type": leg.order_type,
                "limit_price": leg.limit_price
            })

        print("\n--- OPENCLAW JSON PAYLOAD ---")
        payload_str = json.dumps({
            "strategy": strategy_type.value,
            "underlying": spot_price,
            "net_credit": net_credit,
            "orders": api_payload
        }, indent=2)
        print(payload_str)
        print("-----------------------------")
        
        logger.info(f"Generated Payload: {payload_str}")
        
        # Save Trade Decision Context for Post-Trade Logger to evaluate EOD
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
            "recommended_orders": api_payload
        }
        with open("daily_trade_context.json", "w") as f:
            json.dump(trade_context, f, indent=4)
        logger.info("Saved 'daily_trade_context.json' for EOD verification.")
        
        
        # Exit successfully to OpenClaw execution after rendering a valid payload
        sys.exit(0)

if __name__ == "__main__":
    main()
