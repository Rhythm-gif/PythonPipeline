"""
PACR Pipeline — PDF Resolution Cache
Abstract interface + in-memory implementation with TTL.

Cache stores DOI → resolution metadata only. Raw pdf_bytes are never cached
to avoid unbounded memory growth. A Redis implementation can replace
InMemoryPdfCache later without touching any resolver logic.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Optional


class AbstractPdfCache(ABC):
    """
    Abstract cache interface for DOI → resolution result mappings.
    Implement this class to swap in Redis, Memcached, etc.
    """

    @abstractmethod
    async def get(self, key: str) -> Optional[dict]:
        """
        Return the cached resolution result dict for *key*, or None if
        the entry is missing or has expired.
        """
        ...

    @abstractmethod
    async def set(self, key: str, value: dict) -> None:
        """Store a resolution result dict under *key*."""
        ...


class InMemoryPdfCache(AbstractPdfCache):
    """
    Thread-safe in-memory cache with configurable TTL.

    Both successes and failures are cached so that the resolver does not
    waste API calls re-resolving the same DOI on every pipeline run.
    Raw pdf_bytes are stripped before storage.
    """

    def __init__(self, ttl_seconds: int = 86_400) -> None:
        # key → (value_dict, expiry_monotonic_timestamp)
        self._store: dict[str, tuple[dict, float]] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds

    async def get(self, key: str) -> Optional[dict]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del self._store[key]
                return None
            return value

    async def set(self, key: str, value: dict) -> None:
        # Strip s3_key before storing - never cache S3 keys to avoid returning expired/stale URLs
        storable = {k: v for k, v in value.items() if k != "s3_key"}
        async with self._lock:
            self._store[key] = (storable, time.monotonic() + self._ttl)

    async def size(self) -> int:
        """Return current number of cached entries (including expired)."""
        async with self._lock:
            return len(self._store)
