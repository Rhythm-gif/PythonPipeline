"""
PACR Pipeline — Base Connector
Abstract base class for all source connectors.
Provides: HTTP client, retry logic, rate limiting, incremental sync.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import AsyncIterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.common.logging import get_logger
from app.papers.models import Paper, PaperSource

logger = get_logger(__name__)

# Exceptions that trigger a retry
RETRYABLE = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


class BaseConnector(ABC):
    source: PaperSource
    base_url: str
    rate_limit_delay: float = 0.5  # seconds between requests

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "BaseConnector":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "PACR-Pipeline/1.0 (research ingestion)"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Use connector as async context manager.")
        return self._client

    # ── Core Methods ──────────────────────────────────────────────────────────

    @abstractmethod
    async def fetch_latest(
        self, since: datetime | None, limit: int
    ) -> AsyncIterator[Paper]:
        """Yield normalized papers published after `since`."""
        ...

    # ── HTTP Helpers ──────────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def _get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        await asyncio.sleep(self.rate_limit_delay)
        logger.debug("HTTP GET", url=url, params=params)
        resp = await self.client.get(url, params=params, headers=headers)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 10))
            logger.warning("Rate limited", source=self.source, retry_after=retry_after)
            await asyncio.sleep(retry_after)
            raise httpx.RemoteProtocolError("Rate limited", request=resp.request)
        resp.raise_for_status()
        return resp

