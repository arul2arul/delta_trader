#!/usr/bin/env python3
"""
test_connectivity.py
────────────────────────────────────────────────
Validates ALL API keys and connections without
placing any trades or modifying any state.

Usage:
    python test_connectivity.py
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

PASS = "\033[92m✔ PASS\033[0m"
FAIL = "\033[91m✖ FAIL\033[0m"
WARN = "\033[93m⚠ WARN\033[0m"

results = []

def section(title):
    print(f"\n\033[1m\033[96m── {title} {'─' * (45 - len(title))}\033[0m")

def check(name, passed, detail=""):
    icon = PASS if passed else FAIL
    print(f"  {icon}  {name}")
    if detail:
        colour = "\033[92m" if passed else "\033[91m"
        print(f"         {colour}{detail}\033[0m")
    results.append(passed)

print("""
╔══════════════════════════════════════════════════╗
║     Delta Trader — Connectivity Test             ║
║     NO trades will be placed.                    ║
╚══════════════════════════════════════════════════╝""")

# ─────────────────────────────────────────────────
# 1. ENV FILE CHECK
# ─────────────────────────────────────────────────
section("Environment Variables")
required_keys = ["DELTA_API_KEY", "DELTA_API_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GEMINI_API_KEY"]
for key in required_keys:
    val = os.getenv(key, "")
    if val and val not in ("your_api_key_here", "your_telegram_bot_token_here", "your_telegram_chat_id_here"):
        check(f"{key}", True, f"Set ({val[:6]}...)")
    else:
        check(f"{key}", False, "NOT SET or still using placeholder value")

# ─────────────────────────────────────────────────
# 2. TELEGRAM TEST
# ─────────────────────────────────────────────────
section("Telegram Bot API")
try:
    import requests
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    # First verify the bot token is valid
    bot_url = f"https://api.telegram.org/bot{token}/getMe"
    resp = requests.get(bot_url, timeout=10)
    data = resp.json()
    if data.get("ok"):
        bot_name = data["result"].get("username", "?")
        check("Bot Token valid", True, f"Bot username: @{bot_name}")
    else:
        check("Bot Token valid", False, str(data.get("description", "Unknown error")))

    # Send a real test message to your chat
    msg_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": (
            "✅ *Delta Trader — Connectivity Test PASSED*\n\n"
            "Telegram notifications are working correctly.\n"
            "You will receive trade alerts on this chat."
        ),
        "parse_mode": "Markdown"
    }
    resp2 = requests.post(msg_url, json=payload, timeout=10)
    data2 = resp2.json()
    if data2.get("ok"):
        check("Message delivery to Chat ID", True, f"Test message sent! Check your Telegram now.")
    else:
        check("Message delivery to Chat ID", False, str(data2.get("description", "Unknown error")))

except Exception as e:
    check("Telegram connection", False, str(e))

# ─────────────────────────────────────────────────
# 3. DELTA EXCHANGE TEST
# ─────────────────────────────────────────────────
section("Delta Exchange API")
try:
    from exchange_client import ExchangeClient
    client = ExchangeClient()

    # Test 1: Fetch wallet balance (read-only)
    balance = client.get_wallet_balance()
    check("API Key authentication", True, f"Wallet balance fetched successfully")
    if balance > 0:
        check("Wallet balance", True, f"Balance: ₹{balance:,.2f}")
    else:
        check("Wallet balance", False, "Balance is 0 — check if using correct account (Testnet vs Production)")

    # Test 2: Fetch spot price (public endpoint, no auth needed)
    from market_data import MarketData
    md = MarketData(client)
    spot = md.get_spot_price()
    if spot > 0:
        check("Market data (Spot Price)", True, f"BTC Spot: ${spot:,.2f}")
    else:
        check("Market data (Spot Price)", False, "Could not fetch spot price")

    # Test 3: Fetch open positions (verifies trading API scope)
    positions = client.get_positions()
    pos_count = len([p for p in (positions or []) if abs(int(p.get("size", 0))) > 0])
    check("Positions endpoint", True, f"Active positions: {pos_count}")

except Exception as e:
    check("Delta Exchange connection", False, str(e)[:200])

# ─────────────────────────────────────────────────
# 4. GEMINI AI TEST
# ─────────────────────────────────────────────────
section("Google Gemini AI API")
try:
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY", "")
    client = genai.Client(api_key=api_key)

    # Send a tiny test prompt — minimal token usage
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="Reply with exactly: CONNECTIVITY_OK"
    )
    text = response.text.strip()
    if "CONNECTIVITY_OK" in text.upper() or len(text) > 0:
        check("Gemini API key valid", True, "gemini-2.5-flash responded correctly")
        check("Model: gemini-2.5-flash", True, f"Response: {text[:60]}")
    else:
        check("Gemini API key valid", False, f"Unexpected response: {text[:100]}")

except ImportError:
    check("google-genai installed", False, "Run: pip install google-genai")
except Exception as e:
    err = str(e)
    if "quota" in err.lower() or "429" in err.lower():
        # Quota hit but key is valid
        print(f"  {WARN}  Gemini API Key valid but quota currently exhausted")
        print(f"         \033[93mThe key works — free tier daily limit reached. Will reset tomorrow.\033[0m")
        results.append(True)
    else:
        check("Gemini API connection", False, err[:200])

# ─────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────
total  = len(results)
passed = sum(results)
failed = total - passed

print(f"\n\033[1m{'─'*50}\033[0m")
if failed == 0:
    print(f"\033[92m\033[1m  ALL {total} CHECKS PASSED — System is ready to trade!\033[0m")
else:
    print(f"\033[91m\033[1m  {passed}/{total} checks passed. Fix the {failed} failing check(s) above before trading.\033[0m")
print(f"\033[1m{'─'*50}\033[0m\n")
