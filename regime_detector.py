"""
Regime Detector – Classifies market as Sideways, Bullish, or Bearish.
The "Brain" that decides which strategy to deploy.
"""

import logging
import requests
import pandas as pd

import config
from config import Regime

logger = logging.getLogger("regime_detector")


def get_fear_and_greed() -> dict:
    """Fetch the Crypto Fear & Greed index."""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0]
    except Exception as e:
        logger.error(f"Failed to fetch Fear & Greed Index: {e}")
    
    return {"value": "50", "value_classification": "Neutral"}


def detect_regime(df: pd.DataFrame) -> Regime:
    """
    Analyze indicators to determine the current market regime.

    Rules:
    - SIDEWAYS: (RSI ∈ [45, 55] AND ADX < 25 AND ATR is declining) OR (Price is far from VWAP -> Mean Reversion)
    - BULLISH:  Price > 20-EMA (and not sideways)
    - BEARISH:  Price < 20-EMA (and not sideways)
    """
    if df.empty or len(df) < 3:
        logger.warning("Insufficient data for regime detection, defaulting to SIDEWAYS")
        return Regime.SIDEWAYS

    latest = df.iloc[-1]

    rsi = latest.get("rsi", 50)
    adx = latest.get("adx", 20)
    close = latest.get("close", 0)
    ema_col = f"ema_{config.EMA_PERIOD}"
    ema = latest.get(ema_col, close)

    # Handle NaN values
    if pd.isna(rsi): rsi = 50
    if pd.isna(adx): adx = 20
    if pd.isna(ema): ema = close

    logger.info(
        f"Regime inputs: RSI={rsi:.1f}, ADX={adx:.1f}, "
        f"Close={close:.2f}, EMA({config.EMA_PERIOD})={ema:.2f}"
    )

    # ── Sidebar Checks: VWAP and ATR ──
    atr_declining = False
    atr_latest = latest.get("atr", 0)
    atr_prev_mean = df["atr"].iloc[-4:-1].mean()
    if atr_latest < atr_prev_mean:
        atr_declining = True
    
    far_from_vwap = False
    vwap = latest.get("vwap", close)
    if vwap > 0:
        dist_vwap = abs(close - vwap) / vwap
        if dist_vwap > 0.015:  # e.g., > 1.5% away = expectation of mean reversion
            far_from_vwap = True
            logger.info(f"Price is {(dist_vwap*100):.2f}% away from VWAP. Expecting Mean Reversion.")

    # ── The Sideways Guard ──
    is_rsi_neutral = config.RSI_SIDEWAYS_LOW <= rsi <= config.RSI_SIDEWAYS_HIGH
    is_adx_low = adx < getattr(config, "ADX_THRESHOLD", 25)

    if (is_rsi_neutral and is_adx_low and atr_declining) or far_from_vwap:
        logger.info(
            f"🔲 SIDEWAYS regime detected "
            f"(ATR Declining={atr_declining}, Far from VWAP={far_from_vwap}, "
            f"RSI={rsi:.1f}, ADX={adx:.1f})"
        )
        return Regime.SIDEWAYS
    
    if is_rsi_neutral and is_adx_low and not atr_declining:
        logger.warning("RSI and ADX signal sideways, but ATR is rising! Breakout risk elevated. Falling back to underlying trend.")

    # ── The Trend Sentinel ──
    if close > ema:
        logger.info(f"🟢 BULLISH regime detected (Close {close:.2f} > EMA {ema:.2f})")
        return Regime.BULLISH
    else:
        logger.info(f"🔴 BEARISH regime detected (Close {close:.2f} < EMA {ema:.2f})")
        return Regime.BEARISH


def check_volatility(iv_rank: float) -> bool:
    """The Volatility Check: If IV Rank > 70%, use wider wings."""
    thresh = getattr(config, "IV_RANK_THRESHOLD", 70)
    wide_wings = iv_rank > thresh
    if wide_wings:
        logger.info(f"⚡ High volatility detected (IV Rank: {iv_rank:.1f}% > {thresh}%). Using WIDER WINGS.")
    else:
        logger.info(f"📊 Normal volatility (IV Rank: {iv_rank:.1f}% ≤ {thresh}%). Standard wing width.")
    return wide_wings


def get_strategy_for_regime(regime: Regime) -> config.StrategyType:
    """Map regime to strategy type, heavily weighted by the Fear & Greed Index."""
    fng = get_fear_and_greed()
    fear_val = int(fng.get("value", 50))
    fear_class = fng.get("value_classification", "Neutral")
    logger.info(f"Crypto Fear & Greed Index: {fear_val} ({fear_class})")

    strategy = config.StrategyType.IRON_CONDOR

    if regime == Regime.SIDEWAYS:
        strategy = config.StrategyType.IRON_CONDOR
    elif regime == Regime.BULLISH:
        if fear_val <= 25:
            logger.warning("⚠️ Extreme Fear detected. Bull Put Spreads are too risky due to rapid downside velocity. Downgrading to Iron Condor.")
            strategy = config.StrategyType.IRON_CONDOR
        else:
            strategy = config.StrategyType.BULL_CREDIT_SPREAD
    elif regime == Regime.BEARISH:
        if fear_val <= 25:
            logger.warning("⚠️ Extreme Fear detected. Downside volatility is too erratic for Bear Call Spreads. Downgrading to Iron Condor.")
            strategy = config.StrategyType.IRON_CONDOR
        else:
            strategy = config.StrategyType.BEAR_CREDIT_SPREAD

    logger.info(f"Strategy selected for {regime.value} with Fear Factor {fear_val}: {strategy.value}")
    return strategy

