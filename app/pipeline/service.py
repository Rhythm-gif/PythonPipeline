"""
PACR Pipeline - Pipeline Orchestrator

Flow: Fetch -> Resolve PDF -> Score (Q1/Q2 + DOI + pdf_url) -> Approve? -> Publish One-by-One to Next.js

Only APPROVED papers (Q1/Q2, has DOI, has pdf_url) are pushed to the PACR Next.js API.
PDF resolution runs before scoring so that paper.pdf_url is populated when determine_status() checks it.
Rejected papers are simply discarded - nothing is stored locally.
"""
from __future__ import annotations

from datetime import datetime

from app.sources import OpenAlexConnector, PubMedConnector
from app.config.settings import get_settings
from app.pipeline.enrichment import enrich_paper
from app.common.logging import get_logger
from app.papers import file_repository as repo
from app.papers.pacr_client import pacr_client
from app.papers.models import Paper, PaperSource, PaperStatus
from app.pdf_resolver import pdf_resolver
from app.scoring.engine import compute_scores, determine_status

logger = get_logger(__name__)


# Maximum number of papers to publish to the backend per cron run (across all sources)
MAX_PUBLISH_PER_RUN = 5


async def run_pipeline() -> dict:
    """
    Execute the full ingestion pipeline for all sources.
    Only approved papers (with a resolved PDF) are published to the PACR backend.
    Stops publishing once MAX_PUBLISH_PER_RUN papers are sent in this run.
    Returns a summary dict with counts.
    """
    settings = get_settings()
    limit = settings.papers_per_source
    start = datetime.utcnow()

    summary = {
        "started_at": start.isoformat(),
        "sources": {},
        "total_fetched": 0,
        "total_approved": 0,
        "total_rejected": 0,
        "total_duplicate": 0,
        "total_error": 0,
        "total_published_to_backend": 0,
    }

    sources = [
        (PaperSource.OPENALEX, OpenAlexConnector()),
        (PaperSource.PUBMED, PubMedConnector()),
    ]

    logger.info("Pipeline started", limit_per_source=limit, max_publish_per_run=MAX_PUBLISH_PER_RUN)

    # Shared mutable counter — passed into each source so the cap is global across all sources
    run_publish_count = {"count": 0}

    for source, connector in sources:
        if run_publish_count["count"] >= MAX_PUBLISH_PER_RUN:
            logger.info(
                "Run publish cap reached — skipping remaining sources",
                cap=MAX_PUBLISH_PER_RUN,
                source=source.value,
            )
            break

        logger.info("Pipeline: starting source", source=source.value)
        result = await _process_source(connector, limit, run_publish_count)

        summary["sources"][source.value] = result
        summary["total_fetched"] += result.get("fetched", 0)
        summary["total_approved"] += result.get("approved", 0)
        summary["total_rejected"] += result.get("rejected", 0)
        summary["total_duplicate"] += result.get("duplicate", 0)
        summary["total_error"] += result.get("error", 0)
        summary["total_published_to_backend"] += result.get("published_to_backend", 0)

    summary["completed_at"] = datetime.utcnow().isoformat()
    summary["duration_seconds"] = (datetime.utcnow() - start).total_seconds()

    logger.info(
        "Pipeline complete",
        **{k: v for k, v in summary.items() if not isinstance(v, dict)},
    )

    return summary


async def _process_source(connector, limit: int, run_publish_count: dict) -> dict:
    """Process a single source connector, publish to backend individually, and return counts."""
    source = connector.source
    state = await repo.get_sync_state(source)
    since = state.last_sync if state else None

    counts = {
        "fetched": 0,
        "approved": 0,
        "rejected": 0,
        "duplicate": 0,
        "error": 0,
        "published_to_backend": 0,
        # Rejection reason breakdown for diagnostics
        "rejected_q3_q4": 0,
        "rejected_no_doi": 0,
        "rejected_no_pdf": 0,
        "rejected_no_oa_signal": 0,
    }
    start = datetime.utcnow()

    try:
        async with connector:
            # We fetch up to a large number (e.g., 500) to ensure we don't run out
            # of candidates before we hit our MAX_PUBLISH_PER_RUN cap.
            async for paper in connector.fetch_latest(since=since, limit=500):
                # Stop fetching and processing once the per-run cap is hit
                if run_publish_count["count"] >= MAX_PUBLISH_PER_RUN:
                    logger.info(
                        "Run publish cap reached — stopping paper ingestion for this source",
                        cap=MAX_PUBLISH_PER_RUN,
                        source=source.value,
                    )
                    break

                counts["fetched"] += 1
                try:
                    await _ingest_paper(paper, counts, run_publish_count)
                except Exception as exc:
                    logger.error(
                        "Paper ingestion error",
                        doi=paper.doi,
                        title=paper.title[:60],
                        error=str(exc),
                    )
                    counts["error"] += 1

        logger.info(f"Total number of papers approved: {counts['approved']}")
        logger.info(f"Total posts created: {counts['published_to_backend']}")
        logger.info(
            "Rejection breakdown for this source",
            rejected_q3_q4=counts["rejected_q3_q4"],
            rejected_no_doi=counts["rejected_no_doi"],
            rejected_no_pdf=counts["rejected_no_pdf"],
            rejected_no_oa_signal=counts["rejected_no_oa_signal"],
        )

        await repo.update_sync_state(source, last_sync=start, count=counts["fetched"])

    except Exception as exc:
        logger.error("Source sync failed", source=source.value, error=str(exc))
        counts["error"] += 1

    return counts


