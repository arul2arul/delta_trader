"""
WebSocket Client – Real-time price streaming from Delta Exchange.
Primary monitoring mechanism; REST polling is the fallback.
"""

import json
import time
import asyncio
import logging
import threading
from typing import Callable, Optional

import config

logger = logging.getLogger("ws_client")


class WebSocketClient:
    """
    WebSocket client for Delta Exchange real-time data.
    Pushes price updates instead of polling.
    Auto-reconnects on disconnect.
    """

    def __init__(self, exchange_client):
        self.exchange_client = exchange_client
        self.ws_url = exchange_client.ws_url
        self._ws = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Callbacks
        self._price_callbacks: list[Callable] = []
        self._fill_callbacks: list[Callable] = []
        self._pnl_callbacks: list[Callable] = []

        # State
        self._subscribed_channels: list[str] = []
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._last_price: dict = {}

    def on_price_update(self, callback: Callable):
        """Register a handler for real-time price ticks."""
        self._price_callbacks.append(callback)
        logger.info(f"Registered price update callback: {callback.__name__}")

    def on_order_fill(self, callback: Callable):
        """Register a handler for fill notifications."""
        self._fill_callbacks.append(callback)
        logger.info(f"Registered order fill callback: {callback.__name__}")

    def on_pnl_update(self, callback: Callable):
        """Register a handler for PnL updates."""
        self._pnl_callbacks.append(callback)
        logger.info(f"Registered PnL update callback: {callback.__name__}")

    def connect(self, symbols: list[str] = None):
        """
        Establish persistent WebSocket connection in a background thread.
        Subscribes to price channels for given symbols.
        """
        if self._running:
            logger.warning("WebSocket already connected")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_event_loop,
            args=(symbols or [],),
            daemon=True,
            name="ws-client",
        )
        self._thread.start()
        logger.info("WebSocket client thread started")

    def disconnect(self):
        """Gracefully disconnect the WebSocket."""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("WebSocket client disconnected")

    def _run_event_loop(self, symbols: list[str]):
        """Run the asyncio event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_listen(symbols))
        except Exception as e:
            logger.error(f"WebSocket event loop error: {e}")
        finally:
            self._loop.close()

    async def _connect_and_listen(self, symbols: list[str]):
        """Main WebSocket connection loop with auto-reconnect."""
        while self._running:
            try:
                import websockets

                logger.info(f"Connecting to WebSocket: {self.ws_url}")
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._reconnect_attempts = 0
                    logger.info("✅ WebSocket connected successfully")

                    # Subscribe to channels
                    await self._subscribe(ws, symbols)

                    # Listen for messages
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            await self._handle_message(data)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON from WS: {message[:100]}")

            except Exception as e:
                self._reconnect_attempts += 1
                if self._reconnect_attempts > self._max_reconnect_attempts:
                    logger.error(
                        f"Max reconnect attempts ({self._max_reconnect_attempts}) "
                        f"exceeded. Falling back to REST polling."
                    )
                    self._running = False
                    break

                delay = min(30, config.API_RETRY_DELAY * (2 ** (self._reconnect_attempts - 1)))
                logger.warning(
                    f"WebSocket disconnected: {e}. "
                    f"Reconnecting in {delay}s (attempt {self._reconnect_attempts})..."
                )
                await asyncio.sleep(delay)

    async def _subscribe(self, ws, symbols: list[str]):
        """Subscribe to price and position channels."""
        channels = []

        # Subscribe to ticker channels for each symbol
        for symbol in symbols:
            channels.append(f"v2/ticker/{symbol}")

        # Subscribe to user-specific channels (authenticated)
        if self.exchange_client.api_key:
            # Auth payload
            auth_msg = {
                "type": "auth",
                "payload": {
                    "api-key": self.exchange_client.api_key,
                }
            }
            await ws.send(json.dumps(auth_msg))
            logger.info("Sent WebSocket authentication")

            # Position and order channels
            channels.extend([
                "positions",
                "orders",
                "fills",
            ])

        # Subscribe
        if channels:
            sub_msg = {
                "type": "subscribe",
                "payload": {
                    "channels": channels
                }
            }
            await ws.send(json.dumps(sub_msg))
            self._subscribed_channels = channels
            logger.info(f"Subscribed to channels: {channels}")

    async def _handle_message(self, data: dict):
        """Route incoming WebSocket messages to registered callbacks."""
        msg_type = data.get("type", "")
        channel = data.get("channel", "")

        if "ticker" in channel:
            # Price update
            payload = data.get("data", data.get("payload", {}))
            symbol = data.get("symbol", channel.split("/")[-1] if "/" in channel else "")
            self._last_price[symbol] = payload
            for cb in self._price_callbacks:
                try:
                    cb(symbol, payload)
                except Exception as e:
                    logger.error(f"Price callback error: {e}")

        elif channel == "fills" or msg_type == "fill":
            # Order fill notification
            payload = data.get("data", data.get("payload", {}))
            for cb in self._fill_callbacks:
                try:
                    cb(payload)
                except Exception as e:
                    logger.error(f"Fill callback error: {e}")

        elif channel == "positions":
            # Position/PnL update
            payload = data.get("data", data.get("payload", {}))
            for cb in self._pnl_callbacks:
                try:
                    cb(payload)
                except Exception as e:
                    logger.error(f"PnL callback error: {e}")

        elif msg_type in ("subscriptions", "auth"):
            logger.debug(f"WS control message: {msg_type}")

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is actively connected."""
        return self._running and self._ws is not None

    def get_last_price(self, symbol: str) -> dict:
        """Get the last cached price for a symbol."""
        return self._last_price.get(symbol, {})
