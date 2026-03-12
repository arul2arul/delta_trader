"""
Check Strategy – Diagnostic script to analyze market and propose trades.
Run: python check_strategy.py
"""

import logging
import sys
import io
import time
from datetime import datetime
import pytz

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import config
from exchange_client import ExchangeClient
from market_data import MarketData
from indicators import compute_all
from regime_detector import detect_regime
# Import functional strategy builder
from strategy_engine import build_strategy

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("check_strategy")

def main():
    print("\n" + "="*60)
    print("  🔎 ANALYZING CURRENT MARKET STRATEGY")
    print("="*60 + "\n")

    # 1. Initialize
    client = ExchangeClient()
    if not client.check_connectivity():
        logger.error("❌ Could not connect to exchange. Check .env")
        # Continue anyway for dry run if connectivity check fails but we want to try? 
        # No, MarketData needs client.
        return

    md = MarketData(client)
    
    # 2. Fetch Data & Indicators
    print("1. Fetching Market Data...")
    spot = md.get_spot_price()
    print(f"   BTC Spot Price: ${spot:,.2f}")

    df = md.get_candles(resolution="1h")
    if df.empty:
        logger.error("❌ No candle data available.")
        return

    df = compute_all(df)
    last = df.iloc[-1]
    
    rsi = last.get("rsi", 0)
    ema = last.get(f"ema_{config.EMA_PERIOD}", 0)
    adx = last.get("adx", 0)
    
    print(f"   RSI: {rsi:.2f}")
    print(f"   EMA: {ema:.2f}")
    print(f"   ADX: {adx:.2f}")

    # 3. Detect Regime
    regime = detect_regime(df)
    print("\n2. Market Regime")
    print(f"   Detected Regime: {regime.value.upper()}")

    # 4. Fetch Option Chain & IV
    print("\n3. Option Chain Analysis")
    chain = md.get_option_chain() # Gets daily expiry by default
    if not chain:
        logger.error("❌ No daily option chain found.")
        return
        
    iv_rank = md.get_iv_rank(chain)
    print(f"   IV Rank: {iv_rank:.1f}%")
    
    expiry = chain[0]["expiry"]
    print(f"   Target Expiry: {expiry}")
    
    # 5. Build Strategy
    print("\n4. Strategy Generation (Simulation)")
    wide_wings = iv_rank > config.IV_RANK_THRESHOLD
    
    # Use the functional build_strategy directly
    strategy_type, orders = build_strategy(
        regime=regime,
        chain=chain,
        wide_wings=wide_wings,
        lot_size=1
    )
    
    print(f"   Proposed Strategy: {strategy_type.value.replace('_', ' ').title()}")
    print(f"   Total Orders: {len(orders)}")
    
    if not orders:
        logger.error("❌ Could not generate valid orders (missing strikes?).")
        return

    print("\n5. Proposed Orders:")
    for i, order in enumerate(orders, 1):
        side = order.side.upper()
        size = order.size
        role = order.role
        strike = order.strike_price
        price = order.limit_price
        
        print(f"   Order #{i}: {side:<4} {size}x  K={strike:<7.0f} @ ${price:<8.2f} ({role})")

    # Calculate theoretical PnL metrics
    premium_collected = sum(o.limit_price for o in orders if o.side == "sell")
    premium_paid = sum(o.limit_price for o in orders if o.side == "buy")
    net_credit = premium_collected - premium_paid
    
    print(f"\n   Gross Premium: ${premium_collected:.2f}")
    print(f"   Wing Cost:     ${premium_paid:.2f}")
    print(f"   NET CREDIT:    ${net_credit:.2f} (approx per lot)")

    print("\n" + "="*60)
    print("  ✅ ANALYSIS COMPLETE")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
