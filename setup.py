#!/usr/bin/env python3
"""
setup.py
────────────────────────────────────────────────
One-time setup wizard for Delta Trader.
Run this once on any new machine to configure
your .env file with all required credentials.

Usage:
    python setup.py
"""

import os

ENV_FILE = ".env"

print("""
╔══════════════════════════════════════════════════╗
║       Delta Trader  —  First-Time Setup          ║
╚══════════════════════════════════════════════════╝

This wizard will create your .env file with your
API credentials. The file is stored locally only
and is never uploaded to GitHub.

You will need:
  1. Delta Exchange India API Key & Secret
  2. Telegram Bot Token  (from @BotFather)
  3. Telegram Chat ID    (from /getUpdates)
  4. Google Gemini API Key (from aistudio.google.com)

Press ENTER to skip any field you don't have yet.
""")

def ask(label: str, env_key: str, current_values: dict, secret: bool = False) -> str:
    existing = current_values.get(env_key, "")
    if existing:
        masked = existing[:6] + "..." + existing[-4:] if len(existing) > 10 else "***"
        prompt = f"  {label} [{masked}] → "
    else:
        prompt = f"  {label} → "

    if secret:
        import getpass
        value = getpass.getpass(prompt)
    else:
        value = input(prompt).strip()

    return value if value else existing


# ─── Load existing .env if present ─────────────────────────────────
current = {}
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                current[k.strip()] = v.strip()
    print(f"  ✔ Found existing .env — showing current values. Press ENTER to keep.\n")

# ─── Section 1: Delta Exchange ──────────────────────────────────────
print("── Delta Exchange India ─────────────────────────────")
delta_key    = ask("API Key   ", "DELTA_API_KEY",    current, secret=True)
delta_secret = ask("API Secret", "DELTA_API_SECRET", current, secret=True)
use_testnet  = ask("Testnet? (true/false)", "USE_TESTNET", current) or "false"

# ─── Section 2: Telegram ────────────────────────────────────────────
print("\n── Telegram Notifications ────────────────────────────")
print("  How to get these:")
print("    Token  → Open Telegram → search @BotFather → /newbot")
print("    ChatID → Message your bot, then visit:")
print("             https://api.telegram.org/bot<TOKEN>/getUpdates\n")
tg_token   = ask("Bot Token", "TELEGRAM_BOT_TOKEN", current, secret=True)
tg_chat_id = ask("Chat ID  ", "TELEGRAM_CHAT_ID",   current)

# ─── Section 3: Gemini ──────────────────────────────────────────────
print("\n── Google Gemini API (optional AI validation) ────────")
print("  Get key → https://aistudio.google.com/app/apikey\n")
gemini_key = ask("Gemini API Key", "GEMINI_API_KEY", current, secret=True)

# ─── Write .env ─────────────────────────────────────────────────────
lines = [
    "# Delta Exchange India API Credentials",
    f"DELTA_API_KEY={delta_key}",
    f"DELTA_API_SECRET={delta_secret}",
    f"USE_TESTNET={use_testnet}",
    "",
    "# Telegram Bot Notifications",
    f"TELEGRAM_BOT_TOKEN={tg_token}",
    f"TELEGRAM_CHAT_ID={tg_chat_id}",
    "",
    "# Google Gemini AI Validation (optional)",
    f"GEMINI_API_KEY={gemini_key}",
]

with open(ENV_FILE, "w") as f:
    f.write("\n".join(lines) + "\n")

print(f"""
  ✔ .env file written successfully!

── Quick Test ────────────────────────────────────
  Run the following to verify Telegram works:

    python -c "from notifier import Notifier; Notifier().send_alert('Delta Trader connected!')"

  Then run the status dashboard anytime with:

    python bot_status.py

  When you are ready to start trading:

    python analyze_0dte.py
──────────────────────────────────────────────────
""")
