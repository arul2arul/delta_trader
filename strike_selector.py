"""
Strike Selector – Finds option strikes closest to target delta values.
"""

import logging
from typing import Optional

import config
from config import Strike, OptionType

logger = logging.getLogger("strike_selector")


def select_by_delta(
    chain: list[dict],
    target_delta: float,
    option_type: str,
) -> Optional[Strike]:
    """
    Find the strike in the option chain closest to the target delta.

    Args:
        chain: List of option dicts with 'delta', 'strike_price', etc.
        target_delta: Absolute delta value to target (e.g., 0.10).
        option_type: "call_options" or "put_options".

    Returns:
        Strike namedtuple or None if no suitable strike found.
    """
    # Filter chain by option type
    filtered = [
        c for c in chain
        if c.get("contract_type", "").lower() == option_type.lower()
    ]

    if not filtered:
        logger.warning(f"No {option_type} contracts found in chain")
        return None

    # For puts, delta is negative; we compare absolute values
    best = None
    best_diff = float("inf")

    for opt in filtered:
        opt_delta = abs(float(opt.get("delta", 0)))
        diff = abs(opt_delta - target_delta)

        if diff < best_diff:
            best_diff = diff
            best = opt

    if best is None:
        logger.warning(
            f"No strike found for delta={target_delta} in {option_type}"
        )
        return None

    strike = Strike(
        product_id=int(best.get("product_id", 0)),
        strike_price=float(best.get("strike_price", 0)),
        delta=float(best.get("delta", 0)),
        premium=float(best.get("mark_price", 0)),
        option_type=option_type,
        symbol=best.get("symbol", ""),
    )

    logger.info(
        f"Selected {option_type} strike: "
        f"K={strike.strike_price:.0f}, "
        f"Δ={strike.delta:.4f} (target={target_delta}), "
        f"Premium={strike.premium:.4f}, "
        f"ID={strike.product_id}"
    )
    return strike


def select_iron_condor_strikes(
    chain: list[dict],
    wide_wings: bool = False,
) -> dict:
    """
    Select all 4 strikes for an Iron Condor.

    Returns dict with keys: short_call, short_put, long_call, long_put.
    Each value is a Strike or None.
    """
    short_delta = config.SHORT_DELTA
    long_delta = config.LONG_DELTA

    # If wide wings (high IV), widen the gap
    if wide_wings:
        long_delta = max(0.02, long_delta - 0.02)  # Push wings further OTM
        logger.info(
            f"Wide wings mode: short_delta={short_delta}, long_delta={long_delta}"
        )

    strikes = {
        "short_call": select_by_delta(chain, short_delta, OptionType.CALL.value),
        "short_put": select_by_delta(chain, short_delta, OptionType.PUT.value),
        "long_call": select_by_delta(chain, long_delta, OptionType.CALL.value),
        "long_put": select_by_delta(chain, long_delta, OptionType.PUT.value),
    }

    # Validate: long wings must be further OTM than short legs
    if strikes["short_call"] and strikes["long_call"]:
        if strikes["long_call"].strike_price <= strikes["short_call"].strike_price:
            logger.warning(
                "Long call wing is not further OTM than short call. "
                "Adjusting to next available strike."
            )
    if strikes["short_put"] and strikes["long_put"]:
        if strikes["long_put"].strike_price >= strikes["short_put"].strike_price:
            logger.warning(
                "Long put wing is not further OTM than short put. "
                "Adjusting to next available strike."
            )

    valid_count = sum(1 for v in strikes.values() if v is not None)
    logger.info(f"Iron Condor strike selection: {valid_count}/4 legs found")
    return strikes


def select_credit_spread_strikes(
    chain: list[dict],
    direction: str,
    wide_wings: bool = False,
) -> dict:
    """
    Select strikes for a directional Credit Spread.

    Args:
        direction: "bullish" or "bearish"
        wide_wings: Use wider gap between legs

    Returns dict with keys: short_leg, long_leg.
    """
    short_delta = config.DIRECTIONAL_DELTA
    long_delta = config.WING_DELTA

    if wide_wings:
        long_delta = max(0.02, long_delta - 0.02)

    if direction == "bullish":
        # Bull Put Spread: Sell higher-delta put, Buy lower-delta put
        strikes = {
            "short_leg": select_by_delta(chain, short_delta, OptionType.PUT.value),
            "long_leg": select_by_delta(chain, long_delta, OptionType.PUT.value),
        }
    else:
        # Bear Call Spread: Sell higher-delta call, Buy lower-delta call
        strikes = {
            "short_leg": select_by_delta(chain, short_delta, OptionType.CALL.value),
            "long_leg": select_by_delta(chain, long_delta, OptionType.CALL.value),
        }

    valid_count = sum(1 for v in strikes.values() if v is not None)
    logger.info(
        f"{direction.title()} credit spread strike selection: "
        f"{valid_count}/2 legs found"
    )
    return strikes
