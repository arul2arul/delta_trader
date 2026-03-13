"""
Operation Daily Profit - Configuration
All tunable constants and thresholds in one place.
"""

from enum import Enum
from dataclasses import dataclass


# ──────────────────────────────────────────────
# Exchange Endpoints
# ──────────────────────────────────────────────
PRODUCTION_URL = "https://api.india.delta.exchange"
TESTNET_URL = "https://cdn-ind.testnet.deltaex.org"

PRODUCTION_WS_URL = "wss://socket.india.delta.exchange"
TESTNET_WS_URL = "wss://socket-ind.testnet.deltaex.org"

# ──────────────────────────────────────────────
# Capital & Margin
# ──────────────────────────────────────────────
CAPITAL = 120_000           # ₹1,20,000 margin allocation
MAX_MARGIN_PCT = 0.60       # Max 60% of capital at risk
BUFFER_CAPITAL = 80_000     # ₹80,000 buffer (not traded)
BASE_LOT_SIZE = 10          # The default number of contracts per leg (Used during testing week)
USE_AI_VALIDATION = True    # Toggle AI second-opinion trade validation
MIN_NET_CREDIT = 3.0        # Minimum net credit ($) to accept a trade (fee-aware floor)

# ──────────────────────────────────────────────
# Profit & Loss Thresholds
# ──────────────────────────────────────────────
KILL_SWITCH_LOSS = -8_500   # Force-close all positions at -₹8,500 (approx $100)
STOPLOSS_MULTIPLIER = 2.5   # Per-leg stop: 2.5× premium collected
TARGET_DAILY_NET = 1_000    # ₹1,000 net daily target

# ──────────────────────────────────────────────
# Strike Selection (Delta-based)
# ──────────────────────────────────────────────
SHORT_DELTA = 0.10          # Iron Condor short legs (~90% win prob)
LONG_DELTA = 0.05           # Iron Condor wing protection
DIRECTIONAL_DELTA = 0.15    # Credit Spread short leg
WING_DELTA = 0.05           # Credit Spread wing (safety)

# ──────────────────────────────────────────────
# Technical Indicators
# ──────────────────────────────────────────────
RSI_PERIOD = 14
RSI_SIDEWAYS_LOW = 45       # Sideways if RSI in [45, 55]
RSI_SIDEWAYS_HIGH = 55
ADX_PERIOD = 14
ADX_THRESHOLD = 25          # Sideways if ADX < 25
EMA_PERIOD = 20             # 20-period EMA for trend
CANDLE_COUNT = 100          # Number of hourly candles to fetch
CANDLE_TIMEFRAME = "1h"     # Hourly candles

# ──────────────────────────────────────────────
# Implied Volatility
# ──────────────────────────────────────────────
IV_RANK_THRESHOLD = 70      # Widen wings if IV Rank > 70%

# ──────────────────────────────────────────────
# Scheduling (IST)
# ──────────────────────────────────────────────
TIMEZONE = "Asia/Kolkata"
DEPLOY_HOUR = 10            # 10:00 AM IST deployment
DEPLOY_MINUTE = 0
DEPLOY_WINDOW_MINUTES = 2   # ±2 min tolerance
TRADING_DAYS = [0, 1, 2, 3] # Mon=0, Tue=1, Wed=2, Thu=3
WEEKEND_SHUTDOWN_DAY = 4    # Friday
WEEKEND_SHUTDOWN_HOUR = 17  # 5:00 PM IST
WEEKEND_RESUME_DAY = 0      # Monday
WEEKEND_RESUME_HOUR = 9     # 9:00 AM IST

# ──────────────────────────────────────────────
# Monitoring & Polling
# ──────────────────────────────────────────────
PNL_POLL_IRON_CONDOR = 90   # seconds – wide buffer strategy
PNL_POLL_CREDIT_SPREAD = 45 # seconds – tighter strategy
USE_WEBSOCKET = True        # Use WebSocket for real-time updates

# ──────────────────────────────────────────────
# API Resilience
# ──────────────────────────────────────────────
API_RETRY_DELAY = 5         # seconds between retries
API_MAX_RETRIES = 3         # max retry attempts
API_RATE_LIMIT = 10         # max requests per second
BATCH_ORDER_MAX = 5         # Delta API max orders per batch

# ──────────────────────────────────────────────
# Notifications
# ──────────────────────────────────────────────
HEARTBEAT_INTERVAL = 3600   # seconds (1 hour)

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
TRADE_LOG_FILE = "trade_log.csv"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
LOG_LEVEL = "INFO"

# ──────────────────────────────────────────────
# Underlying Asset
# ──────────────────────────────────────────────
UNDERLYING_SYMBOL = "BTCUSD"
ASSET_SYMBOL = "BTC"


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────
class Regime(Enum):
    SIDEWAYS = "sideways"
    BULLISH = "bullish"
    BEARISH = "bearish"


class StrategyType(Enum):
    IRON_CONDOR = "iron_condor"
    BULL_CREDIT_SPREAD = "bull_credit_spread"
    BEAR_CREDIT_SPREAD = "bear_credit_spread"


class RiskAction(Enum):
    HOLD = "hold"
    KILL = "kill"
    PAYDAY = "payday"
    ROLL_LEG = "roll_leg"
    STOP_LEG = "stop_leg"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OptionType(Enum):
    CALL = "call_options"
    PUT = "put_options"


@dataclass
class Strike:
    """Represents a selected option strike."""
    product_id: int
    strike_price: float
    delta: float
    premium: float
    option_type: str
    symbol: str = ""


@dataclass
class OrderSpec:
    """Specification for a single order leg."""
    product_id: int
    side: str          # "buy" or "sell"
    size: int
    order_type: str    # "limit_order" or "market_order"
    limit_price: float = 0.0
    stop_price: float = 0.0
    strike_price: float = 0.0
    option_type: str = ""
    role: str = ""     # "short_call", "long_put", etc.
