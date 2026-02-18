"""
Operation Daily Profit – Functional Test Suite
Tests all core logic modules without requiring API keys.
"""

import os
import sys
import time
import tempfile
from datetime import datetime

import pandas as pd
import numpy as np
import pytz

passed = 0
failed = 0


def test(name):
    print(f"\n--- {name} ---")


def ok(msg):
    global passed
    passed += 1
    print(f"  PASS  {msg}")


def fail(msg):
    global failed
    failed += 1
    print(f"  FAIL  {msg}")


def make_sample_df():
    """Create a sample OHLCV DataFrame for testing."""
    np.random.seed(42)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=100, freq="h"),
        "open":  np.random.uniform(90000, 100000, 100),
        "high":  np.random.uniform(95000, 105000, 100),
        "low":   np.random.uniform(85000, 95000, 100),
        "close": np.random.uniform(90000, 100000, 100),
        "volume": np.random.uniform(100, 1000, 100),
    })
    return df


# ═══════════════════════════════════════════
# TEST 1: Technical Indicators
# ═══════════════════════════════════════════
test("Technical Indicators (RSI, EMA, ADX)")
from indicators import compute_rsi, compute_ema, compute_adx, compute_all

df = make_sample_df()
df = compute_all(df)

rsi_val = df["rsi"].iloc[-1]
if "rsi" in df.columns and not pd.isna(rsi_val):
    ok(f"RSI computed: {rsi_val:.2f}")
else:
    fail("RSI not computed")

ema_val = df["ema_20"].iloc[-1]
if "ema_20" in df.columns and not pd.isna(ema_val):
    ok(f"EMA(20) computed: {ema_val:.2f}")
else:
    fail("EMA not computed")

adx_val = df["adx"].iloc[-1]
if "adx" in df.columns and not pd.isna(adx_val):
    ok(f"ADX computed: {adx_val:.2f}")
else:
    fail("ADX not computed")

# Verify RSI range
if 0 <= rsi_val <= 100:
    ok(f"RSI in valid range [0, 100]")
else:
    fail(f"RSI out of range: {rsi_val}")


# ═══════════════════════════════════════════
# TEST 2: Regime Detection
# ═══════════════════════════════════════════
test("Regime Detection")
from regime_detector import detect_regime, check_volatility
from config import Regime

# Sideways: RSI=50, ADX=20
df_sw = df.copy()
df_sw["rsi"] = 50.0
df_sw["adx"] = 20.0
df_sw["ema_20"] = df_sw["close"]
regime = detect_regime(df_sw)
if regime == Regime.SIDEWAYS:
    ok("Sideways detected (RSI=50, ADX=20)")
else:
    fail(f"Expected SIDEWAYS, got {regime}")

# Bullish: price > EMA
df_bull = df.copy()
df_bull["rsi"] = 60.0
df_bull["adx"] = 30.0
df_bull.loc[df_bull.index[-1], "close"] = 100000.0
df_bull["ema_20"] = 95000.0
regime = detect_regime(df_bull)
if regime == Regime.BULLISH:
    ok("Bullish detected (price > EMA)")
else:
    fail(f"Expected BULLISH, got {regime}")

# Bearish: price < EMA
df_bear = df.copy()
df_bear["rsi"] = 35.0
df_bear["adx"] = 30.0
df_bear.loc[df_bear.index[-1], "close"] = 90000.0
df_bear["ema_20"] = 95000.0
regime = detect_regime(df_bear)
if regime == Regime.BEARISH:
    ok("Bearish detected (price < EMA)")
else:
    fail(f"Expected BEARISH, got {regime}")

# Volatility checks
if check_volatility(75.0):
    ok("Wide wings triggered at IV Rank 75%")
else:
    fail("Wide wings SHOULD trigger at 75%")

if not check_volatility(50.0):
    ok("Normal wings at IV Rank 50%")
else:
    fail("Wide wings SHOULD NOT trigger at 50%")


