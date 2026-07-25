"""
PACR Pipeline — Scimago Scoring Engine
Determines paper ranking based strictly on Scimago Q-values.
"""
from __future__ import annotations

from app.common.logging import get_logger
from app.papers.models import PaperStatus, PaperScore
from app.scoring.scimago import get_q_value

logger = get_logger(__name__)


async def compute_scores(paper_doc: dict) -> tuple[PaperScore, str]:
    """
    Computes Scimago Q-value for a paper document.
    Returns (PaperScore, q_value_string) tuple.
    """
    q_value = get_q_value(paper_doc.get("issn"))

    logger.debug(
        "Scimago Q-value lookup",
        issn=paper_doc.get("issn"),
        q_value=q_value,
        doi=paper_doc.get("doi"),
    )

    paper_score = PaperScore(
        scimago_q_value=q_value,
    )
    return paper_score, q_value


def determine_status(
    scores: PaperScore,
    paper_doc: dict,
) -> PaperStatus:
    """
    Approve paper only if the Scimago Q-value is Q1 or Q2,
    and it has a DOI. PDF check is handled downstream in service.py.
    """
    has_doi = bool(paper_doc.get("doi"))
    q_value = scores.scimago_q_value

    if q_value not in ("Q1", "Q2"):
        logger.debug(
            "Paper rejected by Q-value",
            doi=paper_doc.get("doi"),
            q_value=q_value,
        )
        return PaperStatus.REJECTED

    if not has_doi:
        logger.debug(
            "Paper rejected — no DOI",
            title=str(paper_doc.get("title", ""))[:60],
        )
        return PaperStatus.REJECTED

    return PaperStatus.APPROVED
