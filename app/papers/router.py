"""
PACR Pipeline — Papers Router
Exposes only the two public paper endpoints: latest and top-rated.
All papers returned are pre-approved by the LLM.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.papers import repository as repo
from app.papers.models import PaperStatus
from app.common.models import ApiResponse, ServiceStatusEnum

router = APIRouter(prefix="/papers", tags=["Papers"])


@router.get("/latest", response_model=ApiResponse)
async def latest_papers(
    request: Request,
    limit: int = Query(default=10, ge=1, le=50, description="Number of papers to return"),
):
    """
    Returns the most recently approved papers sorted by ingestion date.
    """
    items, _ = await repo.list_papers(
        status=PaperStatus.APPROVED,
        page=1,
        page_size=limit,
        sort_by="created_at",
        sort_dir=-1,
    )
    return ApiResponse(
        status=ServiceStatusEnum.SUCCESS,
        message=f"Retrieved {len(items)} latest papers.",
        requestId=request.state.request_id,
        metaData={"count": len(items)},
        data=[_serialize(doc) for doc in items],
    )


@router.get("/top-rated", response_model=ApiResponse)
async def top_rated_papers(
    request: Request,
    limit: int = Query(default=10, ge=1, le=50, description="Number of papers to return"),
):
    """
    Returns approved papers sorted by their composite AI score (highest first).
    """
    items, _ = await repo.list_papers(
        status=PaperStatus.APPROVED,
        page=1,
        page_size=limit,
        sort_by="scores.final_score",
        sort_dir=-1,
    )
    return ApiResponse(
        status=ServiceStatusEnum.SUCCESS,
        message=f"Retrieved {len(items)} top-rated papers.",
        requestId=request.state.request_id,
        metaData={"count": len(items)},
        data=[_serialize(doc) for doc in items],
    )


def _serialize(doc: dict) -> dict:
    """Convert a MongoDB document to a clean API response dict."""
    scores = doc.get("scores", {})
    return {
        "id": str(doc.get("_id", "")),
        "title": doc.get("title"),
        "abstract": doc.get("abstract"),
        "authors": [
            {"name": a.get("name"), "affiliation": a.get("affiliation")}
            for a in doc.get("authors", [])
        ],
        "journal": doc.get("journal"),
        "publication_date": doc.get("publication_date"),
        "doi": doc.get("doi"),
        "source": doc.get("source"),
        "source_url": doc.get("source_url"),
        "citation_count": doc.get("citation_count", 0),
        "keywords": doc.get("keywords", []),
        "final_score": scores.get("final_score", 0),
        "llm_reason": scores.get("llm_detail", {}).get("reason") if scores.get("llm_detail") else None,
        "created_at": doc.get("created_at"),
    }
