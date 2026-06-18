"""
PACR Pipeline — Semantic Scholar Connector
Citation validation, impact metrics, and metadata enrichment.
"""
from __future__ import annotations

from typing import Optional

from app.connectors.base import BaseConnector
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.paper import PaperSource

logger = get_logger(__name__)

SS_BASE = "https://api.semanticscholar.org/graph/v1"

PAPER_FIELDS = (
    "title,abstract,authors,citationCount,influentialCitationCount,"
    "year,referenceCount,fieldsOfStudy,publicationTypes,journal,"
    "externalIds,publicationDate,authors.hIndex,authors.citationCount"
)


class SemanticScholarConnector(BaseConnector):
    source = PaperSource.SEMANTIC_SCHOLAR
    base_url = SS_BASE
    rate_limit_delay = 1.0  # 1 req/s without key

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()
        self._api_key = settings.semantic_scholar_api_key
        if self._api_key:
            self.rate_limit_delay = 0.1

    async def fetch_latest(self, since, limit):
        raise NotImplementedError("SemanticScholarConnector is for enrichment only.")

    def _headers(self) -> dict:
        headers = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    async def enrich_by_doi(self, doi: str) -> Optional[dict]:
        url = f"{SS_BASE}/paper/DOI:{doi}"
        return await self._fetch_paper(url)

    async def enrich_by_arxiv(self, arxiv_id: str) -> Optional[dict]:
        url = f"{SS_BASE}/paper/ARXIV:{arxiv_id}"
        return await self._fetch_paper(url)

    async def enrich_by_title(self, title: str) -> Optional[dict]:
        url = f"{SS_BASE}/paper/search"
        params = {"query": title, "fields": PAPER_FIELDS, "limit": 1}
        try:
            resp = await self._get(url, params=params, headers=self._headers())
            data = resp.json()
            papers = data.get("data", [])
            return papers[0] if papers else None
        except Exception as exc:
            logger.warning("S2 title search failed", title=title[:50], error=str(exc))
            return None

    async def _fetch_paper(self, url: str) -> Optional[dict]:
        try:
            resp = await self._get(url, params={"fields": PAPER_FIELDS}, headers=self._headers())
            return resp.json()
        except Exception as exc:
            logger.warning("S2 fetch failed", url=url, error=str(exc))
            return None

    def extract_enrichment(self, data: dict) -> dict:
        """Extract enrichment fields from S2 paper data."""
        result: dict = {}

        result["citation_count"] = data.get("citationCount", 0)
        result["influential_citations"] = data.get("influentialCitationCount", 0)

        journal = data.get("journal") or {}
        if isinstance(journal, dict) and journal.get("name"):
            result["journal"] = journal["name"]

        authors_data = data.get("authors", [])
        author_h_indexes = [
            a.get("hIndex", 0)
            for a in authors_data
            if isinstance(a, dict) and a.get("hIndex")
        ]
        if author_h_indexes:
            result["max_author_h_index"] = max(author_h_indexes)
            result["avg_author_h_index"] = sum(author_h_indexes) / len(author_h_indexes)

        fields = data.get("fieldsOfStudy", [])
        if fields:
            result["fields_of_study"] = fields

        return result
