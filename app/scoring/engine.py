"""
PACR Pipeline — Scimago Scoring Engine
Determines paper ranking based strictly on Scimago Q-values.
"""
from __future__ import annotations

from app.common.logging import get_logger
from app.papers.models import PaperStatus, PaperScore
from app.scoring.scimago import get_q_value

logger = get_logger(__name__)


async def compute_scores(paper_doc: dict) -> PaperScore:
    """
    Computes Scimago Q-value for a paper document.
    """
    q_value = get_q_value(paper_doc.get("issn"))

    return PaperScore(
        scimago_q_value=q_value,
    )


def determine_status(
    scores: PaperScore,
) -> PaperStatus:
    """
    Approve paper only if the Scimago Q-value is Q1 or Q2.
    """
    if scores.scimago_q_value in ("Q1", "Q2"):
        return PaperStatus.APPROVED
    return PaperStatus.REJECTED
