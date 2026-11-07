"""
Simple async rate limiter for APIs.
"""
import asyncio
import time
from typing import Dict
from dataclasses import dataclass, field


@dataclass
class RateLimit:
    """Token bucket rate limiter."""
    max_requests: int
    window_seconds: float
    _tokens: float = field(init=False)
    _last_update: float = field(init=False)

    def __post_init__(self):
        self._tokens = self.max_requests
        self._last_update = time.monotonic()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_update
        refill = elapsed * (self.max_requests / self.window_seconds)
        self._tokens = min(self.max_requests, self._tokens + refill)
        self._last_update = now

    async def acquire(self):
        while True:
            self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                return
            await asyncio.sleep(0.1)


class RateLimiter:
    """Manages rate limits for multiple endpoints."""

    def __init__(self):
        self._limits: Dict[str, RateLimit] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def add_limit(self, name: str, max_requests: int, window_seconds: float):
        self._limits[name] = RateLimit(max_requests, window_seconds)
        self._locks[name] = asyncio.Lock()

    async def acquire(self, name: str):
        if name not in self._limits:
            return
        async with self._locks[name]:
            await self._limits[name].acquire()


# Pre-configured rate limiters
rate_limiter = RateLimiter()
rate_limiter.add_limit("henrikdev", 30, 60)    # 30 req/min
rate_limiter.add_limit("tracker", 20, 60)      # 20 req/min (conservative)