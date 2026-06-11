"""
PACR Pipeline — MongoDB Client
Async motor-based client with indexes and collection accessors.
"""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.config.settings import get_settings
from app.common.logging import get_logger

logger = get_logger(__name__)

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect() -> None:
    global _client, _db
    settings = get_settings()
    logger.info("Connecting to MongoDB", uri=settings.mongodb_uri, db=settings.mongodb_db)
    _client = AsyncIOMotorClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )
    _db = _client[settings.mongodb_db]
    await _ensure_indexes()
    logger.info("MongoDB connected")


async def disconnect() -> None:
    if _client:
        _client.close()
        logger.info("MongoDB disconnected")


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB not connected. Call connect() first.")
    return _db


# ── Collections ────────────────────────────────────────────────────────────────

def papers_col():
    return get_db()["papers"]


def authors_col():
    return get_db()["authors"]


def sync_state_col():
    return get_db()["sync_state"]


# ── Index Creation ─────────────────────────────────────────────────────────────

async def _ensure_indexes() -> None:
    db = get_db()

    paper_indexes = [
        IndexModel([("doi", ASCENDING)], unique=True, sparse=True, name="doi_unique"),
        IndexModel(
            [("external_id", ASCENDING), ("source", ASCENDING)],
            unique=True,
            name="ext_id_source_unique",
        ),
        IndexModel([("title", ASCENDING)], name="title_asc"),
        IndexModel([("title", "text")], name="title_text"),
        IndexModel([("publication_date", DESCENDING)], name="pub_date_desc"),
        IndexModel([("scores.final_score", DESCENDING)], name="final_score_desc"),
        IndexModel([("status", ASCENDING)], name="status_asc"),
        IndexModel(
            [("status", ASCENDING), ("scores.final_score", DESCENDING)],
            name="status_score",
        ),
        IndexModel([("created_at", DESCENDING)], name="created_at_desc"),
    ]
    await db["papers"].create_indexes(paper_indexes)

    sync_indexes = [
        IndexModel([("source", ASCENDING)], unique=True, name="source_unique"),
    ]
    await db["sync_state"].create_indexes(sync_indexes)

    logger.info("MongoDB indexes ensured")
