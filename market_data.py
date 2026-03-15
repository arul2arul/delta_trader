"""
Market Data – Fetches candles, option chains, tickers, and Greeks.
REST-based; real-time updates come from ws_client.py.
"""

import time
import logging
from datetime import datetime, timedelta

import pandas as pd
import pytz

import config

logger = logging.getLogger("market_data")


class MarketData:
    """Market data provider using Delta Exchange REST API."""

    def __init__(self, exchange_client):
        self.client = exchange_client
        self.ist = pytz.timezone(config.TIMEZONE)

    def get_candles(self, symbol: str = None,
                           count: int = config.CANDLE_COUNT, resolution: str = config.CANDLE_TIMEFRAME) -> pd.DataFrame:
        """
        Fetch the last `count` hourly candles for a symbol.
        Returns DataFrame with columns: timestamp, open, high, low, close, volume.
        """
        symbol = symbol or config.UNDERLYING_SYMBOL
        now = datetime.now(self.ist)
        # Buffer end time by 1 minute to ensure it's in the past for the server
        end_ts = int((now - timedelta(minutes=1)).timestamp())
        start_ts = int((now - timedelta(hours=count)).timestamp())

        try:
            data = self.client.get_candles(
                symbol=symbol,
                resolution=resolution,
                start=start_ts,
                end=end_ts,
            )

            if not data or "result" not in data:
                logger.error("No candle data received from API")
                return pd.DataFrame()

            candles = data["result"]
            df = pd.DataFrame(candles)

            if df.empty:
                logger.warning("Empty candle data received")
                return df

            # Normalize column names
            column_map = {
                "time": "timestamp",
                "t": "timestamp",
                "open": "open",
                "o": "open",
                "high": "high",
                "h": "high",
                "low": "low",
                "l": "low",
                "close": "close",
                "c": "close",
                "volume": "volume",
                "v": "volume",
            }
            df = df.rename(columns={
                k: v for k, v in column_map.items() if k in df.columns
            })

            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            if "timestamp" in df.columns:
                df = df.sort_values("timestamp").reset_index(drop=True)

            logger.info(f"Fetched {len(df)} {resolution} candles for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Failed to fetch {resolution} candles for {symbol}: {e}")
            return pd.DataFrame()

    def get_btc_daily_contract(self) -> dict | None:
        """
        Identify the BTC daily expiry futures/options contract
        with the highest volume and optimal liquidity.
        """
        try:
            products = self.client.get_products()
            if not products:
                logger.error("No products returned from API")
                return None

            # Filter for BTC options with daily expiry
            btc_daily = []
            for p in products:
                symbol = str(p.get("symbol", "")).upper()
                contract_type = str(p.get("contract_type", "")).lower()
                is_btc = "BTC" in symbol
                is_option = contract_type in ["call_options", "put_options"]

                if is_btc and is_option:
                    # Check if this is a daily expiry (expires today or tomorrow)
                    settlement_time = p.get("settlement_time")
                    if settlement_time:
                        try:
                            exp_dt = datetime.fromisoformat(
                                settlement_time.replace("Z", "+00:00")
                            )
                            now = datetime.now(pytz.UTC)
                            hours_to_expiry = (exp_dt - now).total_seconds() / 3600
                            if 0 < hours_to_expiry <= 30:  # expires within 30 hours
                                btc_daily.append(p)
                        except (ValueError, TypeError):
                            continue

            if not btc_daily:
                logger.warning("No BTC daily expiry contracts found")
                return None

            # Find underlying product with best volume
            # Group by settlement_time to find the expiry batch
            expiry_groups = {}
            for p in btc_daily:
                exp = p.get("settlement_time", "")
                if exp not in expiry_groups:
                    expiry_groups[exp] = []
                expiry_groups[exp].append(p)

            # Pick the expiry group that expires soonest (true 0-DTE)
            # Sort the expiry times (they are strings like '2026-03-12T12:00:00Z', so string sort works)
            sorted_expiries = sorted(expiry_groups.keys())
            best_expiry = sorted_expiries[0]
            best_group = expiry_groups[best_expiry]
            
            logger.info(
                f"Found BTC daily expiry ({best_expiry}) with {len(best_group)} contracts"
            )
            return {
                "contracts": best_group,
                "expiry": best_expiry,
                "underlying_symbol": config.UNDERLYING_SYMBOL,
            }

        except Exception as e:
            logger.error(f"Failed to scan BTC daily contracts: {e}")
            return None

    def get_option_chain(self, expiry: str = None) -> list:
        """
        Fetch the full option chain for a given expiry.
        Returns list of dicts with strike, delta, gamma, theta, vega, etc.
        """
        try:
            # If no expiry given, find the daily contract
            if not expiry:
                daily = self.get_btc_daily_contract()
                if not daily:
                    return []
                contracts = daily["contracts"]
            else:
                products = self.client.get_products()
                contracts = [
                    p for p in products
                    if p.get("settlement_time") == expiry
                    and "BTC" in str(p.get("symbol", "")).upper()
                    and p.get("contract_type") in ["call_options", "put_options"]
                ]

            # Enrich each contract with ticker data (Greeks)
            # Fetch ALL tickers once to avoid N+1 API calls
            all_tickers = self.client.get_all_tickers()
            # Map product_id -> ticker
            ticker_map = {t.get("product_id"): t for t in all_tickers if t.get("product_id")}
            
            chain = []
            for contract in contracts:
                product_id = contract.get("id")
                try:
                    ticker = ticker_map.get(product_id)
                    if ticker:
                        greeks = ticker.get("greeks") or {} # Handle None
                        chain.append({
                            "product_id": product_id,
                            "symbol": contract.get("symbol", ""),
                            "strike_price": float(contract.get("strike_price", 0)),
                            "contract_type": contract.get("contract_type", ""),
                            "expiry": contract.get("settlement_time", ""),
                            "mark_price": float(ticker.get("mark_price", 0)),
                            "best_bid": float(ticker.get("best_bid", 0)),
                            "best_ask": float(ticker.get("best_ask", 0)),
                            "delta": float(greeks.get("delta", 0)),
                            "gamma": float(greeks.get("gamma", 0)),
                            "theta": float(greeks.get("theta", 0)),
                            "vega": float(greeks.get("vega", 0)),
                            "iv": float(greeks.get("iv", 0)),
                            "volume": float(ticker.get("volume", 0)),
                        })
                except Exception as e:
                    logger.warning(
                        f"Failed to process ticker for product {product_id}: {e}"
                    )
                    continue

            logger.info(f"Option chain loaded: {len(chain)} contracts")
            return chain

        except Exception as e:
            logger.error(f"Failed to fetch option chain: {e}")
            return []

    def get_spot_price(self) -> float:
        """Get the current BTC spot/index price."""
        try:
            # 1. Try to get index ticker directly
            ticker = self.client.get_ticker(config.UNDERLYING_SYMBOL)
            if ticker:
                price = float(ticker.get("mark_price", 0))
                logger.info(f"Current BTC price: ${price:,.2f}")
                return price

            # 2. Fallback: scan products if direct ticker fetch fails
            products = self.client.get_products()
            for p in products:
                symbol = str(p.get("symbol", "")).upper()
                if symbol == config.UNDERLYING_SYMBOL:
                    ticker = self.client.get_ticker(symbol)
                    if ticker:
                        return float(ticker.get("mark_price", 0))

            # 3. Fallback: get from any BTC futures
            for p in products:
                if "BTC" in str(p.get("symbol", "")).upper():
                    if p.get("contract_type") == "perpetual_futures":
                        ticker = self.client.get_ticker(p.get("symbol"))
                        if ticker:
                            return float(ticker.get("mark_price", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Failed to get spot price: {e}")
            return 0.0

    def get_iv_rank(self, chain: list) -> float:
        """
        Calculate a simple IV Rank from the option chain.
        Uses the average IV of ATM options as a proxy.
        Returns percentage (0-100).
        """
        if not chain:
            return 50.0  # default to moderate

        ivs = [c["iv"] for c in chain if c.get("iv", 0) > 0]
        if not ivs:
            return 50.0

        avg_iv = sum(ivs) / len(ivs)
        # Simple percentile: normalize IV to 0-100 range
        # Typical BTC IV ranges from 30% to 120%
        iv_rank = max(0, min(100, ((avg_iv - 30) / 90) * 100))
        logger.info(f"IV Rank: {iv_rank:.1f}% (avg IV: {avg_iv:.1f}%)")
        return iv_rank

    def get_funding_rate(self) -> float:
        """
        Fetch the current funding rate for the BTC perpetual contract.
        Useful for checking at 1:30 PM snapshot times.
        Returns the funding rate as a decimal (e.g., 0.0001 for 0.01%).
        """
        try:
            # We look for the main perpetual ticker
            ticker = self.client.get_ticker("BTCUSD")
            if not ticker:
                # Fallback to USDT perp if BTCUSD isn't active
                ticker = self.client.get_ticker("BTCUSDT")
            
            if ticker and "funding_rate" in ticker:
                rate = float(ticker.get("funding_rate", 0))
                logger.info(f"Current BTC Funding Rate: {rate * 100:.4f}%")
                return rate
            return 0.0
        except Exception as e:
            logger.error(f"Failed to fetch funding rate: {e}")
            return 0.0

    def get_top_5_orderbook_depth(self, symbol: str = "BTCUSD") -> dict:
        """
        Fetch top 5 levels of Bids and Asks for AI validation.
        """
        try:
            data = self.client.get_l2_orderbook(symbol)
            if not data:
                return {"bids": [], "asks": []}
            
            return {
                "bids": data.get("bids", [])[:5],
                "asks": data.get("asks", [])[:5]
            }
        except Exception as e:
            logger.error(f"Failed to fetch orderbook depth: {e}")
            return {"bids": [], "asks": []}

    def get_orderbook_imbalance(self, symbol: str = "BTCUSD") -> float:
        """
        Calculate Order Book Imbalance (Net Buy/Sell pressure).
        Checks top 10 levels of bids vs asks.
        Returns a value from -1.0 (heavy sell side) to 1.0 (heavy buy side).
        """
        try:
            data = self.client.get_l2_orderbook(symbol)
            if not data:
                return 0.0
            
            bids = data.get("bids", [])[:10]
            asks = data.get("asks", [])[:10]
            
            bid_vol = sum(float(b[1]) for b in bids)
            ask_vol = sum(float(a[1]) for a in asks)
            
            if bid_vol + ask_vol == 0:
                return 0.0
                
            imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
            logger.info(f"Order Book Imbalance ({symbol}): {imbalance:.2f} (Bid: {bid_vol:.1f}, Ask: {ask_vol:.1f})")
            return imbalance
        except Exception as e:
            logger.error(f"Failed to calculate imbalance: {e}")
            return 0.0
