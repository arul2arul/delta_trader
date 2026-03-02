"""
Strike Selector – Finds option strikes closest to target delta values and incorporates Greek targeting.
"""

import logging
from typing import Optional

import config
from config import Strike, OptionType

logger = logging.getLogger("strike_selector")


def select_best_strike(
    chain: list[dict],
    max_delta: float,
    option_type: str,
    spot_price: float = 0.0,
    min_distance: float = 0.0,
    maximize_theta: bool = True
) -> Optional[Strike]:
    """
    Find the best strike prioritizing minimum distance, strictly under max delta,
    and optionally maximizing Theta (our "Daily Wage").
    """
    filtered = [
        c for c in chain
        if c.get("contract_type", "").lower() == option_type.lower()
    ]

    if not filtered:
        logger.warning(f"No {option_type} contracts found in chain")
        return None

    candidates = []
    for opt in filtered:
        opt_delta = abs(float(opt.get("delta", 0)))
        strike_price = float(opt.get("strike_price", 0))

        # 1. Enforce minimum distance from spot price (prioritized over delta)
        if min_distance > 0 and spot_price > 0:
            if abs(strike_price - spot_price) < min_distance:
                continue
                
        # 2. Enforce strict maximum delta risk (e.g. 0.15)
        if opt_delta > max_delta:
            continue
            
        # 3. Liquidity Filter (Slippage Guard)
        bid = float(opt.get("best_bid", 0))
        ask = float(opt.get("best_ask", 0))
        mark = float(opt.get("mark_price", 0))
        if mark > 0 and (ask - bid) / mark > 0.02:
            # Spread > 2% of mark price
            continue

        candidates.append(opt)

    if not candidates:
        logger.warning(
            f"No strike found for {option_type} under delta {max_delta} "
            f"and distance >= {min_distance}."
        )
        return None

    # Maximize Theta ("Daily Wage") among candidates, or fallback to closest delta
    if maximize_theta:
        best = max(candidates, key=lambda x: abs(float(x.get("theta", 0))))
    else:
        best = min(candidates, key=lambda x: abs(abs(float(x.get("delta", 0))) - max_delta))

    strike = Strike(
        product_id=int(best.get("product_id", 0)),
        strike_price=float(best.get("strike_price", 0)),
        delta=float(best.get("delta", 0)),
        premium=float(best.get("mark_price", 0)),
        option_type=option_type,
        symbol=best.get("symbol", ""),
    )

    gamma = float(best.get("gamma", 0))
    vega = float(best.get("vega", 0))
    theta = float(best.get("theta", 0))

    logger.info(
        f"Selected {option_type} strike: "
        f"K={strike.strike_price:.0f}, "
        f"Δ={strike.delta:.4f} (max={max_delta}), "
        f"Premium={strike.premium:.4f}, "
        f"Θ={theta:.5f}, Γ={gamma:.5f}, V={vega:.5f}"
    )
    return strike


def select_iron_condor_strikes(
    chain: list[dict],
    spot_price: float = 0.0,
    wide_wings: bool = False,
) -> dict:
    """
    Select 4 strikes for an Iron Condor.
    Short legs target standard delta, long legs serve as wing protection.
    """
    short_delta = config.SHORT_DELTA
    long_delta = config.LONG_DELTA
    
    # Iron Condor is generally delta neutral, we prioritize theta on short legs
    # Distance checking isn't as strictly necessary as directional spreads, but we pass it.
    min_dist = 500.0 if spot_price > 0 else 0.0

    if wide_wings:
        long_delta = max(0.02, long_delta - 0.02)
        logger.info(f"Wide wings mode: short_delta={short_delta}, long_delta={long_delta}")

    strikes = {
        "short_call": select_best_strike(chain, short_delta, OptionType.CALL.value, spot_price, min_dist, maximize_theta=True),
        "short_put": select_best_strike(chain, short_delta, OptionType.PUT.value, spot_price, min_dist, maximize_theta=True),
        "long_call": select_best_strike(chain, long_delta, OptionType.CALL.value, spot_price, min_dist + 500, maximize_theta=False),
        "long_put": select_best_strike(chain, long_delta, OptionType.PUT.value, spot_price, min_dist + 500, maximize_theta=False),
    }

    # Validate wing logic
    if strikes["short_call"] and strikes["long_call"]:
        if strikes["long_call"].strike_price <= strikes["short_call"].strike_price:
            logger.warning("Long call wing is not further OTM. Adjusting fallback...")
    if strikes["short_put"] and strikes["long_put"]:
        if strikes["long_put"].strike_price >= strikes["short_put"].strike_price:
            logger.warning("Long put wing is not further OTM. Adjusting fallback...")

    valid_count = sum(1 for v in strikes.values() if v is not None)
    logger.info(f"Iron Condor strike selection: {valid_count}/4 legs found")
    return strikes


def select_credit_spread_strikes(
    chain: list[dict],
    direction: str,
    spot_price: float = 0.0,
    wide_wings: bool = False,
) -> dict:
    """
    Select strikes for a directional Credit Spread, enforcing the $500 min distance
    and prioritizing max Theta for the short leg.
    """
    short_delta = config.DIRECTIONAL_DELTA
    long_delta = config.WING_DELTA
    min_dist = 500.0 if spot_price > 0 else 0.0

    if wide_wings:
        long_delta = max(0.02, long_delta - 0.02)

    if direction == "bullish":
        strikes = {
            "short_leg": select_best_strike(chain, short_delta, OptionType.PUT.value, spot_price, min_dist, maximize_theta=True),
            "long_leg": select_best_strike(chain, long_delta, OptionType.PUT.value, spot_price, min_dist + 500, maximize_theta=False),
        }
    else:
        strikes = {
            "short_leg": select_best_strike(chain, short_delta, OptionType.CALL.value, spot_price, min_dist, maximize_theta=True),
            "long_leg": select_best_strike(chain, long_delta, OptionType.CALL.value, spot_price, min_dist + 500, maximize_theta=False),
        }

    valid_count = sum(1 for v in strikes.values() if v is not None)
    logger.info(f"{direction.title()} credit spread strike selection: {valid_count}/2 legs found")
    return strikes
