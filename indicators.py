"""
Technical Indicators – RSI, EMA, ADX, VWAP, ATR via `ta` library.
Pure functions that accept DataFrames and return augmented DataFrames.
"""

import logging
import pandas as pd
import ta

import config

logger = logging.getLogger("indicators")


def compute_rsi(df: pd.DataFrame, period: int = config.RSI_PERIOD) -> pd.DataFrame:
    if df.empty or "close" not in df.columns:
        logger.warning("Cannot compute RSI: empty DataFrame or missing 'close' column")
        return df

    df = df.copy()
    rsi_ind = ta.momentum.RSIIndicator(df["close"], window=period)
    df["rsi"] = rsi_ind.rsi()
    logger.info(f"RSI({period}) computed. Latest: {df['rsi'].iloc[-1]:.2f}")
    return df


def compute_ema(df: pd.DataFrame, period: int = config.EMA_PERIOD) -> pd.DataFrame:
    if df.empty or "close" not in df.columns:
        logger.warning("Cannot compute EMA: empty DataFrame or missing 'close' column")
        return df

    df = df.copy()
    col_name = f"ema_{period}"
    ema_ind = ta.trend.EMAIndicator(df["close"], window=period)
    df[col_name] = ema_ind.ema_indicator()
    logger.info(f"EMA({period}) computed. Latest: {df[col_name].iloc[-1]:.2f}")
    return df


def compute_adx(df: pd.DataFrame, period: int = config.ADX_PERIOD) -> pd.DataFrame:
    if df.empty:
        logger.warning("Cannot compute ADX: empty DataFrame")
        return df

    required = ["high", "low", "close"]
    for col in required:
        if col not in df.columns:
            logger.warning(f"Cannot compute ADX: missing '{col}' column")
            return df

    df = df.copy()
    try:
        adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=period)
        df["adx"] = adx_ind.adx()
        logger.info(f"ADX({period}) computed. Latest: {df['adx'].iloc[-1]:.2f}")
    except Exception as e:
        logger.error(f"ADX computation failed: {e}")
        df["adx"] = 0.0

    return df


def compute_atr(df: pd.DataFrame, period: int = getattr(config, "ATR_PERIOD", 14)) -> pd.DataFrame:
    if df.empty:
        logger.warning("Cannot compute ATR: empty DataFrame")
        return df

    required = ["high", "low", "close"]
    for col in required:
        if col not in df.columns:
            logger.warning(f"Cannot compute ATR: missing '{col}' column")
            return df

    df = df.copy()
    try:
        atr_ind = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=period)
        df["atr"] = atr_ind.average_true_range()
        logger.info(f"ATR({period}) computed. Latest: {df['atr'].iloc[-1]:.2f}")
    except Exception as e:
        logger.error(f"ATR computation failed: {e}")
        df["atr"] = 0.0

    return df


def compute_vwap(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        logger.warning("Cannot compute VWAP: empty DataFrame")
        return df

    required = ["high", "low", "close", "volume", "timestamp"]
    for col in required:
        if col not in df.columns:
            logger.warning(f"Cannot compute VWAP: missing '{col}' column")
            return df

    df = df.copy()
    try:
        # Standard anchored VWAP by day
        temp_df = df.copy()
        temp_df['date'] = pd.to_datetime(temp_df["timestamp"], unit='s').dt.date
        
        tp = (temp_df['high'] + temp_df['low'] + temp_df['close']) / 3
        vol = temp_df['volume']
        
        temp_df['vwap'] = (tp * vol).groupby(temp_df['date']).cumsum() / vol.groupby(temp_df['date']).cumsum()
        df["vwap"] = temp_df['vwap'].values
        
        logger.info(f"VWAP computed. Latest: {df['vwap'].iloc[-1]:.2f}")
    except Exception as e:
        logger.error(f"VWAP calculation failed: {e}. Fallback to close price.")
        df["vwap"] = df["close"]

    return df


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    if df.empty:
        return df

    required = ["high", "low", "close"]
    for col in required:
        if col not in df.columns:
            return df

    df = df.copy()
    try:
        atr_ind = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=period)
        atr = atr_ind.average_true_range()
        
        hl2 = (df["high"] + df["low"]) / 2
        
        upperband = hl2 + (multiplier * atr)
        lowerband = hl2 - (multiplier * atr)
        
        supertrend_dir = [1] * len(df)
        
        for i in range(1, len(df)):
            if df["close"].iloc[i-1] <= upperband.iloc[i-1]:
                upperband.iloc[i] = min(upperband.iloc[i], upperband.iloc[i-1])
            if df["close"].iloc[i-1] >= lowerband.iloc[i-1]:
                lowerband.iloc[i] = max(lowerband.iloc[i], lowerband.iloc[i-1])
                
            if df["close"].iloc[i] > upperband.iloc[i-1]:
                supertrend_dir[i] = 1
            elif df["close"].iloc[i] < lowerband.iloc[i-1]:
                supertrend_dir[i] = -1
            else:
                supertrend_dir[i] = supertrend_dir[i-1]
                
        df["supertrend_dir"] = pd.Series(supertrend_dir, index=df.index)
        logger.info("Supertrend computed natively.")
    except Exception as e:
        logger.error(f"Supertrend calculation failed: {e}")
        df["supertrend_dir"] = 1
        
    return df


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    df = compute_rsi(df)
    df = compute_ema(df, period=9)
    df = compute_ema(df)
    df = compute_adx(df)
    df = compute_atr(df)
    df = compute_vwap(df)
    df = compute_supertrend(df)
    
    logger.info("All indicators computed successfully.")
    return df
