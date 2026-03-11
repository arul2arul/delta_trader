import re

with open("analyze_0dte.py", "r") as f:
    content = f.read()

# 1. Add log_rejection function
helper_code = '''logger.setLevel(logging.INFO)

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
        f.write(json.dumps(summary) + "\\n")
    with open("latest_status.json", "w") as f:
        json.dump(summary, f, indent=4)
'''
content = content.replace("logger.setLevel(logging.INFO)", helper_code)

# 2. Add variables to while True loop
content = content.replace(
    "while True:\n        now_ist = datetime.now(pytz.timezone(config.TIMEZONE))",
    "while True:\n        now_ist = datetime.now(pytz.timezone(config.TIMEZONE))\n        current_spot = 0.0\n        current_regime = \"UNKNOWN\""
)

# 3. Update variables
content = content.replace(
    "spot_price = market_data.get_spot_price()",
    "spot_price = market_data.get_spot_price()\n        current_spot = spot_price"
)
content = content.replace(
    "regime = detect_regime(df)",
    "regime = detect_regime(df)\n        current_regime = regime.value"
)

# 4. Replace specific rejections
reps = [
    # Cutoff
    (
        '''print(f"\\n🕘 Criteria Not Met - Skipping Day. Cutoff time reached ({CUTOFF_HOUR}:{CUTOFF_MINUTE} IST). Shutting down.")\n            sys.exit(0)''',
        '''reason = f"Cutoff time reached ({CUTOFF_HOUR}:{CUTOFF_MINUTE} IST). Skipping Day."\n            print(f"\\n🕘 Criteria Not Met - {reason} Shutting down.")\n            log_rejection(reason, current_spot, current_regime)\n            sys.exit(0)'''
    ),
    # No candle data
    (
        '''print("❌ Error: No candle data available.")''',
        '''reason = "No candle data available."\n            print(f"❌ Error: {reason}")\n            log_rejection(reason, current_spot, current_regime)'''
    ),
    # No option chain
    (
        '''print("❌ Error: No option chain available for 0 DTE contracts.")''',
        '''reason = "No option chain available for 0 DTE contracts."\n            print(f"❌ Error: {reason}")\n            log_rejection(reason, current_spot, current_regime)'''
    ),
    # Supertrend
    (
        '''print("🛑 ALARM: Red SuperTrend Ban triggered on 15m timeframe! Blocking Bull Put Spread...")\n            print("⚠️ Suggesting NO TRADE instead to protect capital.")''',
        '''reason = "Red SuperTrend Ban triggered on 15m timeframe! Blocking Bull Put Spread."\n            print(f"🛑 ALARM: {reason}")\n            print("⚠️ Suggesting NO TRADE instead to protect capital.")\n            log_rejection(reason, current_spot, current_regime)'''
    ),
    # ATR filter
    (
        '''print(f"🛑 Safe Entry Filter: ATR ({current_atr:.2f}) is > 20% higher than 3-day average ({avg_atr_3d:.2f}). Skipping.")''',
        '''reason = f"ATR ({current_atr:.2f}) is > 20% higher than 3-day average ({avg_atr_3d:.2f})."\n            print(f"🛑 Safe Entry Filter: {reason} Skipping.")\n            log_rejection(reason, current_spot, current_regime)'''
    ),
    # Consolidation
    (
        '''print(f"🛑 Safe Entry Filter: 60m Consolidation range is ${range_60m:.2f} (>= $400). Skipping.")''',
        '''reason = f"60m Consolidation range is ${range_60m:.2f} (>= $400)."\n            print(f"🛑 Safe Entry Filter: {reason} Skipping.")\n            log_rejection(reason, current_spot, current_regime)'''
    ),
    # Trend 4H Bull block
    (
        '''print(f"🛑 Safe Entry Filter: 1H Trend Anchor is strongly Bearish (dropped ${abs(trend_movement_4h):.2f} in 4H). Blocking Bull Spread.")''',
        '''reason = f"1H Trend Anchor is strongly Bearish (dropped ${abs(trend_movement_4h):.2f} in 4H). Blocking Bull Spread."\n            print(f"🛑 Safe Entry Filter: {reason}")\n            log_rejection(reason, current_spot, current_regime)'''
    ),
    # Trend 4H Bear block
    (
        '''print(f"🛑 Safe Entry Filter: 1H Trend Anchor is strongly Bullish (rose ${trend_movement_4h:.2f} in 4H). Blocking Bear Spread.")''',
        '''reason = f"1H Trend Anchor is strongly Bullish (rose ${trend_movement_4h:.2f} in 4H). Blocking Bear Spread."\n            print(f"🛑 Safe Entry Filter: {reason}")\n            log_rejection(reason, current_spot, current_regime)'''
    ),
    # Funding Rate Bull
    (
        '''print("🛑 ALARM: Funding Rate extremely positive. Too much long leverage in the market. Blocking Bull Put Spread...")''',
        '''reason = "Funding Rate extremely positive. Too much long leverage in the market. Blocking Bull Put Spread."\n                print(f"🛑 ALARM: {reason}")\n                log_rejection(reason, current_spot, current_regime)'''
    ),
    # Funding Rate Bear
    (
        '''print("🛑 ALARM: Funding Rate extremely negative. Too much short leverage in the market. Blocking Bear Call Spread...")''',
        '''reason = "Funding Rate extremely negative. Too much short leverage in the market. Blocking Bear Call Spread."\n                print(f"🛑 ALARM: {reason}")\n                log_rejection(reason, current_spot, current_regime)'''
    ),
    # Strategy Build Error
    (
        '''print(f"❌ Error building strategy: {e}")''',
        '''reason = f"Error building strategy: {e}"\n            print(f"❌ {reason}")\n            log_rejection(reason, current_spot, current_regime)'''
    ),
    # No Valid Strikes
    (
        '''print("⚠️ No valid strikes found passing Greek & Slippage Guard criteria.")''',
        '''reason = "No valid strikes found passing Greek & Slippage Guard criteria."\n            print(f"⚠️ {reason}")\n            log_rejection(reason, current_spot, current_regime)'''
    ),
    # Fee Aware Limit
    (
        '''print(f"🛑 ALARM: Fee-Aware Exit Triggered. Net Credit ${net_credit:.2f} is < $15. Trade rejected to avoid 'working for the exchange'.")''',
        '''reason = f"Fee-Aware Exit Triggered. Net Credit ${net_credit:.2f} is < $15. Trade rejected to avoid 'working for the exchange'."\n            print(f"🛑 ALARM: {reason}")\n            log_rejection(reason, current_spot, current_regime)'''
    ),
    # AI Score < 5
    (
        '''print(f"\\n🛑 ALARM: AI Validation Failed (Confidence {confidence}/10). The mathematical setup looks poor to the AI. Trade rejected.")''',
        '''reason = f"AI Validation Failed (Confidence {confidence}/10). The mathematical setup looks poor to the AI."\n                print(f"\\n🛑 ALARM: {reason} Trade rejected.")\n                log_rejection(reason, current_spot, current_regime)'''
    ),
]

for orig, new in reps:
    if orig not in content:
        print(f"Warning: Could not find snippet:\\n{orig}")
    content = content.replace(orig, new)

with open("analyze_0dte.py", "w") as f:
    f.write(content)

print("Patch applied successfully.")
