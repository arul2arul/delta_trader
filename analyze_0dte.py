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

import config
from exchange_client import ExchangeClient
from market_data import MarketData
from indicators import compute_all
from regime_detector import detect_regime, check_volatility, get_strategy_for_regime
from strategy_engine import build_strategy

logging.basicConfig(level=logging.WARNING, format='%(levelname)s - %(message)s')
logger = logging.getLogger("analyze_0dte")

def main():
    print("🧠 Initiating 0 DTE Brain Analysis...\n")
    exchange = ExchangeClient()
    market_data = MarketData(exchange)

    # 1. Fetch Spot
    spot_price = market_data.get_spot_price()
    print(f"💰 Spot Price: ${spot_price:,.2f}")

    # 2. Fetch Candles & Indicators
    df = market_data.get_hourly_candles()
    if df.empty:
        print("❌ Error: No candle data available.")
        sys.exit(1)

    df = compute_all(df)
    
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
        sys.exit(1)
        
    iv_rank = market_data.get_iv_rank(chain)
    wide_wings = check_volatility(iv_rank)
    print(f"⚡ IV Rank: {iv_rank:.1f}% (Wide Wings: {wide_wings})")
    
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
        sys.exit(1)

    if not order_specs:
        print("⚠️ No valid strikes found passing Greek & Slippage Guard criteria. Aborting trade recommendations.")
        sys.exit(0)

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

if __name__ == "__main__":
    main()
