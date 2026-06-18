"""
PACR Pipeline - Pipeline Orchestrator

Flow: Fetch -> Next.js Deduplicate -> Enrich -> LLM Score -> Approve? -> Publish Batch to Next.js

Only APPROVED papers are pushed to the PACR Next.js API.
Rejected papers are simply discarded - nothing is stored locally.
"""
from __future__ import annotations

from datetime import datetime

from app.sources import ArxivConnector, OpenAlexConnector, PubMedConnector
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
    # Hardcoded to 5 per source as requested
    limit = 5
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
        (PaperSource.ARXIV, ArxivConnector()),
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

        # Publish approved batch to PACR Next.js API
        if batch:
            logger.info(f"Publishing batch of {len(batch)} approved papers to PACR...")
            await pacr_client.publish_batch(batch)
            
        await repo.update_sync_state(source, last_sync=start, count=counts["fetched"])

    except Exception as exc:
        logger.error("Source sync failed", source=source.value, error=str(exc))
        counts["error"] += 1

    return counts


async def _ingest_paper(paper: Paper, counts: dict, batch: list[dict]) -> None:
    """
    Full pipeline for a single paper.

    Steps:
      1. Enrichment    - fetch extra metadata (Crossref, Semantic Scholar)
      2. LLM Scoring   - send to LLM for review
      3. Decision      - approved -> add to batch payload | rejected -> discard
    """
    # Step 1: Metadata Enrichment
    paper_dict = paper.model_dump()

    try:
        enriched = await enrich_paper(paper_dict)
        if enriched:
            paper_dict.update(enriched)
            for key, val in enriched.items():
                if hasattr(paper, key):
                    setattr(paper, key, val)
    except Exception as exc:
        logger.warning("Enrichment failed (continuing)", title=paper.title[:60], error=str(exc))

    # Step 2: LLM Scoring
    try:
        scores, llm_decision = await compute_scores(paper_dict)
        logger.info(
            "Paper scored",
            title=paper.title[:60],
            llm=scores.llm_score,
            journal=scores.journal_score,
            author=scores.author_score,
            final=scores.final_score,
            decision=llm_decision,
        )
    except Exception as exc:
        logger.error("Scoring failed", title=paper.title[:60], error=str(exc))
        counts["error"] += 1
        return

    # Step 3: Approval Decision
    status = determine_status(llm_decision, scores.final_score)

    if status == PaperStatus.APPROVED:
        # Build the payload perfectly matching NestJS PublishBatchDto
        # It expects: title, abstract, doi, authors (array of strings), url, source, score, tags, dateOfPublication, journalName
        approved_payload = {
            "title": paper.title or "",
            "abstract": paper.abstract or "",
            "doi": paper.doi or "",
            "authors": [a.name for a in paper.authors] if paper.authors else [],
            "url": paper.source_url or "",
            "source": paper.source.value,
            "score": scores.final_score,
            "tags": paper.keywords or [],
            "dateOfPublication": paper.publication_date.isoformat() if paper.publication_date else None,
            "journalName": paper.journal or ""
        }
        
        logger.debug("Built NestJS Payload", payload=approved_payload)
        
        batch.append(approved_payload)
        counts["approved"] += 1
        logger.info(
            "Paper approved and added to batch",
            title=paper.title[:60],
            doi=paper.doi,
            score=scores.final_score,
        )
    else:
        counts["rejected"] += 1
        logger.info(
            "Paper rejected",
            title=paper.title[:60],
            score=scores.final_score,
            decision=llm_decision,
        )
