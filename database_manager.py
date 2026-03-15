import sqlite3
import os
import logging
from datetime import datetime, timedelta
import config

logger = logging.getLogger("database_manager")

DB_PATH = config.DB_PATH
DB_DIR = config.DATA_DIR

class DatabaseManager:
    def __init__(self):
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(DB_PATH)

    def _init_db(self):
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Trades Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    strategy TEXT,
                    total_lots INTEGER,
                    entry_spot REAL,
                    exit_spot REAL,
                    net_premium_usd REAL,
                    final_pnl_inr REAL,
                    status TEXT DEFAULT 'PENDING'
                )
            """)
            
            # 2. Basket_Legs Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS basket_legs (
                    leg_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER,
                    product_id INTEGER,
                    strike REAL,
                    side TEXT,
                    fill_price REAL,
                    slippage REAL,
                    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
                )
            """)
            
            # 3. AI_Retrospectives Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_retrospectives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER,
                    pre_flight_confidence INTEGER,
                    post_trade_critique TEXT,
                    liquidity_score REAL,
                    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
                )
            """)
            conn.commit()
            logger.info(f"Database initialized at {DB_PATH}")

    def create_trade(self, strategy, total_lots, entry_spot, net_premium_usd):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (strategy, total_lots, entry_spot, net_premium_usd, status)
                VALUES (?, ?, ?, ?, 'PENDING')
            """, (strategy, total_lots, entry_spot, net_premium_usd))
            return cursor.lastrowid

    def add_leg(self, trade_id, product_id, strike, side, fill_price, slippage=0.0):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO basket_legs (trade_id, product_id, strike, side, fill_price, slippage)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (trade_id, product_id, strike, side, fill_price, slippage))

    def update_trade_status(self, trade_id, status):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE trades SET status = ? WHERE trade_id = ?", (status, trade_id))

    def close_trade(self, trade_id, exit_spot, final_pnl_inr):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE trades 
                SET exit_spot = ?, final_pnl_inr = ?, status = 'CLOSED' 
                WHERE trade_id = ?
            """, (exit_spot, final_pnl_inr, trade_id))

    def add_ai_retrospective(self, trade_id, confidence, critique, liquidity_score):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ai_retrospectives (trade_id, pre_flight_confidence, post_trade_critique, liquidity_score)
                VALUES (?, ?, ?, ?)
            """, (trade_id, confidence, critique, liquidity_score))

    def get_open_trade(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY timestamp DESC LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_recent_performance(self, limit=5):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_7day_win_rate(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN final_pnl_inr > 0 THEN 1 ELSE 0 END) as wins
                FROM trades 
                WHERE status = 'CLOSED' AND timestamp > ?
            """, (seven_days_ago,))
            total, wins = cursor.fetchone()
            if not total:
                return 0.0
            return (wins / total) * 100
