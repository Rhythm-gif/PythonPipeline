"""
PACR Pipeline — Pydantic Models
Canonical data models used throughout the pipeline.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── Enums ──────────────────────────────────────────────────────────────────────

class PaperSource(str, Enum):
    # Primary ingestion sources
    OPENALEX = "openalex"
    PUBMED = "pubmed"
    # Enrichment-only sources (not fetched directly, used internally by enrichment connectors)
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


# ── Score ──────────────────────────────────────────────────────────────────────

class PaperScore(BaseModel):
    scimago_q_value: Optional[str] = None


# ── Normalized Paper ───────────────────────────────────────────────────────────

class Paper(BaseModel):
    """Common structure output by all source connectors."""
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


# ── Sync State ─────────────────────────────────────────────────────────────────

class SyncState(BaseModel):
    """Tracks the last successful sync time per source, stored in sync_state.json."""
    source: PaperSource
    last_sync: Optional[datetime] = None
    last_count: int = 0
    last_error: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
