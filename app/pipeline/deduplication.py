"""
PACR Pipeline — Duplicate Detection Service
Prevents duplicate papers using:
  1. DOI match
  2. External ID + source match
  3. Title fuzzy similarity (RapidFuzz)
"""
from __future__ import annotations

from rapidfuzz import fuzz

from app.common.logging import get_logger
from app.papers import repository as repo
from app.papers.models import Paper

logger = get_logger(__name__)

TITLE_SIMILARITY_THRESHOLD = 92  # percent


async def is_duplicate(paper: Paper) -> tuple[bool, str]:
    """
    Check if a paper is a duplicate.
    Returns (is_dup, reason).
    """
    # 1. DOI check
    if paper.doi:
        if await repo.exists_by_doi(paper.doi):
            logger.debug("Duplicate by DOI", doi=paper.doi)
            return True, f"Duplicate DOI: {paper.doi}"

    # 2. External ID check
    if await repo.exists_by_external_id(paper.source, paper.external_id):
        logger.debug("Duplicate by external ID", source=paper.source, eid=paper.external_id)
        return True, f"Duplicate external_id: {paper.external_id}"

    # 3. Title similarity check against recent papers
    # We query a sample of titles and fuzzy-match
    # For performance, we only check if we have a reasonable title
    if len(paper.title) > 20:
        similar = await _check_title_similarity(paper.title)
        if similar:
            logger.debug("Duplicate by title similarity", title=paper.title[:60])
            return True, "Title too similar to existing paper"

    return False, ""


async def _check_title_similarity(title: str) -> bool:
    """
    Query papers with similar titles using a MongoDB text search approximation.
    Falls back to client-side fuzzy matching.
    """
    from app.db.client import papers_col

    # Use a simple prefix word search as a pre-filter
    words = title.split()[:4]
    prefix_query = " ".join(words)

    try:
        cursor = papers_col().find(
            {"$text": {"$search": prefix_query}},
            {"title": 1},
            limit=20,
        )
        candidates = await cursor.to_list(length=20)
    except Exception:
        # Text index may not exist yet; skip title check
        return False

    for doc in candidates:
        existing_title = doc.get("title", "")
        score = fuzz.token_sort_ratio(title.lower(), existing_title.lower())
        if score >= TITLE_SIMILARITY_THRESHOLD:
            return True

    return False
