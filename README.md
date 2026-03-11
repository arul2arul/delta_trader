# Operation Daily Profit: 0 DTE Delta Options Trader

This repository contains a highly conservative, quantitative 0-DTE (Zero Days to Expiration) crypto options trading bot designed specifically for the **Delta Exchange API**.

The architecture is entirely self-contained within Python:

1. **The Math Engine:** Analyzes real-time market data, checks heavy mathematical constraints, and selects the exact strikes.
2. **The Execution & Notification Engine:** Natively routes API calls to Delta Exchange in milliseconds securely, and uses the Telegram API to send execution receipts directly to your phone.

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
7. **Native Execution & Alerting:** It uses `order_manager.py` to seamlessly submit the Limit/Market orders + instant bracket Stop-Losses, and then fires a Telegram notification receipt to your phone instantly.

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

## 3. Autonomous Execution & Telegram Notification

The system is designed to run 100% autonomously without any "human-in-the-loop" approval needed, bypassing LLM agents entirely.

### Execution Safety

When `analyze_0dte.py` finds a trade:

1. It instantly fetches your wallet balance via `exchange_client.py` and calculates margin requirements safely.
2. It natively batch-submits the exact Limit/Market orders to Delta without resting failures.
3. **CRITICAL:** Instantly after submitting the trade, it uses `order_manager.py` to place **Hard Stop Loss** and **Take Profit** bracket orders natively on Delta Exchange servers, guaranteeing you are never left with naked options overnight even if your laptop/VM loses internet.

### Telegram Receipts

Instead of relying on third-party services, `notifier.py` connects directly to the Telegram API. Every time the bot successfully places an Iron Condor or Credit Spread, it will ping your phone with a receipt detailing the Exact Strategy, Net Credit collected, Spot Price at entry, and the AI's Rationale.

You do not need to click "Yes" – the bot is fully self-sufficient.
