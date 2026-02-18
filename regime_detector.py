"""
Regime Detector – Classifies market as Sideways, Bullish, or Bearish.
The "Brain" that decides which strategy to deploy.
"""

import logging
import pandas as pd

import config
from config import Regime

logger = logging.getLogger("regime_detector")


def detect_regime(df: pd.DataFrame) -> Regime:
    """
    Analyze indicators to determine the current market regime.

    Rules:
    - SIDEWAYS: RSI ∈ [45, 55] AND ADX < 25
    - BULLISH:  Price > 20-EMA (and not sideways)
    - BEARISH:  Price < 20-EMA (and not sideways)

    Args:
        df: DataFrame with 'close', 'rsi', 'ema_20', 'adx' columns.

    Returns:
        Regime enum value.
    """
    if df.empty or len(df) < 2:
        logger.warning("Insufficient data for regime detection, defaulting to SIDEWAYS")
        return Regime.SIDEWAYS

    latest = df.iloc[-1]

    rsi = latest.get("rsi", 50)
    adx = latest.get("adx", 20)
    close = latest.get("close", 0)
    ema_col = f"ema_{config.EMA_PERIOD}"
    ema = latest.get(ema_col, close)

    # Handle NaN values
    if pd.isna(rsi):
        rsi = 50
    if pd.isna(adx):
        adx = 20
    if pd.isna(ema):
        ema = close

    logger.info(
        f"Regime inputs: RSI={rsi:.1f}, ADX={adx:.1f}, "
        f"Close={close:.2f}, EMA({config.EMA_PERIOD})={ema:.2f}"
    )

    # ── The Sideways Guard ──
    is_rsi_neutral = config.RSI_SIDEWAYS_LOW <= rsi <= config.RSI_SIDEWAYS_HIGH
    is_adx_low = adx < config.ADX_THRESHOLD

    if is_rsi_neutral and is_adx_low:
        logger.info(
            f"🔲 SIDEWAYS regime detected "
            f"(RSI {rsi:.1f} in [{config.RSI_SIDEWAYS_LOW},{config.RSI_SIDEWAYS_HIGH}], "
            f"ADX {adx:.1f} < {config.ADX_THRESHOLD})"
        )
        return Regime.SIDEWAYS

    # ── The Trend Sentinel ──
    if close > ema:
        logger.info(
            f"🟢 BULLISH regime detected "
            f"(Close {close:.2f} > EMA {ema:.2f})"
        )
        return Regime.BULLISH
    else:
        logger.info(
            f"🔴 BEARISH regime detected "
            f"(Close {close:.2f} < EMA {ema:.2f})"
        )
        return Regime.BEARISH


def check_volatility(iv_rank: float) -> bool:
    """
    The Volatility Check: If IV Rank > 70%, use wider wings.

    Args:
        iv_rank: IV Rank percentage (0-100).

    Returns:
        True if wide wings should be used.
    """
    wide_wings = iv_rank > config.IV_RANK_THRESHOLD
    if wide_wings:
        logger.info(
            f"⚡ High volatility detected (IV Rank: {iv_rank:.1f}% > "
            f"{config.IV_RANK_THRESHOLD}%). Using WIDER WINGS."
        )
    else:
        logger.info(
            f"📊 Normal volatility (IV Rank: {iv_rank:.1f}% ≤ "
            f"{config.IV_RANK_THRESHOLD}%). Standard wing width."
        )
    return wide_wings


def get_strategy_for_regime(regime: Regime) -> str:
    """
    Map regime to strategy type.

    Returns:
        Strategy name string.
    """
    strategy_map = {
        Regime.SIDEWAYS: config.StrategyType.IRON_CONDOR,
        Regime.BULLISH: config.StrategyType.BULL_CREDIT_SPREAD,
        Regime.BEARISH: config.StrategyType.BEAR_CREDIT_SPREAD,
    }
    strategy = strategy_map.get(regime, config.StrategyType.IRON_CONDOR)
    logger.info(f"Strategy selected for {regime.value}: {strategy.value}")
    return strategy
