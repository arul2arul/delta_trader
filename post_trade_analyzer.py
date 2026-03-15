"""
Post-Trade Analyzer – The Learning System.
Analyzes trade outcomes, determines root causes for stop-losses, 
and updates the bot's self-correction state.
"""
import json
import os
import logging
from datetime import datetime, timedelta
import pytz
import config
from ai_validator import get_ai_retrospective
from trade_logger import TradeLogger
from notifier import Notifier
from database_manager import DatabaseManager

logger = logging.getLogger("post_trade_analyzer")

HISTORY_FILE = "trade_history_master.json"
STATE_FILE = "bot_state.json"

class PostTradeAnalyzer:
    def __init__(self, exchange, db_manager=None):
        self.exchange = exchange
        self.trade_logger = TradeLogger()
        self.db = db_manager or DatabaseManager()
        self._init_files()

    def _init_files(self):
        if not os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "w") as f:
                json.dump([], f)
        
        if not os.path.exists(STATE_FILE):
            with open(STATE_FILE, "w") as f:
                json.dump({"consecutive_losses": 0, "suspend_trading": False}, f)

    def analyze_trade(self, trade_context, exit_reason, final_spot, final_pnl):
        """
        Runs the full retrospective analysis after a position is closed.
        """
        print(f"\n🔍 Initiating Post-Trade Analysis for exit: {exit_reason}")
        
        # 1. Capture snapshots
        entry_spot = trade_context.get("spot_price", 0)
        spot_change = final_spot - entry_spot
        
        # 2. Stop-Loss Root Cause Analysis
        cause = "N/A"
        if exit_reason == "STOP_LOSS_HIT":
            cause = self._analyze_stop_loss_cause(trade_context, final_spot)
            self._update_consecutive_losses(is_loss=True)
        else:
            self._update_consecutive_losses(is_loss=False)

        # 3. AI Retrospective
        trade_summary = {
            "strategy": trade_context.get("suggested_strategy"),
            "entry_spot": entry_spot,
            "exit_spot": final_spot,
            "net_credit": trade_context.get("net_credit_expected"),
            "realized_pnl": final_pnl,
            "exit_reason": exit_reason,
            "sl_cause": cause,
            "regime": trade_context.get("regime"),
            "atr": trade_context.get("atr_at_entry")
        }
        
        ai_note = get_ai_retrospective(trade_summary)
        trade_summary["ai_retrospective"] = ai_note

        # 4. Structured Logging (Legacy and DB)
        self._log_to_history(trade_summary)
        
        # 4.5 Persist to SQLite
        open_trade = self.db.get_open_trade()
        if open_trade:
            trade_id = open_trade['trade_id']
            self.db.close_trade(trade_id, final_spot, final_pnl)
            
            # Log AI critique
            pre_flight_confidence = trade_context.get("ai_result", {}).get("confidence_score", 10)
            # Simple heuristic for liquidity score: check if spread was narrow
            liq_score = 1.0 if "Liquidity" not in trade_summary.get("sl_cause", "") else 0.5
            
            self.db.add_ai_retrospective(trade_id, pre_flight_confidence, ai_note, liq_score)
            logger.info(f"📁 Trade record closed in SQLite | ID: {trade_id}")

        # 5. Check Suspension
        self._check_suspension()

        print(f"📊 Analysis Complete. AI Note: {ai_note}")
        return trade_summary

    def _analyze_stop_loss_cause(self, trade_context, final_spot):
        """
        Identifies 'Wick', 'Trend Break', or 'IV Spike'.
        """
        try:
            # Fetch last 5 minutes of 1m candles
            ist = pytz.timezone(config.TIMEZONE)
            end_time = int(datetime.now(ist).timestamp())
            start_time = end_time - 300 # 5 minutes
            
            # Using BTC spot symbol for trend analysis
            candles = self.exchange.get_candles(config.UNDERLYING_SYMBOL, "1m", start_time, end_time)
            if not candles:
                return "Unknown (No candle data)"

            prices = [float(c[4]) for c in candles] # Closing prices
            max_p = max(prices)
            min_p = min(prices)
            current_p = prices[-1]

            # Logic:
            # If current price reversed back towards entry vs the extreme
            entry_spot = trade_context.get("spot_price", 0)
            
            # Simple Wick detection: price touched a level and moved away > 0.3% in 5m
            if abs(current_p - entry_spot) < abs(max_p - entry_spot) * 0.5:
                return "Wick (Slippage/Flash Movement)"
            
            # Trend Break: Price stayed beyond the strike
            # (Note: In a more complex version we'd check against short strikes)
            if abs(current_p - entry_spot) > trade_context.get("atr_at_entry", 300) * 0.8:
                return "Trend Break (Regime Detection Failed)"
                
            return "IV Spike (Volatility Explosion)"
        except Exception as e:
            logger.error(f"Error in SL cause analysis: {e}")
            return "Analysis Error"

    def _update_consecutive_losses(self, is_loss):
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        
        if is_loss:
            state["consecutive_losses"] += 1
        else:
            state["consecutive_losses"] = 0
            
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)

    def _check_suspension(self):
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        
        if state["consecutive_losses"] >= 3:
            state["suspend_trading"] = True
            with open(STATE_FILE, "w") as f:
                json.dump(state, f)
            print("🚨 CRITICAL: 3 Consecutive Losses. Trading SUSPENDED.")
            
            Notifier().send_error_alert(
                "🚨 *BOT SUSPENDED* 🚨\n\n"
                "The strategy has hit 3 consecutive stop-losses.\n"
                "Trading is now PAUSED for manual review.\n\n"
                "Please check `trade_history_master.json` for analysis."
            )

    def _log_to_history(self, summary):
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
        
        summary["timestamp"] = datetime.now().isoformat()
        history.append(summary)
        
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=4)
