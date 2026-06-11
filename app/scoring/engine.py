"""
PACR Pipeline — Composite Scoring Engine
Combines LLM score, citation score, journal score, and author score
into a single final_score using weighted formula.

Weights (configurable via settings):
  50%  LLM score
  20%  Citation score
  15%  Journal score
  15%  Author score
"""
from __future__ import annotations

import math

from app.config.settings import get_settings
from app.common.logging import get_logger
from app.papers.models import PaperStatus, PaperScore
from app.scoring.llm import score_paper

logger = get_logger(__name__)

# High-impact journal keyword signals
TIER1_JOURNALS = {
    "nature", "science", "cell", "lancet", "nejm",
    "new england journal of medicine", "jama", "bmj",
    "proceedings of the national academy", "pnas",
    "ieee transactions", "acm", "neurips", "icml", "iclr",
}

TIER2_JOURNALS = {
    "plos", "frontiers", "bmc", "springer", "elsevier",
    "oxford", "cambridge", "wiley", "mdpi",
}


async def compute_scores(paper_doc: dict) -> tuple[PaperScore, str]:
    """
    Compute all score components for a paper document.
    Returns (PaperScore, llm_decision) tuple.
    """
    settings = get_settings()

    # ── 1. LLM Score (0-100) ──────────────────────────────────────────────────
    authors_names = []
    affiliations = []
    for a in paper_doc.get("authors", []):
        if isinstance(a, dict):
            authors_names.append(a.get("name", ""))
            if a.get("affiliation"):
                affiliations.append(a.get("affiliation"))
        else:
            authors_names.append(getattr(a, "name", ""))
            if getattr(a, "affiliation", None):
                affiliations.append(getattr(a, "affiliation"))
                
    funding_sources = paper_doc.get("funding_sources", [])

    llm_result = await score_paper(
        title=paper_doc.get("title", ""),
        abstract=paper_doc.get("abstract", ""),
        authors=authors_names,
        journal=paper_doc.get("journal", "") or "",
        citation_count=paper_doc.get("citation_count", 0),
        affiliations=affiliations,
        funding_sources=funding_sources,
    )

    # ── 3. Journal Score (0-100) ──────────────────────────────────────────────
    journal = (paper_doc.get("journal") or "").lower()
    journal_score = _journal_to_score(journal)

    # ── 4. Author Score (0-100) ───────────────────────────────────────────────
    max_h = paper_doc.get("max_author_h_index", 0) or 0
    avg_h = paper_doc.get("avg_author_h_index", 0) or 0
    author_score = _author_to_score(max_h, avg_h, len(authors_names))

    # ── 5. Composite Final Score ──────────────────────────────────────────────
    final_score = (
        llm_result.total_score * settings.weight_llm
        + journal_score * settings.weight_journal
        + author_score * settings.weight_author
    )
    final_score = round(min(max(final_score, 0.0), 100.0), 2)

    logger.info(
        "Paper scored",
        title=paper_doc.get("title", "")[:60],
        llm=llm_result.total_score,
        journal=journal_score,
        author=author_score,
        final=final_score,
    )

    return PaperScore(
        llm_score=llm_result.total_score,
        journal_score=journal_score,
        author_score=author_score,
        final_score=final_score,
        llm_detail=llm_result,
    ), llm_result.decision


def _journal_to_score(journal: str) -> float:
    if not journal:
        return 30.0  # Neutral for unknown venue

    journal_lower = journal.lower()

    for kw in TIER1_JOURNALS:
        if kw in journal_lower:
            return 100.0

    for kw in TIER2_JOURNALS:
        if kw in journal_lower:
            return 65.0

    # arXiv preprints are credible but not peer-reviewed
    if "arxiv" in journal_lower:
        return 45.0

    return 40.0  # Generic journal


def _author_to_score(max_h: float, avg_h: float, num_authors: int) -> float:
    """
    Score based on author h-index signals.
    h-index 0        → 20 (no signal)
    h-index 10       → ~50
    h-index 30       → ~80
    h-index 50+      → ~100
    """
    if max_h == 0 and avg_h == 0:
        return 20.0  # No data — neutral-low

    # Use a combination of max and average
    effective_h = max_h * 0.6 + avg_h * 0.4

    # Sigmoid-ish: saturates around h=50
    score = min(effective_h / 50 * 100, 100.0)
    score = max(score, 20.0)  # floor
    return round(score, 2)


def determine_status(
    llm_decision: str,
) -> PaperStatus:
    """
    Use the LLM's own verdict to approve or reject a paper.
    The LLM is the sole decision maker — no numerical threshold.
    """
    if llm_decision == "approved":
        return PaperStatus.APPROVED
    return PaperStatus.REJECTED
