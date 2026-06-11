"""
PACR Pipeline - Pipeline Orchestrator

Flow: Fetch -> Deduplicate -> Enrich -> LLM Score -> Approve? -> Save | Discard

Only APPROVED papers are saved to the database.
Rejected papers are simply discarded - nothing is stored.
"""
from __future__ import annotations

from datetime import datetime

from app.sources import ArxivConnector, OpenAlexConnector, PubMedConnector
from app.config.settings import get_settings
from app.pipeline.deduplication import is_duplicate
from app.pipeline.enrichment import enrich_paper
from app.common.logging import get_logger
from app.papers import repository as repo
from app.papers.models import Paper, PaperSource, PaperStatus
from app.scoring.engine import compute_scores, determine_status

logger = get_logger(__name__)


async def run_pipeline() -> dict:
    """
    Execute the full ingestion pipeline for all sources.
    Only approved papers are persisted to the database.
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
        (PaperSource.ARXIV, ArxivConnector()),
    ]

    logger.info("Pipeline started", limit_per_source=limit)

    for source, connector in sources:
        logger.info("Pipeline: starting source", source=source.value)
        result = await _process_source(connector, limit)
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


async def _process_source(connector, limit: int) -> dict:
    """Process a single source connector and return counts."""
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
        async with connector:
            async for paper in connector.fetch_latest(since=since, limit=limit):
                counts["fetched"] += 1
                try:
                    await _ingest_paper(paper, counts)
                except Exception as exc:
                    logger.error(
                        "Paper ingestion error",
                        doi=paper.doi,
                        title=paper.title[:60],
                        error=str(exc),
                    )
                    counts["error"] += 1

        await repo.update_sync_state(source, last_sync=start, count=counts["fetched"])

    except Exception as exc:
        logger.error("Source sync failed", source=source.value, error=str(exc))
        await repo.update_sync_state(
            source, last_sync=start, count=counts["fetched"], error=str(exc)
        )

    return counts


async def _ingest_paper(paper: Paper, counts: dict) -> None:
    """
    Full pipeline for a single paper.

    Steps:
      1. Deduplication - skip if already in DB
      2. Enrichment    - fetch extra metadata (Crossref, Semantic Scholar)
      3. LLM Scoring   - send to LLM for review
      4. Decision      - approved -> save to DB | rejected -> discard
    """
    # Step 1: Deduplication
    dup, reason = await is_duplicate(paper)
    if dup:
        logger.debug("Duplicate skipped", title=paper.title[:60], reason=reason)
        counts["duplicate"] += 1
        return

    # Step 2: Metadata Enrichment
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

    # Step 3: LLM Scoring
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

    # Step 4: Approval Decision
    status = determine_status(llm_decision)

    if status == PaperStatus.APPROVED:
        await repo.save_approved_paper(paper, scores)
        counts["approved"] += 1
        logger.info(
            "Paper approved and saved",
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
