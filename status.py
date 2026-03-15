"""
Market Status – Read-only dashboard showing live market conditions.
Run: python status.py

No trades are placed. This is a safe, read-only script.
"""

import sys
import io
import os
import logging
from datetime import datetime

import pytz
import requests
from dotenv import load_dotenv

import config

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Quiet logging for clean output
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("status")

load_dotenv()

ist = pytz.timezone(config.TIMEZONE)


def print_header(title):
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def print_row(label, value, indent=2):
    print(f"{'  ' * indent}{label:<30} {value}")


def fetch_public_products(base_url):
    """Fetch products list from public API (no auth needed)."""
    try:
        resp = requests.get(f"{base_url}/v2/products", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", data) if isinstance(data, dict) else data
    except Exception as e:
        logger.debug(f"Products fetch error: {e}")
        return []


def fetch_public_ticker(base_url, symbol):
    """Fetch ticker from public API (no auth needed)."""
    try:
        resp = requests.get(
            f"{base_url}/v2/tickers/{symbol}",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", data) if isinstance(data, dict) else data
    except Exception as e:
        logger.debug(f"Ticker fetch error: {e}")
        return None


def main():
    now = datetime.now(ist)
    print_header(f"MARKET STATUS - {now.strftime('%Y-%m-%d %H:%M:%S IST')}")

    use_testnet = os.getenv("USE_TESTNET", "true").lower() == "true"
    base_url = config.TESTNET_URL if use_testnet else config.PRODUCTION_URL
    has_keys = bool(os.getenv("DELTA_API_KEY")) and bool(os.getenv("DELTA_API_SECRET"))

    env_label = "TESTNET" if use_testnet else "PRODUCTION"
    print_row("Environment", f"{env_label} ({base_url})")
    print_row("API Keys Configured", "YES" if has_keys else "NO - Some data unavailable")

    # ── Schedule Status ──
    from scheduler import Scheduler
    sch = Scheduler()
    print_header("Schedule")
    print_row("Day", now.strftime("%A"))
    print_row("Weekend Blackout", "ACTIVE" if sch.is_weekend_blackout(now) else "No")
    print_row("Trading Day", "Yes" if sch.is_trading_day(now) else "No")
    print_row("Deploy Window",
              "NOW" if sch.is_deploy_time(now)
              else f"in {sch.seconds_until_deploy(now)/60:.0f} min")

    # ── API Connectivity ──
    print_header("API Connectivity")
    try:
        resp = requests.get(f"{base_url}/v2/settings", timeout=10)
        if resp.status_code == 200:
            print_row("Status", "CONNECTED")
        else:
            print_row("Status", f"FAILED (HTTP {resp.status_code})")
            return
    except Exception as e:
        print_row("Status", f"OFFLINE - {e}")
        return

    # ── BTC Spot Price (Public) ──
    print_header("BTC Price (Public Data)")
    spot_price = None

    # Try to get BTC spot from products / ticker
    products = fetch_public_products(base_url)
    btc_spot_product = None
    btc_options = []

    if isinstance(products, list):
        for p in products:
            symbol = str(p.get("symbol", ""))
            ptype = str(p.get("product_type", p.get("contract_type", "")))

            # Find spot/future for BTC price
            if "BTCUSD" in symbol.upper() and "option" not in ptype.lower():
                if btc_spot_product is None or "perp" in ptype.lower():
                    btc_spot_product = p

            # Collect BTC options
            if "BTC" in symbol.upper() and "option" in ptype.lower():
                btc_options.append(p)

    if btc_spot_product:
        symbol = btc_spot_product.get("symbol", "")
        ticker = fetch_public_ticker(base_url, symbol)
        if ticker:
            mark = ticker.get("mark_price", ticker.get("close", 0))
            last = ticker.get("last_price", ticker.get("close", 0))
            if mark:
                spot_price = float(mark)
                print_row("Mark Price", f"${spot_price:,.2f}")
            if last:
                print_row("Last Traded", f"${float(last):,.2f}")
            vol24h = ticker.get("volume", 0)
            if vol24h:
                print_row("24h Volume", f"{float(vol24h):,.2f}")
            oi = ticker.get("oi", ticker.get("open_interest", 0))
            if oi:
                print_row("Open Interest", f"{float(oi):,.2f}")
            print_row("Source", f"Ticker: {symbol}")
    else:
        print_row("BTC Price", "Could not find BTC product on this exchange")

    # ── Technical Indicators (if we have candle data) ──
    print_header("Technical Indicators")
    try:
        from exchange_client import ExchangeClient
        ec = ExchangeClient()
        from market_data import MarketData
        md = MarketData(ec)

        df = md.get_hourly_candles()
        if df is not None and not df.empty:
            from indicators import compute_all
            df = compute_all(df)

            last = df.iloc[-1]
            rsi = last.get("rsi", None)
            ema = last.get(f"ema_{config.EMA_PERIOD}", None)
            adx = last.get("adx", None)
            close = last.get("close", None)

            if rsi is not None:
                rsi_status = "Overbought" if rsi > 70 else "Oversold" if rsi < 30 else "Neutral"
                print_row("RSI (14)", f"{rsi:.2f} - {rsi_status}")
            if ema is not None and close is not None:
                ema_side = "Above (Bullish)" if close > ema else "Below (Bearish)"
                print_row(f"EMA ({config.EMA_PERIOD})", f"{ema:.2f} (Price {ema_side})")
            if adx is not None:
                adx_str = "Strong Trend" if adx > 25 else "Weak/Sideways"
                print_row("ADX", f"{adx:.2f} - {adx_str}")

            # ── Regime Detection ──
            print_header("Market Regime")
            from regime_detector import detect_regime, check_volatility, get_strategy_for_regime
            regime = detect_regime(df)
            strategy_type = get_strategy_for_regime(regime)

            print_row("Detected Regime", regime.value.upper())
            print_row("Recommended Strategy", strategy_type.value)
            poll = 90 if strategy_type == config.StrategyType.IRON_CONDOR else 45
            print_row("Monitor Interval", f"{poll}s")
        else:
            print_row("Indicators", "No candle data available")
    except Exception as e:
        print_row("Indicators", f"Error: {e}")

    # ── Option Chain (Public) ──
    print_header("BTC Options Chain")
    if btc_options:
        print_row("Available Contracts", len(btc_options))

        calls = [c for c in btc_options if "call" in str(c.get("contract_type", "")).lower()]
        puts = [c for c in btc_options if "put" in str(c.get("contract_type", "")).lower()]
        print_row("Calls", len(calls))
        print_row("Puts", len(puts))

        # Show nearest strikes
        if spot_price and calls:
            atm_calls = sorted(
                calls,
                key=lambda c: abs(float(c.get("strike_price", 0)) - spot_price)
            )[:3]
            print(f"\n    Nearest Call Strikes (ATM):")
            for c in atm_calls:
                strike = float(c.get("strike_price", 0))
                sym = c.get("symbol", "?")
                print(f"      K={strike:>10,.0f}  {sym}")

        if spot_price and puts:
            atm_puts = sorted(
                puts,
                key=lambda c: abs(float(c.get("strike_price", 0)) - spot_price)
            )[:3]
            print(f"\n    Nearest Put Strikes (ATM):")
            for p in atm_puts:
                strike = float(p.get("strike_price", 0))
                sym = p.get("symbol", "?")
                print(f"      K={strike:>10,.0f}  {sym}")
    else:
        print_row("Options", "No BTC option contracts found")

    # ── Authenticated-only sections ──
    if has_keys:
        try:
            from exchange_client import ExchangeClient
            ec = ExchangeClient()

            # Wallet
            print_header("Wallet (Authenticated)")
            balance = ec.get_wallet_balance()
            print_row("Available Balance", f"{balance:,.6f}")
            print_row("Capital Allocated", f"{config.CAPITAL:,} INR")

            # Positions
            print_header("Open Positions")
            positions = ec.get_positions()
            if positions and isinstance(positions, list):
                active = [p for p in positions if abs(int(p.get("size", 0))) > 0]
                if active:
                    print_row("Active Positions", len(active))
                    total_pnl = 0
                    for pos in active:
                        pid = int(pos.get("product_id", 0))
                        # Use ticker/cache or just fetch if needed
                        symbol = pos.get("symbol")
                        if not symbol:
                            try:
                                prod = ec.get_product(pid)
                                symbol = prod.get("symbol", f"PID:{pid}")
                            except:
                                symbol = f"PID:{pid}"
                                
                        size = int(pos.get("size", 0))
                        side = "LONG" if size > 0 else "SHORT"
                        pnl = float(pos.get("unrealized_pnl", 0))
                        total_pnl += pnl
                        print(f"      {side:>5} {abs(size):<3}x {symbol:<22} PnL={pnl:,.2f}")
                    print_row("Total Unrealized PnL", f"{total_pnl:,.2f}")
                else:
                    print_row("Positions", "None (flat)")
            else:
                print_row("Positions", "None (flat)")
        except Exception as e:
            print_row("Wallet/Positions", f"Error: {e}")
    else:
        print_header("Wallet & Positions")
        print_row("Status", "Skipped - No API keys configured")
        print("    Set DELTA_API_KEY and DELTA_API_SECRET in .env")

    # ── Risk Config ──
    print_header("Risk Limits (Config)")
    print_row("Kill Switch", f"at {config.KILL_SWITCH_LOSS:,} INR")
    try:
        print_row("PayDay Exit", f"at {config.TARGET_DAILY_NET:,} INR")
    except AttributeError:
        pass
    print_row("Per-Leg Stop Loss", f"{config.STOPLOSS_MULTIPLIER}x premium")
    print_row("Max Margin", f"{config.MAX_MARGIN_PCT * 100:.0f}% of capital")

    print(f"\n{'=' * 60}")
    print(f"  Status check complete. No trades were placed.")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
