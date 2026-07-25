"""
PACR Pipeline — PDF Resolver (Main Orchestrator)
Collects candidate URLs, tries them in priority order, validates each response,
parses HTML landing pages for hidden PDF links, caches results, and returns
the first verified PDF or a structured failure.

This is the ONLY module the rest of the codebase needs to import.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from app.common.logging import get_logger
from app.config.settings import get_settings
from app.papers.models import Paper
from app.pdf_resolver.cache import AbstractPdfCache, InMemoryPdfCache
from app.pdf_resolver.downloader import download_candidate
from app.pdf_resolver.exceptions import NoCandidatesError
from app.pdf_resolver.parsers import extract_pdf_urls_from_html
from app.pdf_resolver.rate_limiter import DomainRateLimiter
from app.pdf_resolver.sources import Candidate, collect_candidates

logger = get_logger(__name__)


@dataclass
class ResolverResult:
    """Structured return value from PdfResolver.resolve()."""
    success: bool
    pdf_bytes: Optional[bytes] = None
    pdf_url: Optional[str] = None
    source: Optional[str] = None
    validation: Optional[dict] = None
    reason: Optional[str] = None   # populated on failure


class PdfResolver:
    """
    Multi-source, multi-fallback PDF resolution engine.

    Algorithm (per paper)
    ---------------------
    1. Check the DOI-keyed cache → return immediately on hit.
    2. Build candidate list via sources.collect_candidates().
       Raises NoCandidatesError when no OA signal exists → cache & return failure.
    3. Iterate candidates in priority order (queue is extended dynamically
       when an HTML page is parsed and new URLs are discovered).
       For each candidate:
         a. Skip if URL was already attempted (deduplication).
         b. Acquire domain semaphore.
         c. Stream-download with magic-byte validation.
         d. On %PDF match → cache success, return immediately.
         e. On HTML response → parse for PDF URLs, append to queue.
         f. Log failure reason, continue to next candidate.
    4. Queue exhausted → cache failure, return structured failure result.
    """

    def __init__(
        self,
        cache: Optional[AbstractPdfCache] = None,
        rate_limiter: Optional[DomainRateLimiter] = None,
    ) -> None:
        settings = get_settings()
        self._cache       = cache or InMemoryPdfCache()
        self._rate_limiter = rate_limiter or DomainRateLimiter(
            max_concurrent=settings.pdf_domain_concurrency
        )
        self._max_bytes = settings.pdf_max_size_mb * 1024 * 1024

    async def resolve(self, paper: Paper) -> ResolverResult:
        """
        Resolve a PDF for *paper* using every available Open Access source.

        Args:
            paper: Normalized Paper object.  The resolver reads:
                   - paper.doi, paper.pmcid
                   - paper.raw_metadata["oa_locations"]
                   - paper.raw_metadata.get("s2_pdf_url")
                   - paper.raw_metadata.get("open_access_oa_url")
                   - paper.raw_metadata.get("primary_landing_page_url")

        Returns:
            ResolverResult with success=True and pdf_bytes set on success,
            or success=False with a human-readable reason on failure.
        """
        cache_key = paper.doi or f"{paper.source.value}:{paper.external_id}"

        # ── 1. Cache check ────────────────────────────────────────────────────
        cached = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug("PDF cache hit", doi=paper.doi, success=cached.get("success"))
            return ResolverResult(**cached)

        # ── 2. Collect candidates ─────────────────────────────────────────────
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
        ) as meta_client:
            try:
                candidates = await collect_candidates(paper, meta_client)
            except NoCandidatesError as exc:
                logger.info(
                    "No OA signal — PDF resolution skipped",
                    doi=paper.doi,
                    reason=str(exc),
                    pmcid=paper.pmcid,
                    has_oa_locations=bool(paper.raw_metadata.get("oa_locations")),
                )
                result = ResolverResult(success=False, reason="no_oa_signal")
                await self._cache.set(cache_key, _to_cacheable(result))
                return result

            # ── 3. Iterate candidate queue ────────────────────────────────────
            attempted_urls: set[str] = set()
            failure_log: list[dict] = []   # track per-candidate failures for debug
            queue = list(candidates)   # mutable; HTML parser may extend it
            idx = 0

            while idx < len(queue):
                candidate: Candidate = queue[idx]
                idx += 1

                logger.info(
                    "Trying PDF candidate",
                    doi=paper.doi,
                    candidate_url=candidate.url,
                    source=candidate.source,
                    attempt=idx,
                    queue_size=len(queue),
                )

                dl = await download_candidate(
                    candidate.url,
                    self._rate_limiter,
                    max_bytes=self._max_bytes,
                    attempted_urls=attempted_urls,
                )

                logger.info(
                    "Candidate result",
                    doi=paper.doi,
                    source=candidate.source,
                    status_code=dl.status_code,
                    content_type=dl.content_type,
                    redirects=dl.redirects,
                    magic_bytes_verified=dl.magic_bytes_verified,
                    failure_reason=dl.failure_reason,
                )

                if dl.failure_reason:
                    failure_log.append({
                        "source": candidate.source,
                        "url": candidate.url,
                        "reason": dl.failure_reason,
                        "status_code": dl.status_code,
                    })

                # ── SUCCESS ───────────────────────────────────────────────
                if dl.success and dl.pdf_bytes:
                    logger.info(
                        "PDF resolved successfully",
                        doi=paper.doi,
                        winning_source=candidate.source,
                        winning_url=candidate.url,
                        size_bytes=len(dl.pdf_bytes),
                    )
                    result = ResolverResult(
                        success=True,
                        pdf_bytes=dl.pdf_bytes,
                        pdf_url=candidate.url,
                        source=candidate.source,
                        validation={
                            "content_type": dl.content_type,
                            "redirects": dl.redirects,
                            "magic_bytes_verified": dl.magic_bytes_verified,
                        },
                    )
                    await self._cache.set(cache_key, _to_cacheable(result))
                    return result

                # ── HTML landing page → parse and extend queue ────────────
                if dl.html_content:
                    discovered = extract_pdf_urls_from_html(
                        dl.html_content, base_url=candidate.url
                    )
                    if discovered:
                        logger.info(
                            "HTML parser discovered PDF URLs",
                            doi=paper.doi,
                            base_url=candidate.url,
                            count=len(discovered),
                            urls=discovered,
                        )
                        for disc_url in discovered:
                            if disc_url not in attempted_urls:
                                queue.append(
                                    Candidate(
                                        url=disc_url,
                                        source=f"html_parser({candidate.source})",
                                        is_likely_landing_page=False,
                                    )
                                )

        # ── 4. All candidates exhausted ────────────────────────────────────────────────
        logger.warning(
            "PDF resolution exhausted — all candidates failed",
            doi=paper.doi,
            total_tried=len(attempted_urls),
            failure_summary=failure_log,
        )
        result = ResolverResult(
            success=False,
            reason=f"all_candidates_exhausted:{len(attempted_urls)}_tried",
        )
        await self._cache.set(cache_key, _to_cacheable(result))
        return result


def _to_cacheable(result: ResolverResult) -> dict:
    """Strip non-serialisable / memory-heavy fields before caching."""
    return {
        "success":    result.success,
        "pdf_url":    result.pdf_url,
        "source":     result.source,
        "validation": result.validation,
        "reason":     result.reason,
        # pdf_bytes intentionally excluded
    }
