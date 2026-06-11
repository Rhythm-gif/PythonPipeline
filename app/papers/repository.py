"""
PACR Pipeline — Paper Repository
All MongoDB operations for the papers collection.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from pymongo import ReturnDocument

from app.common.logging import get_logger
from app.db.client import authors_col, papers_col, sync_state_col
from app.papers.models import (
    Paper,
    PaperRecord,
    PaperSource,
    PaperStatus,
    PaperScore,
    SyncState,
)

logger = get_logger(__name__)


# ── Sync State ─────────────────────────────────────────────────────────────────

async def get_sync_state(source: PaperSource) -> Optional[SyncState]:
    doc = await sync_state_col().find_one({"source": source.value})
    if doc:
        doc.pop("_id", None)
        return SyncState(**doc)
    return None


async def update_sync_state(
    source: PaperSource,
    last_sync: datetime,
    count: int = 0,
    error: str | None = None,
) -> None:
    await sync_state_col().update_one(
        {"source": source.value},
        {
            "$set": {
                "source": source.value,
                "last_sync": last_sync,
                "last_count": count,
                "last_error": error,
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )


# ── Duplicate Detection ────────────────────────────────────────────────────────

async def find_by_doi(doi: str) -> Optional[dict]:
    if not doi:
        return None
    return await papers_col().find_one({"doi": doi})


async def find_by_external_id(source: PaperSource, external_id: str) -> Optional[dict]:
    return await papers_col().find_one({"source": source.value, "external_id": external_id})


async def exists_by_doi(doi: str) -> bool:
    if not doi:
        return False
    return bool(await papers_col().find_one({"doi": doi}, {"_id": 1}))


async def exists_by_external_id(source: PaperSource, external_id: str) -> bool:
    return bool(
        await papers_col().find_one(
            {"source": source.value, "external_id": external_id}, {"_id": 1}
        )
    )


# ── Paper CRUD ─────────────────────────────────────────────────────────────────

async def insert_paper(paper: PaperRecord) -> str:
    data = paper.to_mongo()
    data.pop("_id", None)
    result = await papers_col().insert_one(data)
    return str(result.inserted_id)


async def save_approved_paper(paper: Paper, scores: PaperScore) -> str:
    """
    Persist a paper that has been approved by the LLM.
    This is the ONLY way a paper enters the database — rejected papers are discarded.
    Returns the new MongoDB document ID.
    """
    now = datetime.utcnow()
    doc = {
        "source": paper.source.value,
        "external_id": paper.external_id,
        "doi": paper.doi,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": [a.model_dump() for a in paper.authors],
        "publication_date": paper.publication_date,
        "journal": paper.journal,
        "citation_count": paper.citation_count,
        "funding_sources": getattr(paper, "funding_sources", []),
        "keywords": paper.keywords,
        "source_url": paper.source_url,
        "status": PaperStatus.APPROVED.value,
        "scores": scores.model_dump(),
        "created_at": now,
        "updated_at": now,
        "last_enriched_at": None,
    }
    # Upsert by DOI or external_id to avoid duplicates
    query: dict[str, Any] = {}
    if paper.doi:
        query["doi"] = paper.doi
    else:
        query = {"source": paper.source.value, "external_id": paper.external_id}

    result = await papers_col().update_one(
        query,
        {"$setOnInsert": doc},
        upsert=True,
    )

    # Also upsert authors to the authors collection
    for author in paper.authors:
        await upsert_author(author)

    return str(result.upserted_id) if result.upserted_id else ""


async def upsert_paper(normalized: Paper) -> tuple[str, bool]:
    """
    Insert or update a paper by DOI or external_id.
    Returns (paper_id, is_new).
    """
    query: dict[str, Any] = {}
    if normalized.doi:
        query["doi"] = normalized.doi
    else:
        query = {"source": normalized.source.value, "external_id": normalized.external_id}

    now = datetime.utcnow()
    update_data: dict[str, Any] = {
        "source": normalized.source.value,
        "external_id": normalized.external_id,
        "title": normalized.title,
        "abstract": normalized.abstract,
        "authors": [a.model_dump() for a in normalized.authors],
        "publication_date": normalized.publication_date,
        "journal": normalized.journal,
        "citation_count": normalized.citation_count,
        "funding_sources": getattr(normalized, "funding_sources", []),
        "keywords": normalized.keywords,
        "source_url": normalized.source_url,
        "updated_at": now,
    }
    # Only include doi in $set if it has a real value.
    if normalized.doi:
        update_data["doi"] = normalized.doi

    result = await papers_col().find_one_and_update(
        query,
        {
            "$set": update_data,
            "$setOnInsert": {
                "status": PaperStatus.REJECTED.value,
                "scores": PaperScore().model_dump(),
                "created_at": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    is_new = result.get("created_at") == now

    # Also upsert authors to the authors collection
    for author in normalized.authors:
        await upsert_author(author)

    return str(result["_id"]), is_new


async def upsert_author(author) -> None:
    """Upsert an author profile to the authors collection."""
    if not author.name:
        return
    query = {"orcid": author.orcid} if author.orcid else {"name": author.name}
    update_data = {
        "name": author.name,
        "orcid": author.orcid,
        "affiliation": author.affiliation,
        "h_index": getattr(author, "h_index", None),
        "updated_at": datetime.utcnow(),
    }
    await authors_col().update_one(
        query,
        {
            "$set": update_data,
            "$setOnInsert": {"created_at": datetime.utcnow()},
        },
        upsert=True,
    )


async def update_scores(
    paper_id: str,
    scores: PaperScore,
    status: PaperStatus,
) -> None:
    await papers_col().update_one(
        {"_id": ObjectId(paper_id)},
        {
            "$set": {
                "scores": scores.model_dump(),
                "status": status.value,
                "updated_at": datetime.utcnow(),
            }
        },
    )


async def mark_enriched(paper_id: str) -> None:
    await papers_col().update_one(
        {"_id": ObjectId(paper_id)},
        {"$set": {"last_enriched_at": datetime.utcnow()}},
    )


# ── Queries ────────────────────────────────────────────────────────────────────

async def get_paper_by_id(paper_id: str) -> Optional[dict]:
    try:
        return await papers_col().find_one({"_id": ObjectId(paper_id)})
    except Exception:
        return None


async def list_papers(
    status: PaperStatus | None = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "scores.final_score",
    sort_dir: int = -1,
    search: str | None = None,
    source: PaperSource | None = None,
) -> tuple[list[dict], int]:
    query: dict[str, Any] = {}
    if status:
        query["status"] = status.value
    if source:
        query["source"] = source.value
    if search:
        query["$text"] = {"$search": search}

    total = await papers_col().count_documents(query)
    skip = (page - 1) * page_size
    cursor = (
        papers_col()
        .find(query)
        .sort(sort_by, sort_dir)
        .skip(skip)
        .limit(page_size)
    )
    items = await cursor.to_list(length=page_size)
    return items, total
