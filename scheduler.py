"""
Scheduler – IST clock management, weekend blackout, trading hours.
"""

import logging
from datetime import datetime, time as dt_time

import pytz

import config

logger = logging.getLogger("scheduler")


class Scheduler:
    """Manages trading schedule in IST timezone."""

    def __init__(self):
        self.ist = pytz.timezone(config.TIMEZONE)

    def now(self) -> datetime:
        """Get current time in IST."""
        return datetime.now(self.ist)

    def is_weekend_blackout(self, now: datetime = None) -> bool:
        """
        The Weekend Blackout: Bot shuts down Friday 5:00 PM IST
        and refuses all trades until Monday 9:00 AM IST.
        """
        now = now or self.now()
        weekday = now.weekday()  # Mon=0, Sun=6
        hour = now.hour

        # Friday after 5 PM
        if weekday == config.WEEKEND_SHUTDOWN_DAY and hour >= config.WEEKEND_SHUTDOWN_HOUR:
            logger.info("🔒 Weekend blackout: Friday after 5 PM IST")
            return True

        # Saturday (full day)
        if weekday == 5:
            logger.info("🔒 Weekend blackout: Saturday")
            return True

        # Sunday (full day)
        if weekday == 6:
            logger.info("🔒 Weekend blackout: Sunday")
            return True

        # Monday before 9 AM
        if weekday == config.WEEKEND_RESUME_DAY and hour < config.WEEKEND_RESUME_HOUR:
            logger.info("🔒 Weekend blackout: Monday before 9 AM IST")
            return True

        return False

    def is_trading_day(self, now: datetime = None) -> bool:
        """Check if today is a trading day (Monday–Thursday)."""
        now = now or self.now()
        is_valid = now.weekday() in config.TRADING_DAYS
        if not is_valid:
            logger.debug(f"Not a trading day: {now.strftime('%A')}")
        return is_valid

    def is_deploy_time(self, now: datetime = None) -> bool:
        """
        Check if it's deployment time: 10:00 AM IST (±2 min window).
        """
        now = now or self.now()

        target = now.replace(
            hour=config.DEPLOY_HOUR,
            minute=config.DEPLOY_MINUTE,
            second=0,
            microsecond=0,
        )

        diff_minutes = abs((now - target).total_seconds()) / 60

        if diff_minutes <= config.DEPLOY_WINDOW_MINUTES:
            logger.info(
                f"🕙 Deploy time! Current: {now.strftime('%H:%M:%S')} IST "
                f"(within ±{config.DEPLOY_WINDOW_MINUTES} min of "
                f"{config.DEPLOY_HOUR:02d}:{config.DEPLOY_MINUTE:02d})"
            )
            return True
        return False

    def seconds_until_deploy(self, now: datetime = None) -> float:
        """Calculate seconds until next 10:00 AM IST deploy window."""
        now = now or self.now()

        target = now.replace(
            hour=config.DEPLOY_HOUR,
            minute=config.DEPLOY_MINUTE,
            second=0,
            microsecond=0,
        )

        if now >= target:
            # Deploy time has passed today; calculate for tomorrow
            from datetime import timedelta
            target += timedelta(days=1)

        return (target - now).total_seconds()

    def get_poll_interval(self, strategy_type) -> int:
        """
        Get the appropriate polling interval based on strategy type.
        Wide Iron Condor: 90s | Tight Credit Spread: 45s
        """
        from config import StrategyType

        if strategy_type == StrategyType.IRON_CONDOR:
            return config.PNL_POLL_IRON_CONDOR
        else:
            return config.PNL_POLL_CREDIT_SPREAD

    def get_status(self) -> dict:
        """Get current schedule status for heartbeat messages."""
        now = self.now()
        return {
            "current_time": now.strftime("%Y-%m-%d %H:%M:%S IST"),
            "weekday": now.strftime("%A"),
            "is_weekend_blackout": self.is_weekend_blackout(now),
            "is_trading_day": self.is_trading_day(now),
            "is_deploy_time": self.is_deploy_time(now),
            "seconds_until_deploy": self.seconds_until_deploy(now),
        }