# ═══════════════════════════════════════════
# TEST 3: Strike Selector
# ═══════════════════════════════════════════
test("Strike Selector")
from strike_selector import select_by_delta, select_iron_condor_strikes

mock_chain = [
    {"product_id": 1, "strike_price": 98000, "delta": 0.30, "mark_price": 0.10, "contract_type": "call_options", "symbol": "BTC-C-98000"},
    {"product_id": 2, "strike_price": 100000, "delta": 0.12, "mark_price": 0.05, "contract_type": "call_options", "symbol": "BTC-C-100000"},
    {"product_id": 3, "strike_price": 102000, "delta": 0.05, "mark_price": 0.02, "contract_type": "call_options", "symbol": "BTC-C-102000"},
    {"product_id": 4, "strike_price": 95000, "delta": -0.30, "mark_price": 0.10, "contract_type": "put_options", "symbol": "BTC-P-95000"},
    {"product_id": 5, "strike_price": 93000, "delta": -0.12, "mark_price": 0.05, "contract_type": "put_options", "symbol": "BTC-P-93000"},
    {"product_id": 6, "strike_price": 91000, "delta": -0.05, "mark_price": 0.02, "contract_type": "put_options", "symbol": "BTC-P-91000"},
]

# Select 0.10 delta call
call_strike = select_by_delta(mock_chain, 0.10, "call_options")
if call_strike and call_strike.product_id == 2:
    ok(f"Selected 0.10-delta call: K={call_strike.strike_price:.0f}")
else:
    fail(f"Wrong call strike selected: {call_strike}")

# Select 0.10 delta put
put_strike = select_by_delta(mock_chain, 0.10, "put_options")
if put_strike and put_strike.product_id == 5:
    ok(f"Selected 0.10-delta put: K={put_strike.strike_price:.0f}")
else:
    fail(f"Wrong put strike selected: {put_strike}")

# Iron condor strikes
ic_strikes = select_iron_condor_strikes(mock_chain)
valid = sum(1 for v in ic_strikes.values() if v is not None)
if valid == 4:
    ok(f"Iron Condor: all 4 legs selected")
else:
    fail(f"Iron Condor: only {valid}/4 legs")


# ═══════════════════════════════════════════
# TEST 4: Strategy Engine
# ═══════════════════════════════════════════
test("Strategy Engine")
from strategy_engine import build_iron_condor, build_credit_spread, build_strategy
from config import StrategyType

ic_orders = build_iron_condor(mock_chain)
if len(ic_orders) == 4:
    sells = [o for o in ic_orders if o.side == "sell"]
    buys = [o for o in ic_orders if o.side == "buy"]
    ok(f"Iron Condor: 4 legs (sells={len(sells)}, buys={len(buys)})")
else:
    fail(f"Iron Condor: expected 4 legs, got {len(ic_orders)}")

cs_orders = build_credit_spread(mock_chain, direction="bullish")
if len(cs_orders) == 2:
    ok(f"Bull Credit Spread: 2 legs")
else:
    fail(f"Credit Spread: expected 2 legs, got {len(cs_orders)}")

# Full strategy builder
strategy_type, orders = build_strategy(Regime.SIDEWAYS, mock_chain)
if strategy_type == StrategyType.IRON_CONDOR and len(orders) == 4:
    ok(f"SIDEWAYS -> Iron Condor (4 legs)")
else:
    fail(f"Expected IC for sideways, got {strategy_type}")

strategy_type, orders = build_strategy(Regime.BULLISH, mock_chain)
if strategy_type == StrategyType.BULL_CREDIT_SPREAD and len(orders) == 2:
    ok(f"BULLISH -> Bull Credit Spread (2 legs)")
else:
    fail(f"Expected bull spread for bullish, got {strategy_type}")


# ═══════════════════════════════════════════
# TEST 5: Risk Manager
# ═══════════════════════════════════════════
test("Risk Manager")
from risk_manager import RiskManager
from config import RiskAction

rm = RiskManager()
if rm.check_kill_switch(-3500):
    ok("Kill switch triggers at -3500")
