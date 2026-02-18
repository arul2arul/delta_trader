"""
Rate Limiter – Token-bucket algorithm for Delta Exchange API.
Ensures we stay within ~10 requests/second to avoid rate-limit bans.
"""

import time
import threading
import logging

import config

logger = logging.getLogger("rate_limiter")


class RateLimiter:
    """Thread-safe token-bucket rate limiter."""

    def __init__(self, max_requests_per_sec: int = config.API_RATE_LIMIT):
        self.max_tokens = max_requests_per_sec
        self.tokens = float(max_requests_per_sec)
        self.rate = float(max_requests_per_sec)  # refill rate per second
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._warn_threshold = 0.80  # warn at 80% usage

    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
        self.last_refill = now

    def acquire(self, tokens: int = 1):
        """
        Block until a request slot is available.
        Consumes `tokens` from the bucket.
        """
        while True:
            with self._lock:
                self._refill()

                # Warn if approaching limit
                usage_pct = 1.0 - (self.tokens / self.max_tokens)
                if usage_pct >= self._warn_threshold:
                    logger.warning(
                        f"Rate limit usage at {usage_pct:.0%} "
                        f"({self.tokens:.1f}/{self.max_tokens} tokens remaining)"
                    )

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return

            # Sleep briefly before retrying
            time.sleep(0.05)

    def wrap(self, func):
        """Decorator to rate-limit any function."""
        def wrapper(*args, **kwargs):
            self.acquire()
            return func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper


# Global singleton instance
rate_limiter = RateLimiter()
