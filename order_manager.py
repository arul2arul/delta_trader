"""
Order Manager – Batch order placement, margin validation,
and exchange-side protective orders (SL/TP).
"""

import logging
from typing import Optional

import config
from config import OrderSpec, StrategyType

logger = logging.getLogger("order_manager")


class OrderManager:
    """Handles all order-related operations."""

    def __init__(self, exchange_client, trade_logger):
        self.client = exchange_client
        self.trade_logger = trade_logger

    def validate_margin(
        self,
        order_specs: list[OrderSpec],
        available_margin: float,
    ) -> bool:
        """
        Ensure total margin required doesn't exceed 60% of capital.

        Returns True if margin is within limits.
        """
        max_allowed = config.CAPITAL * config.MAX_MARGIN_PCT

        if available_margin < max_allowed * 0.1:
            logger.error(
                f"Insufficient margin: ₹{available_margin:,.2f} available, "
                f"need at least ₹{max_allowed * 0.1:,.2f}"
            )
            return False

        # Estimate margin: for spreads, margin ≈ width between strikes × lot size
        # This is an approximation; actual margin is determined by the exchange
        estimated_margin = 0
        sell_legs = [o for o in order_specs if o.side == "sell"]
        buy_legs = [o for o in order_specs if o.side == "buy"]

        for sell_leg in sell_legs:
            # Find the corresponding buy leg (wing)
            matching_buy = None
            for buy_leg in buy_legs:
                if buy_leg.option_type == sell_leg.option_type:
                    matching_buy = buy_leg
                    break

            if matching_buy:
                width = abs(sell_leg.strike_price - matching_buy.strike_price)
                net_premium = sell_leg.limit_price - matching_buy.limit_price
                # Max loss per spread = width - net_premium
                leg_margin = max(0, (width - net_premium) * sell_leg.size)
                estimated_margin += leg_margin
            else:
                # Naked sell – higher margin requirement
                estimated_margin += sell_leg.strike_price * 0.05 * sell_leg.size

        if estimated_margin > max_allowed:
            logger.error(
                f"❌ Margin check FAILED: estimated ₹{estimated_margin:,.2f} "
                f"> max allowed ₹{max_allowed:,.2f} "
                f"({config.MAX_MARGIN_PCT * 100:.0f}% of ₹{config.CAPITAL:,})"
            )
            return False

        logger.info(
            f"✅ Margin check PASSED: estimated ₹{estimated_margin:,.2f} "
            f"< max ₹{max_allowed:,.2f}"
        )
        return True

    def place_batch_orders(
        self,
        order_specs: list[OrderSpec],
    ) -> list:
        """
        Place orders using Delta API batch_create (max 5 per batch).
        Splits into multiple batches if needed.
        """
        if not order_specs:
            logger.warning("No orders to place")
            return []

        # Convert OrderSpec to API format
        api_orders = []
        for spec in order_specs:
            order = {
                "product_id": spec.product_id,
                "size": spec.size,
                "side": spec.side,
                # Batch orders strictly require limit_order per Delta Exchange schema
                "order_type": "limit_order",
            }
            # Delta API requires limit_price as string
            if getattr(spec, "limit_price", 0) > 0:
                order["limit_price"] = str(spec.limit_price)
            api_orders.append(order)

        # Split into batches of max 5
        results = []
        for i in range(0, len(api_orders), config.BATCH_ORDER_MAX):
            batch = api_orders[i : i + config.BATCH_ORDER_MAX]
            batch_num = (i // config.BATCH_ORDER_MAX) + 1

            try:
                logger.info(
                    f"Placing batch {batch_num} "
                    f"({len(batch)} orders)..."
                )
                result = self.client.batch_create_orders(batch)
                results.append(result)

                # Log each order
                for j, spec in enumerate(order_specs[i : i + config.BATCH_ORDER_MAX]):
                    self.trade_logger.log_trade(
                        action="OPEN",
                        product_id=spec.product_id,
                        strike=spec.strike_price,
                        option_type=spec.option_type,
                        side=spec.side,
                        quantity=spec.size,
                        price=spec.limit_price,
                        notes=f"Batch {batch_num}, role={spec.role}",
                    )

                logger.info(f"✅ Batch {batch_num} placed successfully")

            except Exception as e:
                logger.error(f"❌ Batch {batch_num} failed: {e}")
                self.trade_logger.log_event(
                    action="ERROR",
                    notes=f"Batch order failed: {e}",
                )
                raise

        return results

    def place_hard_stop_loss(
        self,
        product_id: int,
        size: int,
        side: str,
        stop_price: float,
    ) -> Optional[dict]:
        """
        Place a hard stop-loss order directly on the exchange server.
        Executes even if bot crashes or loses internet.

        Args:
            product_id: The product to protect.
            size: Position size.
            side: Close side ("buy" for short positions, "sell" for long).
            stop_price: Price at which to trigger the stop.
        """
        try:
            result = self.client.place_order(
                product_id=product_id,
                size=size,
                side=side,
                order_type="market_order",
                stop_price=float(stop_price),
            )
            logger.info(
                f"🛡️ Hard SL placed on exchange: "
                f"product={product_id}, stop={stop_price:.2f}"
            )
            self.trade_logger.log_trade(
                action="HARD_SL",
                product_id=product_id,
                side=side,
                quantity=size,
                price=stop_price,
                notes="Exchange-side stop loss",
            )
            return result
        except Exception as e:
            logger.error(f"Failed to place hard SL for {product_id}: {e}")
            return None

    def place_hard_take_profit(
        self,
        product_id: int,
        size: int,
        side: str,
        tp_price: float,
    ) -> Optional[dict]:
        """
        Place a hard take-profit order directly on the exchange server.
        Executes even if bot crashes or loses internet.
        """
        try:
            result = self.client.place_order(
                product_id=product_id,
                size=size,
                side=side,
                order_type="limit_order",
                limit_price=float(tp_price),
            )
            logger.info(
                f"🎯 Hard TP placed on exchange: "
                f"product={product_id}, tp={tp_price:.2f}"
            )
            self.trade_logger.log_trade(
                action="HARD_TP",
                product_id=product_id,
                side=side,
                quantity=size,
                price=tp_price,
                notes="Exchange-side take profit",
            )
            return result
        except Exception as e:
            logger.error(f"Failed to place hard TP for {product_id}: {e}")
            return None

    def place_protective_orders(
        self,
        order_specs: list[OrderSpec],
        premium_collected: float,
    ):
        """
        After entry fill, place exchange-side SL and TP for each short leg.
        SL = 2.5x premium | TP = when option decays to near-zero.
        """
        for spec in order_specs:
            if spec.side != "sell":
                continue  # Only protect short legs

            # Stop-loss: if the option price exceeds 2.5x the premium collected
            sl_price = spec.limit_price * config.STOPLOSS_MULTIPLIER
            # Take-profit: when option decays to 10% of original premium
            tp_price = spec.limit_price * 0.10

            # Close side is opposite of the sell
            close_side = "buy"

            self.place_hard_stop_loss(
                product_id=spec.product_id,
                size=spec.size,
                side=close_side,
                stop_price=sl_price,
            )

            self.place_hard_take_profit(
                product_id=spec.product_id,
                size=spec.size,
                side=close_side,
                tp_price=tp_price,
            )

    def close_position(self, product_id: int):
        """Market-close a specific position."""
        try:
            result = self.client.close_position(product_id)
            self.trade_logger.log_trade(
                action="CLOSE",
                product_id=product_id,
                notes="Position closed",
            )
            return result
        except Exception as e:
            logger.error(f"Failed to close position {product_id}: {e}")
            raise

    def close_all_positions(self):
        """Kill switch: market-close ALL positions."""
        results = self.client.close_all_positions()
        self.trade_logger.log_event(
            action="KILL_SWITCH",
            notes=f"All positions closed. Results: {len(results)} orders.",
        )
        return results

    def roll_leg(
        self,
        product_id: int,
        new_product_id: int,
        size: int,
        new_side: str,
        new_price: float,
    ):
        """
        Roll a breached leg: close the old, open at new strike.
        """
        try:
            # Close breached leg
            logger.info(f"Rolling leg: closing product {product_id}")
            self.close_position(product_id)

            # Open new leg
            logger.info(
                f"Rolling leg: opening new product {new_product_id}"
            )
            result = self.client.place_order(
                product_id=new_product_id,
                size=size,
                side=new_side,
                order_type="limit_order",
                limit_price=new_price,
            )

            self.trade_logger.log_trade(
                action="ROLL",
                product_id=new_product_id,
                side=new_side,
                quantity=size,
                price=new_price,
                notes=f"Rolled from {product_id}",
            )
            return result
        except Exception as e:
            logger.error(f"Failed to roll leg {product_id}: {e}")
            raise
