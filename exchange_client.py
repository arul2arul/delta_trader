"""
Exchange Client – Delta Exchange India API wrapper.
Handles connectivity, time sync, auth, and wallet balance.
"""

import os
import time
import logging

import requests
import ntplib
from dotenv import load_dotenv

import config
from rate_limiter import rate_limiter

logger = logging.getLogger("exchange_client")

load_dotenv()


class ExchangeClient:
    """Wrapper around Delta Exchange India REST API."""

    def __init__(self):
        self.api_key = os.getenv("DELTA_API_KEY", "")
        self.api_secret = os.getenv("DELTA_API_SECRET", "")
        use_testnet = os.getenv("USE_TESTNET", "true").lower() == "true"

        self.base_url = config.TESTNET_URL if use_testnet else config.PRODUCTION_URL
        self.ws_url = config.TESTNET_WS_URL if use_testnet else config.PRODUCTION_WS_URL
        self.is_testnet = use_testnet

        self._delta_client = None
        self._init_client()

        env_label = "TESTNET" if use_testnet else "PRODUCTION"
        logger.info(f"Exchange client initialized [{env_label}] → {self.base_url}")

    def _init_client(self):
        """Initialize the delta-rest-client."""
        try:
            from delta_rest_client import DeltaRestClient
            self._delta_client = DeltaRestClient(
                base_url=self.base_url,
                api_key=self.api_key,
                api_secret=self.api_secret,
            )
            logger.info("DeltaRestClient initialized successfully")
        except ImportError:
            logger.warning(
                "delta-rest-client not installed. "
                "Install with: pip install delta-rest-client"
            )
        except Exception as e:
            logger.error(f"Failed to initialize DeltaRestClient: {e}")

    def _retry(self, func, *args, **kwargs):
        """
        Retry wrapper with exponential backoff.
        All API calls should go through this.
        """
        last_error = None
        for attempt in range(1, config.API_MAX_RETRIES + 1):
            try:
                rate_limiter.acquire()
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                delay = config.API_RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"API call failed (attempt {attempt}/{config.API_MAX_RETRIES}): "
                    f"{e}. Retrying in {delay}s..."
                )
                time.sleep(delay)

        logger.error(f"API call failed after {config.API_MAX_RETRIES} retries: {last_error}")
        raise last_error

    # ──────────────────────────────────────────
    # Connectivity & Health
    # ──────────────────────────────────────────
    def check_connectivity(self) -> bool:
        """Ping the API and verify we get a valid response."""
        try:
            url = f"{self.base_url}/v2/settings"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                logger.info("✅ API connectivity check PASSED")
                return True
            else:
                logger.error(f"❌ API connectivity check FAILED (HTTP {resp.status_code})")
                return False
        except Exception as e:
            logger.error(f"❌ API connectivity check FAILED: {e}")
            return False

    def check_time_sync(self, max_drift_sec: float = 2.0) -> bool:
        """
        Compare local clock with NTP server.
        Warns if drift exceeds threshold (signature mismatches).
        """
        try:
            ntp_client = ntplib.NTPClient()
            response = ntp_client.request("pool.ntp.org", version=3)
            offset = abs(response.offset)

            if offset > max_drift_sec:
                logger.warning(
                    f"⚠️ Clock drift detected: {offset:.2f}s "
                    f"(threshold: {max_drift_sec}s). "
                    "This may cause API signature mismatches!"
                )
                return False
            else:
                logger.info(f"✅ Time sync OK (drift: {offset:.3f}s)")
                return True
        except Exception as e:
            logger.warning(f"⚠️ NTP time sync check failed: {e}. Proceeding anyway.")
            return True  # Don't block on NTP failure

    # ──────────────────────────────────────────
    # Account
    # ──────────────────────────────────────────
    def get_wallet_balance(self) -> float:
        """Fetch available margin/balance in INR."""
        try:
            def _fetch():
                return self._delta_client.get_balances()

            result = self._retry(_fetch)
            if result and isinstance(result, list):
                for asset in result:
                    if asset.get("asset_symbol") == "INR":
                        balance = float(asset.get("available_balance", 0))
                        logger.info(f"Wallet balance: ₹{balance:,.2f}")
                        return balance
            logger.warning("Could not find INR balance in wallet response")
            return 0.0
        except Exception as e:
            logger.error(f"Failed to fetch wallet balance: {e}")
            return 0.0

    # ──────────────────────────────────────────
    # Products & Market Data Pass-through
    # ──────────────────────────────────────────
    def get_products(self):
        """Fetch all available products."""
        def _fetch():
            return self._delta_client.get_products()
        return self._retry(_fetch)

    def get_product(self, product_id: int):
        """Fetch a single product by ID."""
        def _fetch():
            return self._delta_client.get_product(product_id)
        return self._retry(_fetch)

    def get_ticker(self, product_id: int):
        """Fetch ticker data (includes Greeks for options)."""
        def _fetch():
            return self._delta_client.get_ticker(product_id)
        return self._retry(_fetch)

    def get_candles(self, symbol: str, resolution: str, start: int, end: int):
        """Fetch OHLCV candle data."""
        def _fetch():
            url = f"{self.base_url}/v2/history/candles"
            params = {
                "resolution": resolution,
                "symbol": symbol,
                "start": start,
                "end": end,
            }
            rate_limiter.acquire()
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        return self._retry(_fetch)

    # ──────────────────────────────────────────
    # Orders
    # ──────────────────────────────────────────
    def place_order(self, product_id: int, size: int, side: str,
                    order_type: str = "market_order", limit_price: float = 0,
                    stop_price: float = 0):
        """Place a single order."""
        def _place():
            order = {
                "product_id": product_id,
                "size": size,
                "side": side,
                "order_type": order_type,
            }
            if order_type == "limit_order" and limit_price > 0:
                order["limit_price"] = str(limit_price)
            if stop_price > 0:
                order["stop_price"] = str(stop_price)
                order["order_type"] = "stop_market_order"
            return self._delta_client.create_order(order)
        return self._retry(_place)

    def batch_create_orders(self, orders: list):
        """Place multiple orders in a single batch (max 5)."""
        def _batch():
            return self._delta_client.batch_create(orders)
        return self._retry(_batch)

    def cancel_order(self, order_id: int, product_id: int):
        """Cancel a specific order."""
        def _cancel():
            return self._delta_client.cancel_order(product_id, order_id)
        return self._retry(_cancel)

    def cancel_all_orders(self, product_id: int = None):
        """Cancel all open orders, optionally for a specific product."""
        def _cancel():
            return self._delta_client.cancel_all_orders(product_id)
        return self._retry(_cancel)

    # ──────────────────────────────────────────
    # Positions
    # ──────────────────────────────────────────
    def get_positions(self):
        """Fetch all open positions."""
        def _fetch():
            return self._delta_client.get_position()
        return self._retry(_fetch)

    def close_position(self, product_id: int):
        """Market-close a specific position."""
        try:
            positions = self.get_positions()
            if not positions:
                return None

            for pos in positions:
                if pos.get("product_id") == product_id:
                    size = abs(int(pos.get("size", 0)))
                    if size == 0:
                        return None
                    # Reverse the side to close
                    current_side = pos.get("side", "")
                    close_side = "sell" if current_side == "buy" else "buy"
                    return self.place_order(
                        product_id=product_id,
                        size=size,
                        side=close_side,
                        order_type="market_order",
                    )
            return None
        except Exception as e:
            logger.error(f"Failed to close position {product_id}: {e}")
            raise

    def close_all_positions(self):
        """Market-close ALL open positions (Kill Switch)."""
        logger.critical("🚨 KILL SWITCH ACTIVATED – Closing all positions!")
        results = []
        try:
            positions = self.get_positions()
            if not positions:
                logger.info("No open positions to close")
                return results

            for pos in positions:
                size = abs(int(pos.get("size", 0)))
                if size > 0:
                    product_id = pos.get("product_id")
                    current_side = pos.get("side", "")
                    close_side = "sell" if current_side == "buy" else "buy"
                    try:
                        result = self.place_order(
                            product_id=product_id,
                            size=size,
                            side=close_side,
                            order_type="market_order",
                        )
                        results.append(result)
                        logger.info(f"Closed position: product_id={product_id}, size={size}")
                    except Exception as e:
                        logger.error(f"Failed to close product_id={product_id}: {e}")
        except Exception as e:
            logger.error(f"Error during kill switch: {e}")
        return results

    @property
    def client(self):
        """Direct access to the underlying DeltaRestClient."""
        return self._delta_client
