"""
Strategy Engine – Constructs Iron Condor & Credit Spread order specifications.
Translates selected strikes into OrderSpec lists ready for batch submission.
"""

import logging

import config
from config import (
    OrderSpec, Strike, StrategyType, Regime,
    OrderSide, OptionType,
)
from strike_selector import (
    select_iron_condor_strikes,
    select_credit_spread_strikes,
)

logger = logging.getLogger("strategy_engine")


def build_iron_condor(
    chain: list[dict],
    wide_wings: bool = False,
    lot_size: int = 1,
) -> list[OrderSpec]:
    """
    Build a 4-leg Iron Condor order.

    Legs:
      1. SELL 0.10Δ Call  (short call)
      2. SELL 0.10Δ Put   (short put)
      3. BUY  0.05Δ Call  (long call – wing protection)
      4. BUY  0.05Δ Put   (long put – wing protection)

    Args:
        chain: Option chain data.
        wide_wings: Widen wing gap for high-IV environments.
        lot_size: Number of lots to trade.

    Returns:
        List of 4 OrderSpec objects.
    """
    strikes = select_iron_condor_strikes(chain, wide_wings=wide_wings)

    orders = []
    leg_configs = [
        ("short_call", OrderSide.SELL.value, "short_call"),
        ("short_put", OrderSide.SELL.value, "short_put"),
        ("long_call", OrderSide.BUY.value, "long_call"),
        ("long_put", OrderSide.BUY.value, "long_put"),
    ]

    for key, side, role in leg_configs:
        strike: Strike = strikes.get(key)
        if strike is None:
            logger.error(f"Missing strike for {key} – cannot build Iron Condor")
            return []

        order = OrderSpec(
            product_id=strike.product_id,
            side=side,
            size=lot_size,
            order_type="limit_order",
            limit_price=strike.premium,
            strike_price=strike.strike_price,
            option_type=strike.option_type,
            role=role,
        )
        orders.append(order)

    if len(orders) == 4:
        # Calculate total premium collected
        premium_collected = sum(
            o.limit_price for o in orders if o.side == OrderSide.SELL.value
        )
        premium_paid = sum(
            o.limit_price for o in orders if o.side == OrderSide.BUY.value
        )
        net_credit = premium_collected - premium_paid

        logger.info(
            f"✅ Iron Condor built successfully:\n"
            f"   Short Call: K={orders[0].strike_price:.0f} @ {orders[0].limit_price:.4f}\n"
            f"   Short Put:  K={orders[1].strike_price:.0f} @ {orders[1].limit_price:.4f}\n"
            f"   Long Call:  K={orders[2].strike_price:.0f} @ {orders[2].limit_price:.4f}\n"
            f"   Long Put:   K={orders[3].strike_price:.0f} @ {orders[3].limit_price:.4f}\n"
            f"   Net Credit: {net_credit:.4f}"
        )
    else:
        logger.error(f"Iron Condor incomplete: only {len(orders)}/4 legs")

    return orders


def build_credit_spread(
    chain: list[dict],
    direction: str,
    wide_wings: bool = False,
    lot_size: int = 1,
) -> list[OrderSpec]:
    """
    Build a 2-leg Credit Spread.

    Bullish (Bull Put Spread):
      1. SELL 0.15Δ Put  (short leg)
      2. BUY  0.05Δ Put  (long leg – wing protection)

    Bearish (Bear Call Spread):
      1. SELL 0.15Δ Call (short leg)
      2. BUY  0.05Δ Call (long leg – wing protection)

    Args:
        chain: Option chain data.
        direction: "bullish" or "bearish".
        wide_wings: Widen wing gap.
        lot_size: Number of lots.

    Returns:
        List of 2 OrderSpec objects.
    """
    strikes = select_credit_spread_strikes(
        chain, direction=direction, wide_wings=wide_wings
    )

    orders = []

    # Short leg
    short_strike: Strike = strikes.get("short_leg")
    if short_strike is None:
        logger.error(f"Missing short leg for {direction} credit spread")
        return []

    orders.append(OrderSpec(
        product_id=short_strike.product_id,
        side=OrderSide.SELL.value,
        size=lot_size,
        order_type="limit_order",
        limit_price=short_strike.premium,
        strike_price=short_strike.strike_price,
        option_type=short_strike.option_type,
        role="short_leg",
    ))

    # Long leg (wing)
    long_strike: Strike = strikes.get("long_leg")
    if long_strike is None:
        logger.error(f"Missing long leg for {direction} credit spread")
        return []

    orders.append(OrderSpec(
        product_id=long_strike.product_id,
        side=OrderSide.BUY.value,
        size=lot_size,
        order_type="limit_order",
        limit_price=long_strike.premium,
        strike_price=long_strike.strike_price,
        option_type=long_strike.option_type,
        role="long_leg",
    ))

    premium_collected = short_strike.premium
    premium_paid = long_strike.premium
    net_credit = premium_collected - premium_paid

    logger.info(
        f"✅ {direction.title()} Credit Spread built:\n"
        f"   Short: K={short_strike.strike_price:.0f} @ {short_strike.premium:.4f}\n"
        f"   Long:  K={long_strike.strike_price:.0f} @ {long_strike.premium:.4f}\n"
        f"   Net Credit: {net_credit:.4f}"
    )

    return orders


def build_strategy(
    regime: Regime,
    chain: list[dict],
    wide_wings: bool = False,
    lot_size: int = 1,
) -> tuple[StrategyType, list[OrderSpec]]:
    """
    Top-level strategy builder: picks strategy based on regime, builds orders.

    Args:
        regime: Market regime (SIDEWAYS, BULLISH, BEARISH).
        chain: Option chain data.
        wide_wings: High-IV flag.
        lot_size: Number of lots.

    Returns:
        Tuple of (StrategyType, list of OrderSpec).
    """
    if regime == Regime.SIDEWAYS:
        strategy_type = StrategyType.IRON_CONDOR
        orders = build_iron_condor(chain, wide_wings=wide_wings, lot_size=lot_size)
    elif regime == Regime.BULLISH:
        strategy_type = StrategyType.BULL_CREDIT_SPREAD
        orders = build_credit_spread(
            chain, direction="bullish", wide_wings=wide_wings, lot_size=lot_size
        )
    elif regime == Regime.BEARISH:
        strategy_type = StrategyType.BEAR_CREDIT_SPREAD
        orders = build_credit_spread(
            chain, direction="bearish", wide_wings=wide_wings, lot_size=lot_size
        )
    else:
        logger.warning(f"Unknown regime {regime}, defaulting to Iron Condor")
        strategy_type = StrategyType.IRON_CONDOR
        orders = build_iron_condor(chain, wide_wings=wide_wings, lot_size=lot_size)

    logger.info(
        f"Strategy: {strategy_type.value} | "
        f"Legs: {len(orders)} | "
        f"Wide Wings: {wide_wings}"
    )
    return strategy_type, orders
