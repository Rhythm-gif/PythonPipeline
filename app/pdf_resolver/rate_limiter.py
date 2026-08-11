"""
PACR Pipeline — Per-Domain Rate Limiter
Uses asyncio.Semaphore to cap concurrent outbound requests per hostname.
Semaphores are created lazily on first access and reused for the lifetime
of the process.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse


class DomainRateLimiter:
    """
    Maintains one asyncio.Semaphore per unique hostname.

    Usage::

        limiter = DomainRateLimiter(max_concurrent=2)
        sem = await limiter.semaphore_for("https://example.com/paper.pdf")
        async with sem:
            # at most 2 concurrent requests to example.com
            ...
    """

    def __init__(self, max_concurrent: int = 2) -> None:
        self._max_concurrent = max_concurrent
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _hostname(url: str) -> str:
        try:
            return urlparse(url).hostname or url
        except Exception:
            return url

    async def semaphore_for(self, url: str) -> asyncio.Semaphore:
        """Return the semaphore associated with *url*'s hostname."""
        host = self._hostname(url)
        async with self._lock:
            if host not in self._semaphores:
                self._semaphores[host] = asyncio.Semaphore(self._max_concurrent)
            return self._semaphores[host]
