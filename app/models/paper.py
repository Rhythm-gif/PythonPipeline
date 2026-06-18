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
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        if ObjectId.is_valid(v):
            return str(v)
        raise ValueError(f"Invalid ObjectId: {v}")


# ── Enums ──────────────────────────────────────────────────────────────────────

class PaperSource(str, Enum):
    OPENALEX = "openalex"
    PUBMED = "pubmed"
    ARXIV = "arxiv"
    CROSSREF = "crossref"
    SEMANTIC_SCHOLAR = "semantic_scholar"


class PaperStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ERROR = "error"


# ── Author ─────────────────────────────────────────────────────────────────────

class Author(BaseModel):
    name: str
    orcid: Optional[str] = None
    affiliation: Optional[str] = None
    h_index: Optional[int] = None


# ── Score Components ───────────────────────────────────────────────────────────

class LLMScoreResult(BaseModel):
    novelty: float = Field(ge=0, le=25)
    credibility: float = Field(ge=0, le=25)
    methodology: float = Field(ge=0, le=25)
    impact: float = Field(ge=0, le=25)
    total_score: float = Field(ge=0, le=100)
    reason: str = ""

    @model_validator(mode="after")
    def recalculate_total(self) -> "LLMScoreResult":
        self.total_score = self.novelty + self.credibility + self.methodology + self.impact
        return self


class ScoreComponents(BaseModel):
    llm_score: float = Field(default=0.0, ge=0, le=100)
    citation_score: float = Field(default=0.0, ge=0, le=100)
    journal_score: float = Field(default=0.0, ge=0, le=100)
    author_score: float = Field(default=0.0, ge=0, le=100)
    final_score: float = Field(default=0.0, ge=0, le=100)
    llm_detail: Optional[LLMScoreResult] = None


# ── Normalized Paper ───────────────────────────────────────────────────────────

class NormalizedPaper(BaseModel):
    """Common structure output by all connectors."""
    source: PaperSource
    external_id: str
    doi: Optional[str] = None
    title: str
    abstract: Optional[str] = None
    authors: list[Author] = []
    publication_date: Optional[datetime] = None
    journal: Optional[str] = None
    citation_count: int = 0
    keywords: list[str] = []
    source_url: Optional[str] = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

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

class PaperDocument(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    source: PaperSource
    external_id: str
    doi: Optional[str] = None
    title: str
    abstract: Optional[str] = None
    authors: list[Author] = []
    publication_date: Optional[datetime] = None
    journal: Optional[str] = None
    citation_count: int = 0
    keywords: list[str] = []
    source_url: Optional[str] = None

    # Scoring
    scores: ScoreComponents = Field(default_factory=ScoreComponents)
    status: PaperStatus = PaperStatus.PENDING
    rejection_reason: Optional[str] = None

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


# ── Score Log ──────────────────────────────────────────────────────────────────

class ScoreLog(BaseModel):
    paper_id: str
    doi: Optional[str] = None
    title: str
    scores: ScoreComponents
    status: PaperStatus
    rejection_reason: Optional[str] = None
    logged_at: datetime = Field(default_factory=datetime.utcnow)


# ── API Response Models ────────────────────────────────────────────────────────

class PaperResponse(BaseModel):
    id: str
    source: str
    doi: Optional[str]
    title: str
    abstract: Optional[str]
    authors: list[Author]
    publication_date: Optional[datetime]
    journal: Optional[str]
    citation_count: int
    keywords: list[str]
    source_url: Optional[str]
    final_score: float
    status: str
    created_at: datetime


class PaginatedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PaperResponse]


class HealthResponse(BaseModel):
    status: str
    mongodb: str
    scheduler: str
    version: str = "1.0.0"
