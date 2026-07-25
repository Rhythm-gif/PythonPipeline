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
                    "publication_date,primary_location,best_oa_location,"
                    "locations,cited_by_count,keywords,concepts,type,open_access,grants"
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

            # Journal and ISSN
            location = work.get("primary_location") or {}
            source_info = location.get("source") or {}
            journal = source_info.get("display_name")
            issn = source_info.get("issn_l")

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
                    
            # PDF URL — kept for backward compatibility with scoring gate
            pdf_url = None
            open_access = work.get("open_access", {})
            if open_access and open_access.get("is_oa"):
                pdf_url = open_access.get("oa_url")

            # ── Build structured OA location list for the PDF resolver ──────
            # Priority order matches sources.py collect_candidates():
            #   1. primary_location.pdf_url
            #   2. best_oa_location.pdf_url
            #   3. locations[i].pdf_url (remaining)
            #   9. open_access.oa_url   (often a landing page)
            #  10. primary_location.landing_page_url
            oa_locations: list[dict] = []
            is_globally_oa = bool(open_access and open_access.get("is_oa"))

            # 1. primary_location.pdf_url
            prim_pdf = location.get("pdf_url")
            if prim_pdf:
                oa_locations.append({
                    "url": prim_pdf,
                    "source": "primary_location.pdf_url",
                    "is_oa": is_globally_oa,
                    "is_landing": False,
                })

            # 2. best_oa_location.pdf_url
            best_oa = work.get("best_oa_location") or {}
            best_oa_pdf = best_oa.get("pdf_url")
            if best_oa_pdf and best_oa_pdf != prim_pdf:
                oa_locations.append({
                    "url": best_oa_pdf,
                    "source": "best_oa_location.pdf_url",
                    "is_oa": True,
                    "is_landing": False,
                })

            # 3. locations[i].pdf_url
            seen_loc_urls = {prim_pdf, best_oa_pdf} - {None}
            for i, loc in enumerate(work.get("locations") or []):
                loc_pdf = loc.get("pdf_url")
                if loc_pdf and loc_pdf not in seen_loc_urls:
                    seen_loc_urls.add(loc_pdf)
                    oa_locations.append({
                        "url": loc_pdf,
                        "source": f"locations[{i}].pdf_url",
                        "is_oa": bool(loc.get("is_oa")),
                        "is_landing": False,
                    })

            # 9. open_access.oa_url (landing page tier)
            oa_url = open_access.get("oa_url") if open_access else None
            if oa_url and oa_url not in seen_loc_urls:
                oa_locations.append({
                    "url": oa_url,
                    "source": "open_access.oa_url",
                    "is_oa": is_globally_oa,
                    "is_landing": True,
                })

            # 10. primary_location.landing_page_url
            prim_landing = location.get("landing_page_url")
            if prim_landing:
                oa_locations.append({
                    "url": prim_landing,
                    "source": "primary_location.landing_page_url",
                    "is_oa": is_globally_oa,
                    "is_landing": True,
                })

            return Paper(
                source=PaperSource.OPENALEX,
                external_id=external_id,
                doi=doi or None,
                title=title,
                abstract=abstract,
                authors=authors,
                publication_date=pub_date,
                journal=journal,
                issn=issn,
                citation_count=work.get("cited_by_count", 0),
                funding_sources=funding_sources,
                keywords=keywords,
                source_url=f"https://openalex.org/{external_id}",
                pdf_url=pdf_url,
                raw_metadata={
                    "oa_locations": oa_locations,
                    "open_access_oa_url": oa_url,
                    "primary_landing_page_url": prim_landing,
                },
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
