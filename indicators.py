"""
Technical Indicators – RSI, EMA, ADX via pandas_ta.
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
        # pandas_ta returns ADX_14, DMP_14, DMN_14
        adx_col = f"ADX_{period}"
        if adx_col in adx_df.columns:
            df["adx"] = adx_df[adx_col]
            logger.info(f"ADX({period}) computed. Latest: {df['adx'].iloc[-1]:.2f}")
        else:
            # Try first column
            df["adx"] = adx_df.iloc[:, 0]
            logger.info(f"ADX({period}) computed (fallback column)")
    else:
        logger.warning("ADX computation returned empty result")

    return df


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all indicators (RSI, EMA, ADX) in one pass.
    Returns augmented DataFrame.
    """
    df = compute_rsi(df)
    df = compute_ema(df)
    df = compute_adx(df)

    logger.info(
        f"All indicators computed. "
        f"RSI={df['rsi'].iloc[-1]:.1f}, "
        f"EMA={df.get(f'ema_{config.EMA_PERIOD}', pd.Series([0])).iloc[-1]:.1f}, "
        f"ADX={df.get('adx', pd.Series([0])).iloc[-1]:.1f}"
    )
    return df
