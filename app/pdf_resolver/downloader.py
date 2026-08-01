"""
PACR Pipeline — Streaming PDF Downloader
Downloads candidate URLs with browser-like headers, streamed validation,
per-domain concurrency limiting, and explicit HTTP status handling.

Key design decisions
--------------------
- Never uses HEAD + GET: every download is a single streaming GET.
- Never calls raise_for_status(): every status code is handled explicitly.
- Validates %PDF magic bytes on the FIRST chunk before reading the rest.
- Returns html_content when a landing page is detected so the resolver
  can hand it off to parsers.extract_pdf_urls_from_html().
- Enforces a configurable max_bytes limit during streaming.
- Retries only on 429/5xx and network errors (max 3 retries, exponential backoff).
- Deduplicates via a caller-supplied attempted_urls set.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.common.logging import get_logger
from app.pdf_resolver.rate_limiter import DomainRateLimiter
from app.pdf_resolver.validators import has_pdf_magic_bytes, is_html_content_type

logger = get_logger(__name__)

# Browser-like headers required by many publishers
_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# HTTP status codes that warrant a retry
_RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503})

# Network-level exceptions that warrant a retry
# NOTE: RemoteProtocolError is intentionally excluded — it means the server
# actively closed the connection (e.g. Europe PMC blocking scrapers).
# Retrying would waste 60+ seconds with the same result.
_RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
)

_MAX_RETRIES = 3
_CHUNK_SIZE  = 8_192   # bytes per streaming chunk


@dataclass
class DownloadResult:
    """Result of a single candidate download attempt."""
    success: bool
    pdf_bytes: Optional[bytes] = None
    content_type: str = ""
    status_code: int = 0
    redirects: int = 0
    magic_bytes_verified: bool = False
    # Populated when the server returns HTML instead of a PDF
    html_content: Optional[str] = None
    failure_reason: Optional[str] = None


async def download_candidate(
    url: str,
    rate_limiter: DomainRateLimiter,
    *,
    max_bytes: int = 50 * 1024 * 1024,
    timeout: float = 30.0,
    attempted_urls: set[str],
) -> DownloadResult:
    """
    Download *url* with streaming validation.

    Args:
        url:           Target URL (must be http/https).
        rate_limiter:  Per-domain concurrency controller.
        max_bytes:     Hard limit on download size in bytes.
        timeout:       HTTP timeout in seconds (connect + read combined).
        attempted_urls: Mutable set of already-attempted URLs; this function
                        adds *url* to the set before making any request.

    Returns:
        DownloadResult describing the outcome.
    """
    if url in attempted_urls:
        return DownloadResult(success=False, failure_reason="already_attempted")
    attempted_urls.add(url)

    sem = await rate_limiter.semaphore_for(url)
    async with sem:
        return await _download_with_retry(url, max_bytes=max_bytes, timeout=timeout)


async def _download_with_retry(
    url: str,
    max_bytes: int,
    timeout: float,
) -> DownloadResult:
    """Attempt download up to _MAX_RETRIES times for retryable failures."""
    last_result: DownloadResult = DownloadResult(
        success=False, failure_reason="unknown"
    )

    for attempt in range(_MAX_RETRIES + 1):
        try:
            last_result = await _do_download(url, max_bytes=max_bytes, timeout=timeout)
        except _RETRYABLE_EXCEPTIONS as exc:
            last_result = DownloadResult(
                success=False,
                failure_reason=f"network:{type(exc).__name__}:{exc}",
            )
        except Exception as exc:
            logger.warning("Unexpected download error", url=url, error=str(exc))
            return DownloadResult(
                success=False,
                failure_reason=f"unexpected:{type(exc).__name__}:{exc}",
            )

        if last_result.success:
            return last_result

        # Only retry on retryable status codes or network errors
        is_retryable = (
            last_result.status_code in _RETRYABLE_STATUSES
            or (last_result.failure_reason or "").startswith("network:")
        )
        if not is_retryable or attempt >= _MAX_RETRIES:
            return last_result

        # Exponential backoff; honour Retry-After for 429
        wait = min(2 ** attempt * 2, 30)
        if last_result.status_code == 429:
            wait = max(wait, 10)

        logger.debug(
            "Retrying download",
            url=url,
            attempt=attempt + 1,
            wait_seconds=wait,
            reason=last_result.failure_reason,
        )
        await asyncio.sleep(wait)

    return last_result


async def _do_download(
    url: str,
    max_bytes: int,
    timeout: float,
) -> DownloadResult:
    """Perform a single streaming GET request and validate the response."""
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_BROWSER_HEADERS,
        timeout=httpx.Timeout(timeout),
        http2=True,
    ) as client:
        async with client.stream("GET", url) as response:
            status       = response.status_code
            content_type = response.headers.get("content-type", "")
            redirects    = len(response.history)

            logger.debug(
                "HTTP response",
                url=url,
                status=status,
                content_type=content_type,
                redirects=redirects,
            )

            # ── Non-success statuses ──────────────────────────────────────
            if status not in (200, 206):
                return DownloadResult(
                    success=False,
                    status_code=status,
                    content_type=content_type,
                    redirects=redirects,
                    failure_reason=f"http_{status}",
                )

            # ── HTML landing page ─────────────────────────────────────────
            if is_html_content_type(content_type):
                html_bytes = await response.aread()
                html_text  = html_bytes.decode("utf-8", errors="replace")
                return DownloadResult(
                    success=False,
                    status_code=status,
                    content_type=content_type,
                    redirects=redirects,
                    html_content=html_text,
                    failure_reason="html_landing_page",
                )

            # ── Stream and validate PDF ───────────────────────────────────
            chunks: list[bytes] = []
            total          = 0
            magic_verified = False
            first_chunk    = True

            async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
                if first_chunk:
                    first_chunk = False
                    if not has_pdf_magic_bytes(chunk):
                        logger.debug(
                            "Magic bytes invalid",
                            url=url,
                            first_bytes=chunk[:8].hex(),
                        )
                        return DownloadResult(
                            success=False,
                            status_code=status,
                            content_type=content_type,
                            redirects=redirects,
                            failure_reason="magic_bytes_invalid",
                        )
                    magic_verified = True

                chunks.append(chunk)
                total += len(chunk)

                if total > max_bytes:
                    logger.warning(
                        "PDF exceeds max size limit",
                        url=url,
                        max_bytes=max_bytes,
                    )
                    return DownloadResult(
                        success=False,
                        status_code=status,
                        content_type=content_type,
                        redirects=redirects,
                        failure_reason=f"exceeded_max_size",
                    )

            if not magic_verified:
                return DownloadResult(
                    success=False,
                    status_code=status,
                    content_type=content_type,
                    redirects=redirects,
                    failure_reason="empty_response",
                )

            pdf_bytes = b"".join(chunks)
            logger.debug(
                "PDF download complete",
                url=url,
                size_bytes=len(pdf_bytes),
                redirects=redirects,
            )
            return DownloadResult(
                success=True,
                pdf_bytes=pdf_bytes,
                content_type=content_type,
                status_code=status,
                redirects=redirects,
                magic_bytes_verified=True,
            )