async def _ingest_paper(paper: Paper, counts: dict, run_publish_count: dict) -> None:
    """
    Full pipeline for a single paper.

    Steps:
      1. Scoring        - Scimago Q-value check
      2. Decision       - approved (Q1/Q2 + DOI) -> Proceed | otherwise -> reject
      3. PDF Resolution - resolve PDF link and stream to S3
      4. Publish        - publish to backend if S3 stream succeeded
    """
    paper_dict = paper.model_dump()

    # Step 1: Scoring
    try:
        scores, q_value = await compute_scores(paper_dict)
    except Exception as exc:
        logger.error("Scoring failed", title=paper.title[:60], error=str(exc))
        counts["error"] += 1
        return

    # Step 2: Approval Decision
    status = determine_status(scores, paper_dict)

    logger.info(
        "Paper scored",
        title=paper.title[:60],
        q_value=scores.scimago_q_value,
        status=status.value,
        doi=paper.doi,
    )

    if status == PaperStatus.APPROVED:
        # Step 3: PDF Resolution & S3 Stream (Only for approved papers to save S3 costs)
        try:
            resolve_result = await pdf_resolver.resolve(paper)
            resolve_reason = resolve_result.reason or "success"
            if resolve_result.success:
                paper.pdf_url = resolve_result.pdf_url
                paper_dict["pdf_url"] = resolve_result.pdf_url
                s3_key: str | None = resolve_result.s3_key
                logger.info(
                    "PDF resolved and streamed to S3",
                    doi=paper.doi,
                    source=resolve_result.source,
                    s3_key=s3_key,
                )
            else:
                s3_key = None
                logger.info("PDF not resolved", doi=paper.doi, reason=resolve_result.reason)
        except Exception as exc:
            logger.warning("PDF resolution error (continuing)", doi=paper.doi, error=str(exc))
            s3_key = None

        # Step 4: Lazy Enrichment (only for approved papers)
        try:
            enriched = await enrich_paper(paper_dict)
            if enriched:
                paper_dict.update(enriched)
                for key, val in enriched.items():
                    if hasattr(paper, key):
                        setattr(paper, key, val)
        except Exception as exc:
            logger.warning("Enrichment failed (continuing)", title=paper.title[:60], error=str(exc))
            
        # Build the payload expected by the PACR Next.js API
        approved_payload = {
            "source": paper.source.value,
            "external_id": paper.external_id,
            "doi": paper.doi,
            "title": paper.title,
            "abstract": paper.abstract,
            "authors": [a.model_dump() for a in paper.authors],
            "publication_date": paper.publication_date.isoformat() if paper.publication_date else None,
            "journal": paper.journal,
            "funding_sources": getattr(paper, "funding_sources", []),
            "keywords": paper.keywords,
            "source_url": paper.source_url,
            "pdf_url": paper.pdf_url,
            "s3_key": s3_key,
            "scores": scores.model_dump(),
            "q_value": scores.scimago_q_value,
        }
        
        # ── STRICT PDF REQUIREMENT ──
        # We only publish Q1/Q2 papers if they have a valid PDF streamed to S3.
        if s3_key is None:
            logger.info("Paper rejected — missing PDF (S3 Key)", doi=paper.doi)
            counts["rejected_no_pdf"] += 1
            return
                
        # Send to backend
        try:
            logger.info("Publishing single paper with S3 Key...", doi=paper.doi)
            publish_result = await pacr_client.publish_single_with_pdf(approved_payload)
            
            counts["approved"] += 1
            if publish_result.get("published", 0) > 0:
                counts["published_to_backend"] = counts.get("published_to_backend", 0) + 1
                run_publish_count["count"] += 1  # increment global per-run cap counter
                
            logger.info(
                "Paper approved and sent to backend",
                title=paper.title[:60],
                doi=paper.doi,
                run_total=run_publish_count["count"],
            )
        except Exception as exc:
            logger.error("Failed to publish paper to backend", doi=paper.doi, error=str(exc))
            counts["error"] += 1
    else:
        counts["rejected"] += 1
        if scores.scimago_q_value not in ("Q1", "Q2"):
            counts["rejected_q3_q4"] += 1
            logger.info(
                "Paper rejected — Q-value below threshold",
                title=paper.title[:60],
                q_value=scores.scimago_q_value,
                doi=paper.doi,
            )
        elif not paper_dict.get("doi"):
            counts["rejected_no_doi"] += 1
            logger.info(
                "Paper rejected — no DOI",
                title=paper.title[:60],
            )
        else:
            logger.info(
                "Paper rejected",
                title=paper.title[:60],
                q_value=scores.scimago_q_value,
                doi=paper.doi,
            )
