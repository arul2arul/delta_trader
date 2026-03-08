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

logging.basicConfig(level=logging.WARNING, format='%(levelname)s - %(message)s')
logger = logging.getLogger("analyze_0dte")

def main():
    print("🧠 Initiating 0 DTE Brain Analysis (Polling Mode)...\n")
    exchange = ExchangeClient()
    market_data = MarketData(exchange)

    POLL_INTERVAL_SEC = 5 * 60  # 5 minutes
    CUTOFF_HOUR = 14            # 2:00 PM IST (14:00)

    while True:
        now_ist = datetime.now(pytz.timezone(config.TIMEZONE))
        if now_ist.hour >= CUTOFF_HOUR:
            print(f"\n🕘 Cutoff time reached ({CUTOFF_HOUR}:00 IST). No valid setups found today. Shutting down.")
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
        print(f"\n⚙️  Building orders for: {suggested_strategy.value.replace('_', ' ').title()}")
        try:
            strategy_type, order_specs = build_strategy(
                regime=regime,
                chain=chain,
                spot_price=spot_price,
                wide_wings=wide_wings,
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
                "order_type": "limit_order",
                "limit_price": leg.limit_price
            })

        print("\n--- OPENCLAW JSON PAYLOAD ---")
        print(json.dumps({
            "strategy": strategy_type.value,
            "underlying": spot_price,
            "net_credit": net_credit,
            "orders": api_payload
        }, indent=2))
        print("-----------------------------")
        
        # Exit successfully to OpenClaw execution after rendering a valid payload
        sys.exit(0)

if __name__ == "__main__":
    main()
