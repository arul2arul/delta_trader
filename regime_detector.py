"""
Regime Detector – Classifies market as Sideways, Bullish, or Bearish.
The "Brain" that decides which strategy to deploy.
"""

import logging
import pandas as pd

import config
from config import Regime

logger = logging.getLogger("regime_detector")


def compute_intraday_sentiment(
    funding_rate: float,
    ob_imbalance: float,
    supertrend_dir: int,
) -> tuple[int, str]:
    """
    Build a live 0-100 sentiment score from three signals already fetched
    during the main loop — no external API call required (Task 2).

    Scoring (all additive, centred on 50 = neutral):
      • Funding rate: strongly positive → bullish (+25); strongly negative → fearful (-25)
      • OB imbalance: heavy buy side → bullish (+15); heavy sell → bearful (-15)
      • 15m Supertrend: bullish (+10), bearish (-10)

    Returns (score, classification) where classification mirrors the old F&G labels
    so callers need no changes.
    """
    score = 50  # neutral baseline

    # Funding rate component
    if funding_rate > 0.0005:
        score += 25        # market overheated / greedy
    elif funding_rate > 0.0001:
        score += 10
    elif funding_rate < -0.0005:
        score -= 25        # extreme fear / short squeeze territory
    elif funding_rate < -0.0001:
        score -= 10

    # Order-book imbalance component (-1 to +1 scale)
    score += int(ob_imbalance * 15)

    # 15m Supertrend component
    if supertrend_dir > 0:
        score += 10
    else:
        score -= 10

    score = max(0, min(100, score))  # clamp to [0, 100]

    if score <= 25:
        classification = "Extreme Fear"
    elif score <= 45:
        classification = "Fear"
    elif score <= 55:
        classification = "Neutral"
    elif score <= 75:
        classification = "Greed"
    else:
        classification = "Extreme Greed"

    logger.info(
        f"Intraday Sentiment Score: {score} ({classification}) | "
        f"funding={funding_rate*100:.4f}%, OBI={ob_imbalance:.2f}, "
        f"supertrend={'bullish' if supertrend_dir > 0 else 'bearish'}"
    )
    return score, classification


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


def get_strategy_for_regime(
    regime: Regime,
    funding_rate: float = 0.0,
    ob_imbalance: float = 0.0,
    supertrend_dir: int = 1,
) -> config.StrategyType:
    """
    Map regime to strategy using live intraday sentiment (Task 2).
    Replaces the once-daily Fear & Greed index with a real-time composite
    built from funding rate, order-book imbalance, and 15m Supertrend.
    """
    sentiment_score, sentiment_class = compute_intraday_sentiment(
        funding_rate, ob_imbalance, supertrend_dir
    )

    strategy = config.StrategyType.IRON_CONDOR

    if regime == Regime.SIDEWAYS:
        strategy = config.StrategyType.IRON_CONDOR
    elif regime == Regime.BULLISH:
        if sentiment_score <= 25:
            logger.warning(
                f"⚠️ {sentiment_class} detected (score={sentiment_score}). "
                "Bull Put Spreads too risky given intraday sentiment. Downgrading to Iron Condor."
            )
            strategy = config.StrategyType.IRON_CONDOR
        else:
            strategy = config.StrategyType.BULL_CREDIT_SPREAD
    elif regime == Regime.BEARISH:
        if sentiment_score <= 25:
            logger.warning(
                f"⚠️ {sentiment_class} detected (score={sentiment_score}). "
                "Downside volatility too erratic for Bear Call Spreads. Downgrading to Iron Condor."
            )
            strategy = config.StrategyType.IRON_CONDOR
        else:
            strategy = config.StrategyType.BEAR_CREDIT_SPREAD

    logger.info(
        f"Strategy: {strategy.value} | Regime: {regime.value} | "
        f"Sentiment: {sentiment_score} ({sentiment_class})"
    )
    return strategy


def confirm_regime(regime_1h: Regime, regime_15m: Regime) -> Regime:
    """
    Multi-timeframe regime confirmation (Task 5).
    Requires both timeframes to agree before allowing a directional spread.
    Any disagreement defaults to SIDEWAYS (Iron Condor) — the most conservative choice.

    Agreement matrix:
      1H=SIDEWAYS,  15m=SIDEWAYS  → SIDEWAYS  ✓
      1H=BULLISH,   15m=BULLISH   → BULLISH   ✓
      1H=BEARISH,   15m=BEARISH   → BEARISH   ✓
      1H=SIDEWAYS,  15m=TRENDING  → SIDEWAYS  (15m hasn't settled yet)
      1H=BULLISH,   15m=SIDEWAYS  → SIDEWAYS  (15m not confirming trend)
      1H=BULLISH,   15m=BEARISH   → SIDEWAYS  (outright disagreement)
      any other mismatch           → SIDEWAYS
    """
    if regime_1h == regime_15m:
        logger.info(f"✅ Multi-TF confirmation: both 1H and 15m agree → {regime_1h.value.upper()}")
        return regime_1h

    logger.warning(
        f"⚠️ Multi-TF conflict: 1H={regime_1h.value.upper()} vs 15m={regime_15m.value.upper()}. "
        "Defaulting to SIDEWAYS (Iron Condor) — no directional spread without both timeframes aligned."
    )
    return Regime.SIDEWAYS

