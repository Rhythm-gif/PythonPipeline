"""
PACR Pipeline — OpenAlex Connector
Fetches latest research works from the OpenAlex API.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Optional

from app.sources.base import BaseConnector
from app.common.logging import get_logger
from app.papers.models import Author, Paper, PaperSource

logger = get_logger(__name__)

OPENALEX_BASE = "https://api.openalex.org/works"


class OpenAlexConnector(BaseConnector):
    source = PaperSource.OPENALEX
    base_url = OPENALEX_BASE
    rate_limit_delay = 0.1  # polite pool allows 10 req/s

    async def fetch_latest(
        self, since: Optional[datetime], limit: int
    ) -> AsyncIterator[Paper]:
        page = 1
        per_page = min(limit, 200)
        fetched = 0

        filter_parts = ["has_abstract:true", "type:article"]
        if since:
            filter_parts.append(f"from_publication_date:{since.date().isoformat()}")

        filters = ",".join(filter_parts)

        while fetched < limit:
            params = {
                "filter": filters,
                "sort": "publication_date:desc",
                "per-page": per_page,
                "page": page,
                "select": (
                    "id,doi,title,abstract_inverted_index,authorships,"
                    "publication_date,primary_location,cited_by_count,"
                    "keywords,concepts,type,grants"
                ),
            }

            try:
                resp = await self._get(self.base_url, params=params)
                data = resp.json()
            except Exception as exc:
                logger.error("OpenAlex fetch failed", page=page, error=str(exc))
                break

            results = data.get("results", [])
            if not results:
                break

            for work in results:
                if fetched >= limit:
                    return
                paper = self._normalize(work)
                if paper:
                    yield paper
                    fetched += 1

            # Check if there are more pages
            meta = data.get("meta", {})
            total = meta.get("count", 0)
            if page * per_page >= total:
                break
            page += 1

    def _normalize(self, work: dict) -> Optional[Paper]:
        try:
            title = work.get("title", "").strip()
            if not title:
                return None

            # Reconstruct abstract from inverted index
            abstract = self._reconstruct_abstract(work.get("abstract_inverted_index"))

            # Authors
            authors = []
            for authorship in work.get("authorships", []):
                author_data = authorship.get("author", {})
                institutions = authorship.get("institutions", [])
                affiliation = institutions[0].get("display_name") if institutions else None
                orcid = author_data.get("orcid")
                if orcid:
                    orcid = orcid.replace("https://orcid.org/", "")
                authors.append(Author(
                    name=author_data.get("display_name", "Unknown"),
                    orcid=orcid,
                    affiliation=affiliation,
                ))

            # Publication date
            pub_date_str = work.get("publication_date")
            pub_date = None
            if pub_date_str:
                try:
                    pub_date = datetime.fromisoformat(pub_date_str)
                except ValueError:
                    pass

            # Journal
            location = work.get("primary_location") or {}
            source_info = location.get("source") or {}
            journal = source_info.get("display_name")

            # DOI
            doi = work.get("doi", "")
            if doi:
                doi = doi.replace("https://doi.org/", "").strip()

            # Keywords
            keywords = [k.get("keyword", "") for k in work.get("keywords", [])]
            if not keywords:
                keywords = [c.get("display_name", "") for c in work.get("concepts", [])[:5]]
            keywords = [k for k in keywords if k]

            external_id = work.get("id", "").replace("https://openalex.org/", "")

            # Funding Sources
            funding_sources = []
            for grant in work.get("grants", []) or []:
                funder = grant.get("funder_display_name")
                if funder and funder not in funding_sources:
                    funding_sources.append(funder)

            return Paper(
                source=PaperSource.OPENALEX,
                external_id=external_id,
                doi=doi or None,
                title=title,
                abstract=abstract,
                authors=authors,
                publication_date=pub_date,
                journal=journal,
                citation_count=work.get("cited_by_count", 0),
                funding_sources=funding_sources,
                keywords=keywords,
                source_url=f"https://openalex.org/{external_id}",
            )
        except Exception as exc:
            logger.warning("OpenAlex normalization failed", error=str(exc))
            return None

    @staticmethod
    def _reconstruct_abstract(inverted_index: Optional[dict]) -> Optional[str]:
        if not inverted_index:
            return None
        word_positions: list[tuple[int, str]] = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        word_positions.sort(key=lambda x: x[0])
        return " ".join(w for _, w in word_positions) or None
