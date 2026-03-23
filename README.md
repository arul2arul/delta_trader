# Operation Daily Profit: 0-DTE Delta Options Trader

A conservative, quantitative 0-DTE (Zero Days to Expiration) BTC options trading bot for **Delta Exchange India**. Deploys Iron Condors or Credit Spreads daily, monitored in real-time, with an AI circuit breaker powered by Gemini.

---

## Quick Start (Windows Laptop Setup)

### Step 1 — Prerequisites

- Python 3.11 or 3.12 installed and added to PATH
- Git installed
- A Delta Exchange India account with API access enabled
- A Telegram bot (create via [@BotFather](https://t.me/BotFather)) and your Chat ID
- A Google Gemini API key (free tier works: [aistudio.google.com](https://aistudio.google.com))

### Step 2 — Clone and Install

```bat
git clone https://github.com/arul2arul/delta_trader.git
cd delta_trader
pip install -r requirements.txt
```

### Step 3 — Configure API Keys

Run the interactive setup wizard. It will ask for each key and write a `.env` file:

```bat
python setup.py
```

You will be prompted for:
| Key | Where to find it |
|---|---|
| `DELTA_API_KEY` | Delta Exchange India → Account → API Keys |
| `DELTA_API_SECRET` | Same page (shown only once at creation) |
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_ID` | Send any message to your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) → Get API Key |

> **Testnet vs Production:** The setup wizard also asks if you want to use the testnet. Start with testnet (`USE_TESTNET=true`) to verify everything works before going live.

### Step 4 — Verify Everything Works

Run the health check script. This validates all API keys and connectivity **without placing any trade**:

```bat
python test_connectivity.py
```

Expected output if everything is correctly configured:
```
── Environment Variables ─────────────────────────────
  ✔ PASS  DELTA_API_KEY       Set (abc123...)
  ✔ PASS  DELTA_API_SECRET    Set (xyz789...)
  ✔ PASS  TELEGRAM_BOT_TOKEN  Set (110234...)
  ✔ PASS  TELEGRAM_CHAT_ID    Set (987654...)
  ✔ PASS  GEMINI_API_KEY      Set (AIzaSy...)

── Telegram Bot API ──────────────────────────────────
  ✔ PASS  Bot Token valid              Bot username: @YourBot
  ✔ PASS  Message delivery to Chat ID  Test message sent!

── Delta Exchange API ────────────────────────────────
  ✔ PASS  API Key authentication       Wallet balance fetched successfully
  ✔ PASS  Wallet balance               Balance: ₹90,000.00
  ✔ PASS  Market data (Spot Price)     BTC Spot: $84,500.00
  ✔ PASS  Positions endpoint           Active positions: 0

── Google Gemini AI API ──────────────────────────────
  ✔ PASS  Gemini API key valid         gemini-2.5-flash responded correctly

──────────────────────────────────────────────────────
  ALL 13 CHECKS PASSED — System is ready to trade!
──────────────────────────────────────────────────────
```

You will also receive a Telegram message confirming delivery.

**What the health check validates:**

| Check | What it confirms |
|---|---|
| `.env` keys present | All 5 keys are set and not left as placeholder values |
| Telegram token | Bot token is accepted by the Telegram API |
| Telegram message delivery | Sends a real test message — you will see it arrive on your phone |
| Delta Exchange connectivity | REST API endpoint is reachable |
| Delta API authentication | Wallet balance fetched successfully (proves key + secret are correct) |
| Wallet balance | Balance is > 0 and you are connected to the right environment (testnet vs production) |
| Market data | Spot price endpoint responds (public, no auth required) |
| Positions endpoint | Trading-scope API permission is granted on your key |
| Gemini AI key | Sends a tiny test prompt to gemini-2.5-flash and confirms a valid response — uses negligible tokens, no meaningful cost |

### Step 5 — Automate with Windows Task Scheduler

The `run_bot.bat` script automatically runs the health check first, then starts the trading bot only if all checks pass.

1. Open **Task Scheduler** → `Create Basic Task`
2. Set the trigger to **Daily at 11:00 AM**
3. Set the action to: **Start a program**
   - Program: `C:\path\to\delta_trader\run_bot.bat`
4. Check **"Run whether user is logged on or not"** and **"Run with highest privileges"**
5. Under **Conditions**, uncheck "Start only if the computer is on AC power" if on a laptop

That's it. Every day at 11:00 AM:
- `run_bot.bat` runs the health check
- If all checks pass, `analyze_0dte.py` starts automatically
- You get a Telegram message confirming the bot is live
- All output is logged to `automation_log.txt` in the project folder

---

## Daily Workflow (Once Running)

| Time (IST) | What happens |
|---|---|
| 11:00 AM | Laptop starts, Task Scheduler launches `run_bot.bat` |
| 11:00–11:45 AM | Health check runs, bot polls market data, pre-entry filters evaluated |
| ~12:00 PM | Trade deployed if all filters + AI approval pass |
| 12:00 PM–3:30 PM | Bot monitors position in real-time, risk manager active |
| On target hit | Telegram alert: "PAYDAY — position closed at profit" |
| On stop hit | Telegram alert: "KILL — daily loss limit reached" |
| 5:00 PM Friday | Trading halted for weekend |
| 9:00 AM Monday | Trading resumes automatically |

> **No manual action needed during the day.** The bot handles entry, monitoring, and exit autonomously.

---

## How It Works

### Execution Flow

1. **State check** — abort if an open position already exists (prevents double-entry)
2. **Data aggregation** — spot price + 1H and 15m candles via `market_data.py`
3. **Indicator computation** — RSI, EMA, ADX, ATR, VWAP, Supertrend via `indicators.py`
4. **Regime classification** — both timeframes must agree: SIDEWAYS / BULLISH / BEARISH
5. **Pre-entry filters** — ATR spike, 60m consolidation, trend anchor ban, Supertrend direction, funding rate acceleration, fee-aware credit floor
6. **Strategy construction** — Iron Condor (SIDEWAYS) or Credit Spread (directional)
7. **Pre-flight validation** — capital sufficiency, clock sync (<2s drift), L2 slippage
8. **AI second opinion** — Gemini 2.5-Flash scores the setup 1–10; score ≤ 5 aborts the trade
9. **Order execution** — batch limit orders + bracket SL/TP submitted atomically
10. **Monitoring loop** — real-time PnL via WebSocket; risk manager evaluates KILL / PAYDAY / HOLD each cycle

### Strategies

| Market Regime | Strategy | Structure |
|---|---|---|
| SIDEWAYS | Iron Condor | SELL 0.10Δ call + SELL 0.10Δ put + BUY 0.05Δ wings |
| BULLISH | Bull Put Spread | SELL 0.15Δ put + BUY 0.05Δ put (lower) |
| BEARISH | Bear Call Spread | SELL 0.15Δ call + BUY 0.05Δ call (higher) |

### Risk Controls

| Control | Setting |
|---|---|
| Daily loss kill-switch | -₹4,500 (5% of ₹90,000 capital) |
| Per-leg stop | 2.5× entry premium collected |
| Profit target | ₹500 (scales to ₹2,000 over winning days) |
| IV expansion guard | Close position if short-leg mark price rises 30%+ |
| Gamma risk guard | Block entry if aggregate gamma risk exceeds net credit |
| Open Interest floor | Skip strikes with OI < 10 (illiquid exit risk) |
| Consecutive loss circuit breaker | Suspend trading after 2 back-to-back losing days |
| AI kill-switch | Gemini score ≤ 5 aborts the trade regardless of math |

---

## Pre-Entry Filters

All filters must pass before a strategy is constructed. Any single failure aborts the trade cycle for that poll.

| Filter | Threshold | What it prevents |
|---|---|---|
| ATR spike guard | Blocks entry if 1H ATR exceeds recent average by a significant ratio | Entering during high-volatility, erratic price action |
| 60m consolidation check | Requires price to be in a defined range for the last 60 minutes | Entering mid-trend or after a large candle burst |
| Trend anchor ban | Blocks Iron Condor if a strong directional trend is anchored | Selling a range structure into a trending market |
| Supertrend direction | Must align with the chosen strategy direction | Prevents selling into the wrong side of momentum |
| Funding rate acceleration | Change between polls must be < 0.0003 | Avoids entry when perpetual funding is accelerating (momentum signal) |
| Fee-aware credit floor | Net credit must be > ₹3.0 after estimated fees | Ensures the trade has positive expected value after costs |
| IV entry floor | IV Rank must be ≥ 30% for Iron Condor | Prevents selling cheap premium that can spike against you |
| Regime consensus | Both 1H and 15m timeframes must agree on SIDEWAYS/BULLISH/BEARISH | Avoids conflicting signals across timeframes |
| Gamma risk guard | Aggregate short-leg gamma risk must not exceed net credit collected | Controls pin risk near expiry |
| Open Interest floor | Each strike leg must have OI ≥ 10 | Avoids illiquid strikes where exit may be impossible |
| Pre-flight capital check | Available balance must cover estimated margin for chosen lot size | Prevents partial fills or margin calls |
| Clock sync | System clock must be within 2 seconds of exchange time | Avoids order rejections due to timestamp drift |
| L2 slippage check | Live order book spread must be within acceptable bounds | Ensures limit orders can fill near theoretical price |

---

## Max Loss: Is It Capped?

**Yes — both strategies have a mathematically defined maximum loss.**

### Iron Condor
Structure: SELL 0.10Δ call + BUY 0.05Δ call (above) + SELL 0.10Δ put + BUY 0.05Δ put (below)

```
Max Loss = max(call spread width, put spread width) − net credit collected
```

The long wings (bought at 0.05Δ) cap the loss on each side. If BTC gaps through one spread entirely, the loss is limited to the spread width minus the credit received for that side.

### Credit Spread (Bull Put / Bear Call)
Structure: SELL 0.15Δ leg + BUY 0.05Δ wing

```
Max Loss = spread width − net credit collected
```

The bought wing fully caps downside. This is defined risk — there is no unlimited loss scenario.

### Loss in INR Terms

Each lot = 0.001 BTC. So for N lots:

```
Max Loss (INR) ≈ (spread width in USD − net credit in USD) × 0.001 × N lots × USD/INR rate
```

### Daily Loss Cap

The kill-switch at **−₹4,500** (5% of ₹90,000 capital) will close the position before theoretical max loss is reached if the market moves against the trade intraday. In practice, the kill-switch fires before the spread fully expires worthless against you.

---

## Lot Sizing

Lot size is calculated dynamically each trade using a **Safety-First** model. The system computes three independent lot caps and picks the smallest:

| Constraint | Formula | Purpose |
|---|---|---|
| **Margin Cap** | `(usable_balance × 0.80 − fee_reserve) ÷ margin_per_lot` | Never exceed what the broker will allow |
| **Target Cap** | `profit_target_INR ÷ (net_premium_per_BTC × 0.001 × USD_INR)` | Size only for what you need to hit the daily target |
| **Hard Safety Cap** | `1,000 lots` | Absolute upper bound regardless of balance |

The final lot size = `min(Margin Cap, Target Cap, Hard Safety Cap)`, floored at 1.

**Key parameters:**
- Margin buffer: 20% of balance kept untouched for wicks
- Fee reserve: ₹1,000 held back for transaction costs
- Margin per lot: estimated at `spot_price × 0.001 × 2%`
- Profit target: starts at ₹500, scales to ₹2,000 over consecutive winning days

This means the bot naturally trades fewer lots early on (small profit target → fewer lots needed) and scales up only after a winning streak is established.

---

## Useful Scripts

| Script | Purpose |
|---|---|
| `python test_connectivity.py` | Validate all API keys and connections |
| `python analyze_0dte.py --dry-run` | Full simulation — no real orders placed |
| `python bot_status.py` | Live dashboard showing current position and PnL |
| `python check_strategy.py` | Check today's market regime and proposed strategy |
| `python cancel_all.py` | Emergency: cancel all open orders immediately |
| `python check_pos.py` | Inspect live positions |
| `python test_all.py` | Run the full unit test suite (no API keys needed) |

---

## Configuration

All tunable parameters are in `config.py`:

- **Capital:** `TOTAL_CAPITAL_INR = 90_000`
- **Trading days:** Monday–Friday (`TRADING_DAYS = [0,1,2,3,4]`)
- **Deploy window:** 10:00–10:02 AM IST (use `analyze_0dte.py` directly for 11 AM+ starts)
- **Polling interval:** 120s Iron Condor, 60s Credit Spread

To switch from testnet to production, set `USE_TESTNET=false` in your `.env` file.