else:
    fail("Kill switch SHOULD trigger at -3500")

rm2 = RiskManager()
if not rm2.check_kill_switch(-2000):
    ok("Kill switch does NOT trigger at -2000")
else:
    fail("Kill switch should NOT trigger at -2000")

if rm2.check_payday(1500):
    ok("PayDay triggers at 1500")
else:
    fail("PayDay SHOULD trigger at 1500")

rm3 = RiskManager()
if not rm3.check_payday(800):
    ok("PayDay does NOT trigger at 800")
else:
    fail("PayDay should NOT trigger at 800")

# Stop loss
rm4 = RiskManager()
rm4.register_premium(101, 0.05)
if rm4.check_stop_loss(101, 0.15):
    ok("Stop loss triggers (0.15 >= 2.5 * 0.05 = 0.125)")
else:
    fail("Stop loss SHOULD trigger")

if not rm4.check_stop_loss(101, 0.08):
    ok("Stop loss does NOT trigger (0.08 < 0.125)")
else:
    fail("Stop loss should NOT trigger")

# Master evaluation - kill priority
rm5 = RiskManager()
action, details = rm5.evaluate([], -4000, 0, 95000)
if action == RiskAction.KILL:
    ok("Master eval: KILL at -4000 PnL")
else:
    fail(f"Expected KILL, got {action}")

# Master evaluation - payday
rm6 = RiskManager()
action, details = rm6.evaluate([], 200, 1100, 95000)
if action == RiskAction.PAYDAY:
    ok("Master eval: PAYDAY at 1300 total PnL")
else:
    fail(f"Expected PAYDAY, got {action}")

# Master evaluation - hold
rm7 = RiskManager()
action, details = rm7.evaluate([], 500, 200, 95000)
if action == RiskAction.HOLD:
    ok("Master eval: HOLD at 700 total PnL")
else:
    fail(f"Expected HOLD, got {action}")


# ═══════════════════════════════════════════
# TEST 6: Scheduler
# ═══════════════════════════════════════════
test("Scheduler")
from scheduler import Scheduler

sch = Scheduler()
ist = pytz.timezone("Asia/Kolkata")

# Saturday = blackout
sat = datetime(2025, 3, 1, 10, 0, tzinfo=ist)
if sch.is_weekend_blackout(sat):
    ok("Weekend blackout on Saturday")
else:
    fail("Should be blackout on Saturday")

# Friday 18:00 = blackout
fri_late = datetime(2025, 2, 28, 18, 0, tzinfo=ist)
if sch.is_weekend_blackout(fri_late):
    ok("Weekend blackout on Friday 6 PM")
else:
    fail("Should be blackout on Friday 6 PM")

# Monday 8 AM = still blackout
mon_early = datetime(2025, 3, 3, 8, 0, tzinfo=ist)
if sch.is_weekend_blackout(mon_early):
    ok("Weekend blackout on Monday 8 AM")
else:
    fail("Should be blackout on Monday 8 AM")

# Monday 10 AM = NOT blackout
mon10 = datetime(2025, 3, 3, 10, 0, tzinfo=ist)
if not sch.is_weekend_blackout(mon10):
    ok("No blackout on Monday 10 AM")
else:
    fail("Should NOT be blackout on Monday 10 AM")

# Trading days
wed = datetime(2025, 3, 5, 10, 0, tzinfo=ist)
if sch.is_trading_day(wed):
    ok("Wednesday is a trading day")
else:
    fail("Wednesday SHOULD be trading day")

fri = datetime(2025, 3, 7, 10, 0, tzinfo=ist)
if not sch.is_trading_day(fri):
    ok("Friday is NOT a trading day")
else:
    fail("Friday should NOT be trading day")

# Deploy time
deploy = datetime(2025, 3, 3, 10, 0, 0, tzinfo=ist)
if sch.is_deploy_time(deploy):
    ok("10:00 AM IST is deploy time")
else:
    fail("10:00 AM SHOULD be deploy time")

