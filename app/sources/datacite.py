"""
PACR Pipeline - DataCite Connector
DOI validation and metadata enrichment for repositories like Zenodo, Figshare, etc.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.sources.base import BaseConnector
from app.common.logging import get_logger
from app.papers.models import Author, PaperSource

logger = get_logger(__name__)


class DataCiteConnector(BaseConnector):
    # Treating it as a Crossref alternative for the data model
    source = PaperSource.CROSSREF 
    base_url = "https://api.datacite.org/dois"
    rate_limit_delay = 0.2

    async def fetch_latest(self, since, limit):
        raise NotImplementedError("DataCiteConnector is for enrichment only.")

    async def enrich(self, doi: str) -> Optional[dict]:
        """Fetch enriched metadata for a given DOI."""
        if not doi:
            return None
        url = f"{self.base_url}/{doi}"
        try:
            resp = await self._get(url)
            data = resp.json()
            return data.get("data", {}).get("attributes", {})
        except Exception as exc:
            logger.debug("DataCite enrich skipped (not found/error)", doi=doi, error=str(exc))
            return None

    def extract_enrichment(self, attributes: dict) -> dict:
        """Extract enriched fields from DataCite attributes."""
        result: dict = {}

        # Citation count
        citation_count = attributes.get("citationCount")
        if citation_count is not None:
            result["citation_count"] = citation_count

        # Journal / Publisher
        publisher = attributes.get("publisher")
        if publisher:
            result["journal"] = publisher

        # Authors
        authors = []
        for a in attributes.get("creators", []):
            name = a.get("name", "").strip()
            if not name and a.get("givenName"):
                name = f"{a.get('givenName', '')} {a.get('familyName', '')}".strip()
            if not name:
                name = "Unknown"
                
            orcid = None
            for id_dict in a.get("nameIdentifiers", []):
                if id_dict.get("nameIdentifierScheme") == "ORCID":
                    orcid = id_dict.get("nameIdentifier", "").replace("https://orcid.org/", "")
                    break
                    
            affils = []
            for aff in a.get("affiliation", []):
                if isinstance(aff, str):
                    affils.append(aff)
                elif isinstance(aff, dict):
                    affils.append(aff.get("name", ""))
            
            authors.append(Author(
                name=name,
                orcid=orcid,
                affiliation=affils[0] if affils else None,
            ))
            
        if authors:
            result["authors"] = authors

        # Publication date
        pub_year = attributes.get("publicationYear")
        if pub_year:
            try:
                result["publication_date"] = datetime(int(pub_year), 1, 1)
            except ValueError:
                pass

        # Keywords
        subjects = [s.get("subject", "") for s in attributes.get("subjects", [])]
        subjects = [s for s in subjects if s]
        if subjects:
            result["keywords"] = subjects

        return result
