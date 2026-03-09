# Operation Daily Profit: 0 DTE Delta Options Trader

This repository contains a highly conservative, quantitative 0-DTE (Zero Days to Expiration) crypto options trading bot designed specifically for the **Delta Exchange API**.

The architecture is split into two perfectly isolated components:

1. **The Pure-Math Python Brain:** Analyzes real-time market data, checks heavy mathematical constraints, and outputs a valid JSON trade payload.
2. **The OpenClaw AI Wrapper:** Acts as a deterministic robotic "Secretary" that executes the payload and handles human-in-the-loop Telegram approvals.

---

## 1. How the Python Code Works (The Brain)

The core logic resides in `analyze_0dte.py`. This script runs as a continuous polling daemon between 12:00 PM and 1:45 PM IST.

### The Flow of Execution

1. **State Check:** Before looking at market data, it checks the Delta API. If an open position already exists, it aborts immediately to prevent double-entries.
2. **Data Aggregation:** It downloads the current spot price, 15-minute, and 1-Hour candles.
3. **Indicator Computation:** It calculates RSI, ADX, ATR, VWAP, EMA-9, and Supertrend.
4. **Regime Detection:** It looks at RSI & ADX to label the market as `SIDEWAYS`, `BULLISH`, or `BEARISH`.
5. **Strict Pre-Entry Checks:** It passes the market data through heavily constrained safety checks. If *any* check fails, it safely goes to sleep for 5 minutes and tries again.
6. **AI Second Opinion:** If all mathematical checks pass, it packages the market context and asks the `gemini-2.5-flash` LLM for a qualitative safety check.
7. **JSON Payload Generation:** It mathematically builds the Option strikes (e.g., Short Call, Long Call), applies the lot size, Calculates Stop Loss & Take Profit limits, and prints a final JSON payload to standard output for OpenClaw to catch.

### The Quantitative Pre-Entry Criteria (Safety Checks)

The bot is designed to err on the side of NOT trading. A trade is only generated if it passes:

* **ATR Spike Filter:** The current 1H ATR cannot be > 20% higher than the 3-day trailing average (blocks trading during sudden high volatility events).
* **60m Consolidation Filter:** The High/Low range of the last hour must be < $400 (ensures the market is actually settling before sideways strategies are deployed).
* **Trend Anchor Ban:** If the price dropped by more than $250 in the last 4 hours, all Bull Put Spreads are banned. If it rose by more than $250, Bear Call Spreads are banned.
* **Supertrend Filter:** Blocks Bull Put spreads if the 15-minute Supertrend is physically Red.
* **Funding Rate Trigger:** At 1:30 PM IST, if the funding rate is heavily positive (>0.0005) it bans Bull trades. If heavily negative, it bans Bear trades.
* **Fee-Aware Filter:** Bounces the final trade if the net credit collected per contract is less than roughly $15 to prevent "working to pay exchange fees."

---

## 2. The AI Assessment (`ai_validator.py`)

To prevent the mathematical algorithm from missing real-world macroeconomic context, the Python Brain uses a **Hybrid Quantitative + Qualitative** architecture.

Right before issuing a real trade payload, the Python code calls the `gemini-2.5-flash` LLM API.

* It feeds the model the exact quantitative state (e.g., Spot Price, ATR, 4H Momentum vector, Funding rate, and Proposed Strategy).
* It asks Gemini to score the trade setup from 1 to 10 and write a 2-sentence rationale.
* **The Kill Switch:** If Gemini gives the mathematical setup a score of `<= 5` (e.g., it senses a flash crash or bizarre contradiction the math missed), the Python script literally aborts its own trade.
* If the score is valid, it appends the written rationale into the JSON payload.

---

## 3. OpenClaw's Responsibilities (The Executor)

OpenClaw has **no authority** to pick strikes, decide the strategy, or determine the logic. OpenClaw is strictly a "Sentinel" execution assistant.

OpenClaw's exact responsibilities are:

1. **Macro-Scheduling:** Trigger `analyze_0dte.py` once per day.
2. **Listen Silently:** Ignore all the noisy terminal polling output the Brain produces.
3. **Parse & Present:** When the Brain outputs the final `--- OPENCLAW JSON PAYLOAD ---`, OpenClaw must parse it. It calculates the Estimated Max Profit, Max Loss, and Breakeven Point, grabs the AI Risk Assessment paragraph, and sends a highly formatted message to the user on Telegram.
4. **Human-in-the-Loop Execution:** OpenClaw waits for the user to reply "Yes".
5. **A-Symmetric Order Routing:** Upon approval, OpenClaw routes to the Delta Exchange API:
   * Primary `limit_order` for Short (Sell) legs to extract exact premium.
   * Primary `market_order` for Long (Buy) protection wings to guarantee fill.
6. **Autonomous Exchange Exits:** **CRITICAL** - Immediately after verifying the short legs are securely filled, OpenClaw natively triggers the hard Bracket Orders (Stop Market & Limit Take Profit) provided in the JSON directly onto the Delta Exchange servers so the bot is mathematically protected from blowouts even if the VM shuts down or the internet drops.
