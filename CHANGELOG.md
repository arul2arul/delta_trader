# Changelog - AI Trading Bot Hardening

All notable changes to the AI Trading Bot project will be documented in this file.

## [Unreleased] - 2026-03-15

### Added
- **State Recovery System**: Both `main.py` and `analyze_0dte.py` now scan Delta Exchange for open positions at startup. If a trade is found (e.g., after a crash or lid closure), the bot resumes monitoring immediately instead of starting fresh.
- **Detailed Rejection Context**: Added `get_market_liquidity_context` to log median spreads, quote counts, and max/min spreads when a trade is rejected due to slippage guards.
- **Sleep Prevention**: Integrated `wakepy` to prevent the laptop from entering power-saving modes or sleeping while the bot is active.
- **Market Stop Support**: Added `market_stop` order type support in `ExchangeClient` and `OrderManager` for guaranteed fills on protective orders.

### Changed
- **Stop Loss Mechanism**: Upgraded Stop-Loss orders from `Stop-Limit` to `Market Stop`. This ensures positions are closed even if the price gaps through the trigger strike during high volatility.
- **Network Resilience**: Wrapped the main trading loop in `analyze_0dte.py` in a `try-except` block to gracefully handle `ConnectionError` and `TimeoutError` by retrying after 10 seconds.
- **Logging Infrastructure**: Switched `brain_execution.log` to use `RotatingFileHandler` (10MB limit, 5 backups) to prevent disk space exhaustion.
- **Clock Synchronization**: Replaced generic NTP sync with Delta-specific server time sync to eliminate "Delta Drift" and signature mismatch errors.
- **AI Validation Logic**: Enhanced the Gemini prompt to specifically analyze Order Book Imbalance (`ob_imbalance`), Funding Rates, and look for "Squeeze" or "Stop Loss Cluster" setups.
- **Polling Intervals**: Increased `PNL_POLL_IRON_CONDOR` to 120s and `PNL_POLL_CREDIT_SPREAD` to 60s to accommodate network latency from local machines.

### Fixed
- Fixed an issue where the bot would lose track of open trades if the terminal session hung or the script was restarted.
- Fixed a potential "no-fill" risk on Stop Losses during sharp market moves by moving to Market Stops.
- Improved error reporting via Telegram for fatal crashes on local machines.
