"""
Technical Indicators – RSI, EMA, ADX, VWAP, ATR via pandas_ta.
Pure functions that accept DataFrames and return augmented DataFrames.
"""

import logging
import pandas as pd
import pandas_ta as ta

import config

logger = logging.getLogger("indicators")


def compute_rsi(df: pd.DataFrame, period: int = config.RSI_PERIOD) -> pd.DataFrame:
    """
    Compute RSI (Relative Strength Index).
    Adds 'rsi' column to the DataFrame.
    """
    if df.empty or "close" not in df.columns:
        logger.warning("Cannot compute RSI: empty DataFrame or missing 'close' column")
        return df

    df = df.copy()
    df["rsi"] = ta.rsi(df["close"], length=period)
    logger.info(f"RSI({period}) computed. Latest: {df['rsi'].iloc[-1]:.2f}")
    return df


def compute_ema(df: pd.DataFrame, period: int = config.EMA_PERIOD) -> pd.DataFrame:
    """
    Compute EMA (Exponential Moving Average).
    Adds 'ema_{period}' column to the DataFrame.
    """
    if df.empty or "close" not in df.columns:
        logger.warning("Cannot compute EMA: empty DataFrame or missing 'close' column")
        return df

    df = df.copy()
    col_name = f"ema_{period}"
    df[col_name] = ta.ema(df["close"], length=period)
    logger.info(f"EMA({period}) computed. Latest: {df[col_name].iloc[-1]:.2f}")
    return df


def compute_adx(df: pd.DataFrame, period: int = config.ADX_PERIOD) -> pd.DataFrame:
    """
    Compute ADX (Average Directional Index).
    Adds 'adx' column to the DataFrame.
    Requires 'high', 'low', 'close' columns.
    """
    if df.empty:
        logger.warning("Cannot compute ADX: empty DataFrame")
        return df

    required = ["high", "low", "close"]
    for col in required:
        if col not in df.columns:
            logger.warning(f"Cannot compute ADX: missing '{col}' column")
            return df

    df = df.copy()
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=period)
    if adx_df is not None and not adx_df.empty:
        adx_col = f"ADX_{period}"
        if adx_col in adx_df.columns:
            df["adx"] = adx_df[adx_col]
            logger.info(f"ADX({period}) computed. Latest: {df['adx'].iloc[-1]:.2f}")
        else:
            df["adx"] = adx_df.iloc[:, 0]
            logger.info(f"ADX({period}) computed (fallback column)")
    else:
        logger.warning("ADX computation returned empty result")

    return df


def compute_atr(df: pd.DataFrame, period: int = getattr(config, "ATR_PERIOD", 14)) -> pd.DataFrame:
    """
    Compute ATR (Average True Range).
    Adds 'atr' column to the DataFrame.
    """
    if df.empty:
        logger.warning("Cannot compute ATR: empty DataFrame")
        return df

    required = ["high", "low", "close"]
    for col in required:
        if col not in df.columns:
            logger.warning(f"Cannot compute ATR: missing '{col}' column")
            return df

    df = df.copy()
    atr_val = ta.atr(df["high"], df["low"], df["close"], length=period)
    if atr_val is not None and not atr_val.empty:
        df["atr"] = atr_val
        logger.info(f"ATR({period}) computed. Latest: {df['atr'].iloc[-1]:.2f}")
    else:
        df["atr"] = pd.Series([0.0] * len(df))
        logger.warning("ATR computation returned empty result")

    return df


def compute_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute VWAP (Volume Weighted Average Price).
    Adds 'vwap' column to the DataFrame.
    Using pandas_ta vwap requires a datetime index typically, so we set it temporarily if needed.
    """
    if df.empty:
        logger.warning("Cannot compute VWAP: empty DataFrame")
        return df

    required = ["high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            logger.warning(f"Cannot compute VWAP: missing '{col}' column")
            return df

    df = df.copy()

    # pandas_ta vwap uses index, let's ensure index is datetime if possible
    temp_df = df.copy()
    if "timestamp" in temp_df.columns:
        temp_df.index = pd.to_datetime(temp_df["timestamp"], unit='s' if temp_df["timestamp"].dtype in ['int64', 'float64'] else None)
    
    try:
        vwap_val = ta.vwap(temp_df["high"], temp_df["low"], temp_df["close"], temp_df["volume"])
        if vwap_val is not None and not vwap_val.empty:
            df["vwap"] = vwap_val.values
            logger.info(f"VWAP computed. Latest: {df['vwap'].iloc[-1]:.2f}")
        else:
            df["vwap"] = pd.Series([df["close"].iloc[-1]] * len(df))
            logger.warning("VWAP empty, fallback to close price")
    except Exception as e:
        logger.error(f"VWAP calculation failed: {e}. Fallback to close price.")
        df["vwap"] = df["close"]

    return df


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all indicators (RSI, EMA, ADX, ATR, VWAP) in one pass.
    Returns augmented DataFrame.
    """
    df = compute_rsi(df)
    df = compute_ema(df)
    df = compute_adx(df)
    df = compute_atr(df)
    df = compute_vwap(df)

    logger.info(
        f"All indicators computed. "
        f"RSI={df['rsi'].iloc[-1]:.1f}, "
        f"EMA={df.get(f'ema_{config.EMA_PERIOD}', pd.Series([0])).iloc[-1]:.1f}, "
        f"ADX={df.get('adx', pd.Series([0])).iloc[-1]:.1f}, "
        f"ATR={df.get('atr', pd.Series([0])).iloc[-1]:.1f}, "
        f"VWAP={df.get('vwap', pd.Series([0])).iloc[-1]:.1f}"
    )
    return df

