"""
Simple in-memory cache with TTL and file persistence.
"""
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from functools import wraps

from bot.replay.paths import get_paths

CACHE_DIR = get_paths().cache  # created lazily in persist(); import must not touch the disk


@dataclass
class CacheEntry:
    value: Any
    expires_at: float = 0


class Cache:
    """In-memory cache with optional file persistence."""

    def __init__(self, default_ttl: int = 300):
        self._data: dict[str, CacheEntry] = {}
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()

    def _is_expired(self, entry: CacheEntry) -> bool:
        return entry.expires_at > 0 and time.time() > entry.expires_at

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            entry = self._data.get(key)
            if entry and not self._is_expired(entry):
                return entry.value
            elif entry:
                del self._data[key]
            return None

    async def set(self, key: str, value: Any, ttl: int = None):
        async with self._lock:
            ttl = ttl or self._default_ttl
            self._data[key] = CacheEntry(
                value=value,
                expires_at=time.time() + ttl if ttl > 0 else 0
            )

    async def delete(self, key: str):
        async with self._lock:
            self._data.pop(key, None)

    async def clear_expired(self):
        async with self._lock:
            now = time.time()
            expired = [k for k, v in self._data.items() if v.expires_at > 0 and now > v.expires_at]
            for k in expired:
                del self._data[k]

    async def persist(self, filename: str = "cache.json"):
        """Save cache to disk without blocking the event loop."""
        async with self._lock:
            data = {
                k: {"value": v.value, "expires_at": v.expires_at}
                for k, v in self._data.items()
                if not self._is_expired(v)
            }
            payload = json.dumps(data)
        path = CACHE_DIR / filename

        def _write():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")

        await asyncio.to_thread(_write)

    async def load(self, filename: str = "cache.json"):
        """Load cache from disk."""
        path = CACHE_DIR / filename
        if not path.exists():
            return
        try:
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return
        async with self._lock:
            now = time.time()
            for k, v in data.items():
                if isinstance(v, dict) and "value" in v and (
                    v.get("expires_at", 0) == 0 or now < v["expires_at"]
                ):
                    self._data[k] = CacheEntry(
                        value=v["value"], expires_at=v.get("expires_at", 0)
                    )


cache = Cache(default_ttl=300)


async def cached_fetch(
    key: str,
    fetch_fn: Callable[[], Awaitable[Any]],
    ttl: int = 300
) -> Any:
    """Fetch with cache - returns cached value or calls fetch_fn."""
    cached = await cache.get(key)
    if cached is not None:
        return cached
    value = await fetch_fn()
    await cache.set(key, value, ttl)
    return value


def cached(ttl: int = 300, key_prefix: str = ""):
    """Decorator for async functions to add caching."""
    def decorator(func: Callable[..., Awaitable[Any]]):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            key = f"{key_prefix}{func.__name__}:{args}:{kwargs}"
            return await cached_fetch(key, lambda: func(*args, **kwargs), ttl)
        return wrapper
    return decorator