"""
PACR Pipeline — Crossref Connector
DOI validation and metadata enrichment.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.sources.base import BaseConnector
from app.common.logging import get_logger
from app.papers.models import Author, PaperSource

logger = get_logger(__name__)


class CrossrefConnector(BaseConnector):
    source = PaperSource.CROSSREF
    base_url = "https://api.crossref.org/works"
    rate_limit_delay = 0.2

    async def fetch_latest(self, since, limit):
        # Crossref is enrichment-only; not a primary source
        raise NotImplementedError("CrossrefConnector is for enrichment only.")

    async def enrich(self, doi: str) -> Optional[dict]:
        """Fetch enriched metadata for a given DOI."""
        if not doi:
            return None
        url = f"{self.base_url}/{doi}"
        try:
            resp = await self._get(url)
            data = resp.json()
            return data.get("message", {})
        except Exception as exc:
            logger.debug("Crossref enrich skipped (not found/error)", doi=doi, error=str(exc))
            return None

    def extract_enrichment(self, message: dict) -> dict:
        """Extract enriched fields from a Crossref message."""
        result: dict = {}

        # Citation count
        result["citation_count"] = message.get("is-referenced-by-count", 0)

        # Journal
        container = message.get("container-title", [])
        if container:
            result["journal"] = container[0]

        # Authors
        authors = []
        for a in message.get("author", []):
            given = a.get("given", "")
            family = a.get("family", "")
            name = f"{given} {family}".strip() or "Unknown"
            orcid = (
                a.get("ORCID", "")
                .replace("http://orcid.org/", "")
                .replace("https://orcid.org/", "")
            )
            affils = [aff.get("name", "") for aff in a.get("affiliation", [])]
            authors.append(Author(
                name=name,
                orcid=orcid or None,
                affiliation=affils[0] if affils else None,
            ))
        if authors:
            result["authors"] = authors

        # Publication date
        for key in ("published", "published-print", "published-online"):
            dp = message.get(key, {}).get("date-parts", [[]])
            if dp and dp[0]:
                parts = dp[0]
                try:
                    result["publication_date"] = datetime(
                        parts[0],
                        parts[1] if len(parts) > 1 else 1,
                        parts[2] if len(parts) > 2 else 1,
                    )
                    break
                except (ValueError, IndexError):
                    pass

        # Keywords
        subjects = message.get("subject", [])
        if subjects:
            result["keywords"] = subjects

        return result
