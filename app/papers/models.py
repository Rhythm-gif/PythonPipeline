"""
PACR Pipeline — Pydantic Models
Canonical data models used throughout the pipeline.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from bson import ObjectId
from pydantic import BaseModel, Field, field_validator, model_validator


# ── Helpers ────────────────────────────────────────────────────────────────────

class PyObjectId(str):
    """
    Custom Pydantic-compatible ObjectId type for MongoDB.
    Compatible with Pydantic v2.
    """
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema
        return core_schema.no_info_plain_validator_function(
            cls.validate,
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        if ObjectId.is_valid(str(v)):
            return str(v)
        raise ValueError(f"Invalid ObjectId: {v}")


# ── Enums ──────────────────────────────────────────────────────────────────────

class PaperSource(str, Enum):
    OPENALEX = "openalex"
    PUBMED = "pubmed"
    CROSSREF = "crossref"
    SEMANTIC_SCHOLAR = "semantic_scholar"


class PaperStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


# ── Author ─────────────────────────────────────────────────────────────────────

class Author(BaseModel):
    name: str
    orcid: Optional[str] = None
    affiliation: Optional[str] = None
    h_index: Optional[int] = None


class PaperScore(BaseModel):
    scimago_q_value: Optional[str] = None


# ── Normalized Paper ───────────────────────────────────────────────────────────

class Paper(BaseModel):
    """Common structure output by all connectors."""
    source: PaperSource
    external_id: str
    doi: Optional[str] = None
    title: str
    abstract: Optional[str] = None
    authors: list[Author] = []
    publication_date: Optional[datetime] = None
    journal: Optional[str] = None
    issn: Optional[str] = None
    citation_count: int = 0
    funding_sources: list[str] = []
    keywords: list[str] = []
    source_url: Optional[str] = None
    pdf_url: Optional[str] = None
    pmcid: Optional[str] = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    scimago_q_value: Optional[str] = None

    @field_validator("doi", mode="before")
    @classmethod
    def clean_doi(cls, v):
        if v:
            v = str(v).strip()
            if v.startswith("https://doi.org/"):
                v = v[len("https://doi.org/"):]
            if v.startswith("http://dx.doi.org/"):
                v = v[len("http://dx.doi.org/"):]
        return v or None

    @field_validator("title", mode="before")
    @classmethod
    def clean_title(cls, v):
        return str(v).strip() if v else ""


# ── Stored Paper (MongoDB document) ───────────────────────────────────────────

class PaperRecord(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    source: PaperSource
    external_id: str
    doi: Optional[str] = None
    title: str
    abstract: Optional[str] = None
    authors: list[Author] = []
    publication_date: Optional[datetime] = None
    journal: Optional[str] = None
    issn: Optional[str] = None
    citation_count: int = 0
    funding_sources: list[str] = []
    keywords: list[str] = []
    source_url: Optional[str] = None
    pdf_url: Optional[str] = None
    pmcid: Optional[str] = None
    scimago_q_value: Optional[str] = None

    # Scoring
    scores: PaperScore = Field(default_factory=PaperScore)
    status: PaperStatus = PaperStatus.REJECTED

    # Housekeeping
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_enriched_at: Optional[datetime] = None

    model_config = {"populate_by_name": True}

    def to_mongo(self) -> dict:
        data = self.model_dump(by_alias=True, exclude_none=False)
        if data.get("_id") is None:
            data.pop("_id", None)
        return data


# ── Sync State ─────────────────────────────────────────────────────────────────

class SyncState(BaseModel):
    source: PaperSource
    last_sync: Optional[datetime] = None
    last_count: int = 0
    last_error: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


