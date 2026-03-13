#!/usr/bin/env python3
"""
bot_status.py
─────────────────────────────────────────────
READ-ONLY status dashboard for the Delta Trader bot.
This script makes ZERO API calls and performs ZERO trades.
Safe to run at any time, as many times as you want.

Usage:
    python bot_status.py
"""

import json
import os
import sys
from datetime import datetime

# ── Colour helpers (works on Windows CMD and PowerShell with VT enabled) ──
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
DIM    = "\033[2m"

def hdr(text):   print(f"\n{BOLD}{CYAN}{'─'*55}{RESET}")  ; print(f"{BOLD}{CYAN}  {text}{RESET}") ; print(f"{BOLD}{CYAN}{'─'*55}{RESET}")
def ok(text):    print(f"  {GREEN}✔  {text}{RESET}")
def warn(text):  print(f"  {YELLOW}⚠  {text}{RESET}")
def err(text):   print(f"  {RED}✖  {text}{RESET}")
def info(text):  print(f"  {DIM}{text}{RESET}")

# ─────────────────────────────────────────────────────────────
# 1. CURRENT DATE / TIME
# ─────────────────────────────────────────────────────────────
hdr("SYSTEM TIME")
now = datetime.now()
print(f"  Local time : {BOLD}{now.strftime('%Y-%m-%d  %H:%M:%S')}{RESET}")
try:
    import pytz
    ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    print(f"  IST        : {BOLD}{ist.strftime('%Y-%m-%d  %H:%M:%S IST')}{RESET}")
except Exception:
    warn("pytz not available – IST time skipped")

# ─────────────────────────────────────────────────────────────
# 2. TRADE LOCK FILE
# ─────────────────────────────────────────────────────────────
LOCK_FILE = ".trade_lock"
hdr("TRADE LOCK STATUS")
if os.path.exists(LOCK_FILE):
    try:
        with open(LOCK_FILE) as f:
            lock = json.load(f)
        err(f"LOCKED  —  set at {lock.get('locked_at', 'unknown')}")
        warn(f"Strategy : {lock.get('strategy')}")
        warn(f"Spot     : ${lock.get('spot_price')}")
        warn("A previous execution attempt may have partially placed orders.")
        warn(f"Manually check Delta Exchange, then: del .trade_lock  (Windows)  |  rm .trade_lock  (Mac/Linux)")
    except Exception as e:
        err(f"Lock file exists but could not be read: {e}")
else:
    ok("No lock file — bot is FREE to execute a new trade today")

# ─────────────────────────────────────────────────────────────
# 3. LATEST REJECTION / STATUS
# ─────────────────────────────────────────────────────────────
hdr("LAST CYCLE STATUS")
STATUS_FILE = "latest_status.json"
if os.path.exists(STATUS_FILE):
    try:
        with open(STATUS_FILE) as f:
            st = json.load(f)
        status   = st.get("status", "UNKNOWN")
        ts       = st.get("timestamp", "?")
        spot     = st.get("spot_price", 0)
        regime   = st.get("regime", "?")
        reason   = st.get("reason", "?")

        col = GREEN if status == "TRADE_EXECUTED" else (RED if "ERROR" in status else YELLOW)
        print(f"  Status    : {col}{BOLD}{status}{RESET}")
        print(f"  Time      : {ts}")
        print(f"  Spot      : ${spot:,.2f}")
        print(f"  Regime    : {regime.upper()}")
        print(f"  Reason    : {reason}")
    except Exception as e:
        warn(f"Could not read {STATUS_FILE}: {e}")
else:
    warn("latest_status.json not found — bot has not run today yet")

# ─────────────────────────────────────────────────────────────
# 4. TODAY'S REJECTION HISTORY
# ─────────────────────────────────────────────────────────────
hdr("TODAY'S REJECTION LOG")
REJECTION_FILE = "rejection_log.jsonl"
today_str = datetime.now().strftime("%Y-%m-%d")
if os.path.exists(REJECTION_FILE):
    with open(REJECTION_FILE) as f:
        lines = f.readlines()
    today_entries = []
    for line in lines:
        try:
            entry = json.loads(line.strip())
            if entry.get("timestamp", "").startswith(today_str):
                today_entries.append(entry)
        except Exception:
            continue

    if today_entries:
        print(f"  {len(today_entries)} rejection(s) logged today:\n")
        for entry in today_entries:
            ts     = entry.get("timestamp", "")
            reason = entry.get("reason", "")
            spot   = entry.get("spot_price", 0)
            # Colour based on type of rejection
            if "FATAL" in reason or "ERROR" in reason:
                colour = RED
            elif "Fee-Aware" in reason or "Margin" in reason:
                colour = YELLOW
            else:
                colour = DIM
            print(f"  {DIM}{ts}{RESET}  {colour}{reason[:90]}{RESET}")
    else:
        ok(f"No rejections logged for today ({today_str})")
else:
    warn("rejection_log.jsonl not found")

# ─────────────────────────────────────────────────────────────
# 5. LAST 10 LINES OF brain_execution.log
# ─────────────────────────────────────────────────────────────
hdr("BRAIN EXECUTION LOG  (last 10 lines)")
LOG_FILE = "brain_execution.log"
if os.path.exists(LOG_FILE):
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    last_lines = all_lines[-10:]
    for line in last_lines:
        line = line.rstrip()
        if "ERROR" in line or "FATAL" in line:
            print(f"  {RED}{line}{RESET}")
        elif "WARNING" in line or "ALARM" in line:
            print(f"  {YELLOW}{line}{RESET}")
        elif "INFO" in line and ("Generated Payload" in line or "Saved" in line):
            print(f"  {GREEN}{line}{RESET}")
        else:
            print(f"  {DIM}{line}{RESET}")
else:
    warn("brain_execution.log not found — bot has not run yet today")

# ─────────────────────────────────────────────────────────────
# 6. DAILY TRADE CONTEXT (if a trade was found today)
# ─────────────────────────────────────────────────────────────
CONTEXT_FILE = "daily_trade_context.json"
if os.path.exists(CONTEXT_FILE):
    try:
        with open(CONTEXT_FILE) as f:
            ctx = json.load(f)
        if ctx.get("date") == today_str:
            hdr("TODAY'S TRADE CONTEXT  (a trade was found!)")
            ok(f"Strategy   : {ctx.get('suggested_strategy', '?').upper()}")
            ok(f"Entry Time : {ctx.get('entry_time', '?')}")
            ok(f"Spot Price : ${ctx.get('spot_price', 0):,.2f}")
            ok(f"Net Credit : ${ctx.get('net_credit_expected', 0):.4f}")
            ok(f"Regime     : {ctx.get('regime', '?').upper()}")
    except Exception:
        pass

print(f"\n{DIM}{'─'*55}")
print(f"  Status check complete. No API calls were made.{RESET}\n")
