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

    def get_hourly_candles(self, symbol: str = None,
                           count: int = config.CANDLE_COUNT) -> pd.DataFrame:
        """
        Fetch the last `count` hourly candles for a symbol.
        Returns DataFrame with columns: timestamp, open, high, low, close, volume.
        """
        symbol = symbol or config.UNDERLYING_SYMBOL
        now = datetime.now(self.ist)
        end_ts = int(now.timestamp())
        start_ts = int((now - timedelta(hours=count)).timestamp())

        try:
            data = self.client.get_candles(
                symbol=symbol,
                resolution="60",  # 60 minutes
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

            logger.info(f"Fetched {len(df)} hourly candles for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Failed to fetch candles for {symbol}: {e}")
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

            # Pick the expiry group with the most contracts (most liquid)
            best_group = max(expiry_groups.values(), key=len)
            logger.info(
                f"Found BTC daily expiry with {len(best_group)} contracts"
            )
            return {
                "contracts": best_group,
                "expiry": best_group[0].get("settlement_time"),
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
            chain = []
            for contract in contracts:
                product_id = contract.get("id")
                try:
                    ticker = self.client.get_ticker(product_id)
                    if ticker:
                        greeks = ticker.get("greeks", {})
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
                        f"Failed to get ticker for product {product_id}: {e}"
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
            products = self.client.get_products()
            for p in products:
                symbol = str(p.get("symbol", "")).upper()
                if symbol == config.UNDERLYING_SYMBOL:
                    ticker = self.client.get_ticker(p.get("id"))
                    if ticker:
                        price = float(ticker.get("mark_price", 0))
                        logger.info(f"Current BTC price: ${price:,.2f}")
                        return price
            # Fallback: get from any BTC futures
            for p in products:
                if "BTC" in str(p.get("symbol", "")).upper():
                    if p.get("contract_type") == "perpetual_futures":
                        ticker = self.client.get_ticker(p.get("id"))
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
