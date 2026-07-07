"""
PACR Pipeline - Pipeline Orchestrator

Flow: Fetch -> Scimago Q-value Check -> Approve? -> Enrich -> Publish Batch to Next.js

Only APPROVED (Q1/Q2) papers are enriched and pushed to the PACR Next.js API.
Rejected papers are immediately discarded - nothing is stored locally.
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
from app.scoring.engine import compute_scores, determine_status

logger = get_logger(__name__)


async def run_pipeline() -> dict:
    """
    Execute the full ingestion pipeline for all sources.
    Only approved papers are published to the PACR Next.js API.
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
    }

    sources = [
        (PaperSource.OPENALEX, OpenAlexConnector()),
        (PaperSource.PUBMED, PubMedConnector()),
    ]

    logger.info("Pipeline started", limit_per_source=limit)

    for source, connector in sources:
        logger.info("Pipeline: starting source", source=source.value)
        batch: list[dict] = []
        result = await _process_source(connector, limit, batch)
            
        summary["sources"][source.value] = result
        summary["total_fetched"] += result.get("fetched", 0)
        summary["total_approved"] += result.get("approved", 0)
        summary["total_rejected"] += result.get("rejected", 0)
        summary["total_duplicate"] += result.get("duplicate", 0)
        summary["total_error"] += result.get("error", 0)

    summary["completed_at"] = datetime.utcnow().isoformat()
    summary["duration_seconds"] = (datetime.utcnow() - start).total_seconds()

    logger.info(
        "Pipeline complete",
        **{k: v for k, v in summary.items() if not isinstance(v, dict)},
    )

    return summary


async def _process_source(connector, limit: int, batch: list[dict]) -> dict:
    """Process a single source connector, batch publish to Next.js, and return counts."""
    source = connector.source
    state = await repo.get_sync_state(source)
    since = state.last_sync if state else None

    counts = {
        "fetched": 0,
        "approved": 0,
        "rejected": 0,
        "duplicate": 0,
        "error": 0,
    }
    start = datetime.utcnow()

    try:
        fetched_papers = []
        async with connector:
            async for paper in connector.fetch_latest(since=since, limit=limit):
                fetched_papers.append(paper)
                counts["fetched"] += 1
                
        # Batch Check Duplicates against Next.js
        dois_to_check = [p.doi for p in fetched_papers if p.doi]
        existing_dois = set()
        if dois_to_check:
            existing_list = await pacr_client.check_exists_batch(dois_to_check)
            existing_dois = set(existing_list)
            counts["duplicate"] += len(existing_list)
            
        for paper in fetched_papers:
            if paper.doi in existing_dois:
                logger.debug("Duplicate skipped (Next.js Batch API)", title=paper.title[:60])
                continue
                
            try:
                await _ingest_paper(paper, counts, batch)
            except Exception as exc:
                logger.error(
                    "Paper ingestion error",
                    doi=paper.doi,
                    title=paper.title[:60],
                    error=str(exc),
                )
                counts["error"] += 1

        # ONLY after successfully processing and potentially publishing, update sync state
        if batch:
            logger.info(f"Publishing batch of {len(batch)} approved papers to PACR...")
            publish_result = await pacr_client.publish_batch(batch)
            logger.info("Batch publish result", **publish_result.get("metaData", {}))
            
        await repo.update_sync_state(source, last_sync=start, count=counts["fetched"])

    except Exception as exc:
        logger.error("Source sync failed", source=source.value, error=str(exc))
        # Important: We do not update last_sync if we hit an error (especially during publish),
        # so that the next run will fetch these papers again instead of skipping them.
        counts["error"] += 1

    return counts


async def _ingest_paper(paper: Paper, counts: dict, batch: list[dict]) -> None:
    """
    Full pipeline for a single paper.

    Steps:
      1. Scimago Q-value Check — look up journal ISSN against Scimago database
      2. Decision             — Q1/Q2 approved, rest rejected
      3. Lazy Enrichment      — only for approved papers: fetch Crossref/S2 metadata
      4. Publish              — add enriched paper to batch for Next.js API
    """
    # Step 1: Metadata Enrichment (Lazy)
    # Moved to after approval.
    paper_dict = paper.model_dump()

    # Step 2: LLM Scoring (Bypassed in favor of Scimago)
    try:
        scores = await compute_scores(paper_dict)
    except Exception as exc:
        logger.error("Scoring failed", title=paper.title[:60], error=str(exc))
        counts["error"] += 1
        return

    # Step 3: Approval Decision
    status = determine_status(scores)
    
    logger.info(
        "Paper scored",
        title=paper.title[:60],
        q_value=scores.scimago_q_value,
        status=status.value,
    )

    if status == PaperStatus.APPROVED:
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
            
        # Build the payload exactly matching the PACR backend PaperDto schema
        approved_payload = {
            "title": paper.title,
            "abstract": paper.abstract or "No abstract available.",
            "doi": paper.doi,
            "authors": [a.name for a in paper.authors],  # backend expects list of name strings
            "url": paper.source_url,
            "source": paper.source.value,
            "tags": paper.keywords,
            "dateOfPublication": paper.publication_date.isoformat() if paper.publication_date else None,
            "journalName": paper.journal,
        }
        batch.append(approved_payload)
        counts["approved"] += 1
        logger.info(
            "Paper approved and added to batch",
            title=paper.title[:60],
            doi=paper.doi,
            q_value=scores.scimago_q_value,
        )
    else:
        counts["rejected"] += 1
        logger.info(
            "Paper rejected",
            title=paper.title[:60],
            q_value=scores.scimago_q_value,
        )
