"""
PACR Pipeline — Metadata Enrichment Service
Orchestrates Crossref + Semantic Scholar enrichment for stored papers.
"""
from __future__ import annotations

from app.config.settings import get_settings

from app.sources.crossref import CrossrefConnector
from app.sources.datacite import DataCiteConnector
from app.sources.semantic_scholar import SemanticScholarConnector
from app.common.logging import get_logger
from app.papers.models import PaperSource

logger = get_logger(__name__)


async def enrich_paper(paper_doc: dict) -> dict:
    """
    Enrich a paper document with additional metadata.
    Returns an updated dict of fields to merge.
    Skips Semantic Scholar enrichment if no API key is configured
    to avoid severe rate limiting on the free unauthenticated tier.
    """
    updates: dict = {}
    doi = paper_doc.get("doi")
    external_id = paper_doc.get("external_id", "")
    source = paper_doc.get("source", "")

    # ── Crossref / DataCite Enrichment ────────────────────────────────────────
    if doi:
        async with CrossrefConnector() as crossref:
            cr_data = await crossref.enrich(doi)
            if cr_data:
                enriched = crossref.extract_enrichment(cr_data)
                updates.update(enriched)
                logger.debug("Crossref enrichment", doi=doi, fields=list(enriched.keys()))
        
        # Fallback to DataCite if Crossref found nothing
        if not cr_data:
            async with DataCiteConnector() as datacite:
                dc_data = await datacite.enrich(doi)
                if dc_data:
                    enriched = datacite.extract_enrichment(dc_data)
                    updates.update(enriched)
                    logger.debug("DataCite enrichment", doi=doi, fields=list(enriched.keys()))

    # ── Semantic Scholar Enrichment (only if API key present) ─────────────────
    s2_api_key = get_settings().semantic_scholar_api_key.strip()
    if not s2_api_key:
        logger.debug(
            "Skipping Semantic Scholar enrichment — no API key configured. "
            "Set SEMANTIC_SCHOLAR_API_KEY in .env to enable enrichment."
        )
        return updates

    async with SemanticScholarConnector() as s2:
        s2_data = None
        if doi:
            s2_data = await s2.enrich_by_doi(doi)
        elif source == PaperSource.ARXIV.value:
            s2_data = await s2.enrich_by_arxiv(external_id)

        if not s2_data:
            # Fallback: title search
            s2_data = await s2.enrich_by_title(paper_doc.get("title", ""))

        if s2_data:
            s2_enriched = s2.extract_enrichment(s2_data)
            # S2 citation count supersedes Crossref if higher
            if s2_enriched.get("citation_count", 0) > updates.get("citation_count", 0):
                updates["citation_count"] = s2_enriched["citation_count"]
            updates.update({k: v for k, v in s2_enriched.items() if k != "citation_count"})
            logger.debug("S2 enrichment", doi=doi, fields=list(s2_enriched.keys()))

    return updates