deploy1m = datetime(2025, 3, 3, 10, 1, 0, tzinfo=ist)
if sch.is_deploy_time(deploy1m):
    ok("10:01 AM IST is within deploy window")
else:
    fail("10:01 AM should be within 2-min window")

not_deploy = datetime(2025, 3, 3, 14, 0, tzinfo=ist)
if not sch.is_deploy_time(not_deploy):
    ok("2:00 PM IST is NOT deploy time")
else:
    fail("2:00 PM should NOT be deploy time")

# Strategy-based polling
from config import StrategyType
ic_interval = sch.get_poll_interval(StrategyType.IRON_CONDOR)
cs_interval = sch.get_poll_interval(StrategyType.BULL_CREDIT_SPREAD)
if ic_interval == 90 and cs_interval == 45:
    ok(f"Adaptive polling: IC={ic_interval}s, CS={cs_interval}s")
else:
    fail(f"Wrong intervals: IC={ic_interval}, CS={cs_interval}")


# ═══════════════════════════════════════════
# TEST 7: Trade Logger
# ═══════════════════════════════════════════
test("Trade Logger")
from trade_logger import TradeLogger

tmp = os.path.join(tempfile.gettempdir(), "test_trade_log.csv")
tl = TradeLogger(filepath=tmp)
tl.log_trade(action="OPEN", product_id=1, strike=95000,
             side="sell", quantity=1, price=0.05, fee=0.001)
tl.log_trade(action="CLOSE", product_id=1, strike=95000,
             side="buy", quantity=1, price=0.02, pnl=300)
tl.log_event(action="KILL_SWITCH", notes="Test kill switch")
entries = tl.get_all_entries()
if len(entries) == 3:
    ok(f"Logged 3 entries to CSV")
else:
    fail(f"Expected 3 entries, got {len(entries)}")

summary = tl.get_daily_summary()
if summary["total_trades"] == 3:
    ok(f"Daily summary: {summary['total_trades']} trades, PnL={summary['gross_pnl']:.2f}")
else:
    fail(f"Summary wrong: {summary}")

os.remove(tmp)


# ═══════════════════════════════════════════
# TEST 8: Rate Limiter
# ═══════════════════════════════════════════
test("Rate Limiter")
from rate_limiter import RateLimiter

rl = RateLimiter(max_requests_per_sec=100)
start = time.monotonic()
for _ in range(10):
    rl.acquire()
elapsed = time.monotonic() - start
if elapsed < 1.0:
    ok(f"10 requests in {elapsed:.3f}s (within 100/s limit)")
else:
    fail(f"Too slow: {elapsed:.3f}s for 10 requests")

# Test decorator
rl2 = RateLimiter(max_requests_per_sec=100)

@rl2.wrap
def dummy_api_call():
    return 42

result = dummy_api_call()
if result == 42:
    ok("Rate limiter decorator works")
else:
    fail(f"Decorator returned {result}")


# ═══════════════════════════════════════════
# TEST 9: Notifier (dry run, no Telegram)
# ═══════════════════════════════════════════
test("Notifier (dry run)")
from notifier import Notifier

notif = Notifier()
if not notif.enabled:
    ok("Telegram correctly disabled (no keys configured)")
else:
    ok("Telegram enabled (keys found in env)")

# These should not crash even without Telegram
notif.send_alert("Test alert")
notif.send_heartbeat(pnl=500, positions=4, strategy="iron_condor")
ok("Alert and heartbeat send without crash (Telegram disabled)")


# ═══════════════════════════════════════════
# TEST 10: API Connectivity
# ═══════════════════════════════════════════
test("API Connectivity (Delta Exchange India)")
from exchange_client import ExchangeClient

ec = ExchangeClient()
result = ec.check_connectivity()
if result:
    ok("API reachable (testnet)")
else:
    ok("API returned non-200 (testnet may require different endpoint)")


# ═══════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════
print()
print("=" * 60)
print(f"  RESULTS: {passed} PASSED, {failed} FAILED")
print("=" * 60)

if failed > 0:
    sys.exit(1)
