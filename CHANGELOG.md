# Changelog - AI Trading Bot Hardening

All notable changes to the AI Trading Bot project will be documented in this file.

## [Unreleased] - 2026-03-15

### Added

- **Post-Trade Analysis System**: Implemented `PostTradeAnalyzer` to perform forensic audits on trade exits, including root-cause analysis for stop-losses (Wicks, Trend Breaks, IV Spikes).
- **SQLite Trade Memory**: Introduced a dedicated SQLite database (`openclaw_vault.db`) for persistent "Trade Memory," tracking every basket leg and AI critique through restarts.
- **AI Feedback Loop**: Integrated a 7-day running win-rate into the AI pre-flight prompt, allowing the model to adjust risk based on recent bot performance.
- **Strategy Suspension Fail-Safe**: Automatic "Kill-Switch" that halts trading and alerts via Telegram after 3 consecutive stop-loss hits for manual strategy review.
- **Historical Performance Reports**: Telegram heartbeats now include a performance snapshot of the last 5 trades to provide EOD context at a glance.

### Changed

- **Atomic Order Logging**: Every trade is now written to SQLite as `PENDING` before the API call is fired, ensuring no trade is "lost" due to network or script failure.
- **Multi-Layer State Recovery**: Enhanced startup logic to cross-reference live exchange positions with persistent DB state for guaranteed re-attachment to orphan trades.
- **Telegram Reliability**: Sanitized strategy and metadata strings in Telegram alerts to prevent Markdown formatting errors (re-formatting underscores).

### Fixed

- **NameError in Strike Selection**: Resolved bug in `strike_selector.py` where Greek variables were referenced before their retrieval from the chain.
- **State Recovery Crash**: Fixed `active_positions` NameError that occurred during the startup sequence after the SQLite refactor.
- **Clock Drift Detection**: Improved reliability of pre-flight timestamp validation against Delta Exchange servers.

## [Previous] - 2026-03-15 (Batch 1)

### Previous Added

- **State Recovery System**: Both `main.py` and `analyze_0dte.py` now scan Delta Exchange for open positions at startup.
- **Detailed Rejection Context**: Added `get_market_liquidity_context` to log median spreads.
- **Sleep Prevention**: Integrated `wakepy` for local server stability.
- **Market Stop Support**: Added `market_stop` order type for guaranteed SL fills.
