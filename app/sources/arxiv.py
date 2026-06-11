"""
PACR Pipeline — ArXiv Connector
Fetches AI, CS, Physics, Math papers via the arXiv Atom feed API.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import AsyncIterator, Optional

import feedparser

from app.sources.base import BaseConnector
from app.common.logging import get_logger
from app.papers.models import Author, Paper, PaperSource

logger = get_logger(__name__)

ARXIV_BASE = "https://export.arxiv.org/api/query"

# Categories to ingest
CATEGORIES = [
    "cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.NE",
    "stat.ML", "physics.data-an", "math.ST", "q-bio.NC",
]

BATCH_SIZE = 100


class ArxivConnector(BaseConnector):
    source = PaperSource.ARXIV
    base_url = ARXIV_BASE
    rate_limit_delay = 3.0  # arXiv asks for 3s between requests

    async def fetch_latest(
        self, since: Optional[datetime], limit: int
    ) -> AsyncIterator[Paper]:
        seen_ids: set[str] = set()
        fetched = 0

        for category in CATEGORIES:
            if fetched >= limit:
                break

            start = 0
            while fetched < limit:
                batch = await self._fetch_category(category, start, BATCH_SIZE)
                if not batch:
                    break

                for entry in batch:
                    if fetched >= limit:
                        return
                    arxiv_id = self._extract_id(entry.get("id", ""))
                    if not arxiv_id or arxiv_id in seen_ids:
                        continue

                    # Incremental filter
                    pub_date = self._parse_date(entry.get("published", ""))
                    if since and pub_date and pub_date < since:
                        return  # results are sorted by date desc

                    paper = self._normalize(entry, arxiv_id, category)
                    if paper:
                        seen_ids.add(arxiv_id)
                        yield paper
                        fetched += 1

                start += BATCH_SIZE

    async def _fetch_category(self, category: str, start: int, max_results: int) -> list[dict]:
        params = {
            "search_query": f"cat:{category}",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": start,
            "max_results": max_results,
        }
        try:
            resp = await self._get(self.base_url, params=params)
            feed = feedparser.parse(resp.text)
            return feed.get("entries", [])
        except Exception as exc:
            logger.error("arXiv fetch failed", category=category, error=str(exc))
            return []

    def _normalize(self, entry: dict, arxiv_id: str, category: str) -> Optional[Paper]:
        try:
            title = entry.get("title", "").replace("\n", " ").strip()
            if not title:
                return None

            abstract = entry.get("summary", "").replace("\n", " ").strip() or None

            authors = [
                Author(name=a.get("name", "Unknown"))
                for a in entry.get("authors", [])
            ]

            pub_date = self._parse_date(entry.get("published", ""))

            doi = None
            for link in entry.get("links", []):
                if link.get("title") == "doi":
                    doi = link.get("href", "").replace("http://dx.doi.org/", "")

            tags = entry.get("tags", [])
            keywords = [t.get("term", "") for t in tags if t.get("term")]

            return Paper(
                source=PaperSource.ARXIV,
                external_id=arxiv_id,
                doi=doi,
                title=title,
                abstract=abstract,
                authors=authors,
                publication_date=pub_date,
                journal=f"arXiv:{category}",
                citation_count=0,
                keywords=keywords,
                source_url=f"https://arxiv.org/abs/{arxiv_id}",
            )
        except Exception as exc:
            logger.warning("arXiv normalization failed", arxiv_id=arxiv_id, error=str(exc))
            return None

    @staticmethod
    def _extract_id(url: str) -> Optional[str]:
        match = re.search(r"arxiv\.org/abs/([^\s]+)", url)
        return match.group(1) if match else None

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        if not date_str:
            return None
        formats = ["%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%d"]
        # Normalise common timezone suffixes that strptime can't parse
        clean = date_str.strip().replace(" GMT", "").replace(" UTC", "")
        for fmt in formats:
            try:
                return datetime.strptime(clean, fmt)
            except ValueError:
                continue
        return None
