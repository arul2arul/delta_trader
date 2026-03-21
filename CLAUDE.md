# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & Running

```bash
pip install -r requirements.txt
python setup.py          # interactive wizard to configure .env
python analyze_0dte.py   # core trading brain (runs as polling daemon)
python analyze_0dte.py --dry-run  # simulate without placing real orders
python main.py           # long-running orchestrator with scheduler
python bot_status.py     # live dashboard
```

Copy `.env.example` to `.env` and populate: Delta Exchange API keys, Telegram bot token, Gemini API key.

## Testing

```bash
python test_all.py       # runs the full functional test suite (no API keys needed)
```

The test suite (`test_all.py`) covers: indicators, regime detection, strike selector, strategy engine, risk manager, scheduler, trade logger, rate limiter, and notifier. Tests use mock data — no live exchange connection required except for the optional connectivity test at the end.

## Utility / Debug Scripts

| Script | Purpose |
|---|---|
| `bot_status.py` | Live dashboard reading `latest_status.json` and `rejection_log.jsonl` |
| `cancel_all.py` | Emergency: cancel all open orders on the exchange |
| `check_pos.py` / `check_trades.py` | Inspect live positions and recent fills |
| `debug_api.py` / `debug_wallet.py` | Low-level API diagnostics |
| `check_strategy.py` | Evaluate the current market regime and proposed strategy without trading |

## Architecture

`analyze_0dte.py` is the main trading brain. `main.py` is the long-running orchestrator that uses `scheduler.py` to invoke it during the IST trading window.

### Execution Flow

1. **State check** — abort if open position already exists (prevents double-entry)
2. **Data aggregation** — spot price + 1H candles via `market_data.py`
3. **Indicator computation** — RSI, EMA, ADX, ATR, VWAP, Supertrend via `indicators.py`
4. **Regime classification** — SIDEWAYS / BULLISH / BEARISH via `regime_detector.py`
5. **Pre-entry quantitative filters** — ATR spike, 60m consolidation, trend anchor ban, Supertrend direction, funding rate, fee-aware credit floor
6. **Strategy construction** — Iron Condor (SIDEWAYS) or Credit Spread (directional) via `strategy_engine.py`
7. **Pre-flight validation** — `preflight.py` checks capital sufficiency, clock sync (<2s drift), and L2 slippage before AI call
8. **AI second opinion** — Gemini 2.5-Flash scores 1–10; score ≤ 5 aborts the trade (`ai_validator.py`)
9. **Order execution** — batch limit/market orders + bracket SL/TP atomically via `order_router.py`
10. **Monitoring loop** — real-time PnL via WebSocket + polling in `monitor.py`; `risk_manager.py` evaluates KILL / PAYDAY / HOLD each cycle
11. **Post-trade forensics** — `post_trade_analyzer.py` writes root-cause analysis to SQLite after exit

### Module Reference

| Module | Role |
|---|---|
| `config.py` | Central config: capital (90K INR), trading windows, indicator thresholds |
| `exchange_client.py` | Delta Exchange REST API wrapper |
| `market_data.py` | Candle/OHLC aggregation |
| `indicators.py` | RSI, ADX, ATR, VWAP, EMA, Supertrend |
| `regime_detector.py` | Market regime classification: SIDEWAYS / BULLISH / BEARISH |
| `strategy_engine.py` | Iron Condor & Credit Spread position builders |
| `strike_selector.py` | Delta-based strike selection |
| `preflight.py` | Final gatekeeper: capital, clock sync, L2 slippage checks before execution |
| `ai_validator.py` | Gemini 2.5-Flash pre-trade approval — acts as kill-switch if score ≤ 5 |
| `order_manager.py` | Order execution + bracket SL/TP lifecycle management |
| `order_router.py` | Batch order submission to Delta Exchange |
| `risk_manager.py` | Daily PnL tracking; KILL / PAYDAY / HOLD evaluation each monitor cycle |
| `monitor.py` | Real-time position monitoring |
| `post_trade_analyzer.py` | Post-exit forensics and root-cause analysis |
| `database_manager.py` | SQLite trade journal (`data/openclaw_vault.db`) for crash recovery |
| `trade_logger.py` | CSV trade log (`trade_log.csv`) |
| `notifier.py` | Telegram alerts and heartbeats |
| `ws_client.py` | WebSocket client for real-time order updates |
| `scheduler.py` | IST-based scheduling; trading days Mon–Thu only |
| `rate_limiter.py` | Token-bucket rate limiter (10 req/s default); use `@rl.wrap` decorator |

## Key Design Decisions

- **Hybrid validation:** Quantitative signals are validated by Gemini AI before any trade is placed. AI acts as a circuit breaker, not a signal generator.
- **Atomic risk:** Bracket SL/TP orders are submitted atomically with the entry order via `order_router.py`.
- **Crash recovery:** All trades are journaled to SQLite (`data/openclaw_vault.db`) so state can be reconstructed after a restart.
- **Pre-entry filters:** ATR spike detection, 60-minute consolidation check, trend anchors, funding rate checks, and fee-aware PnL validation all gate order submission.
- **Trading schedule:** Mon–Thu only; Friday shutdown at 17:00 IST; Monday resume at 09:00 IST. Deploy window is 10:00–10:02 AM IST. Polling: 120s for Iron Condor, 60s for Credit Spread.
- **Risk thresholds (config.py):** Kill-switch at -4,500 INR daily loss (5% of 90K capital); per-leg stop at 2.5× premium collected; profit target starts at 500 INR (gradual growth to 2,000 INR goal).
- **`--dry-run` flag:** Pass to `analyze_0dte.py` to simulate the full pipeline without submitting real orders.
