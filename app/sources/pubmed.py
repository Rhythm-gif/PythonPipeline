"""
PACR Pipeline — PubMed Connector
Uses NCBI eSearch + eFetch to retrieve biomedical papers.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import AsyncIterator, Optional

from app.sources.base import BaseConnector
from app.config.settings import get_settings
from app.common.logging import get_logger
from app.papers.models import Author, Paper, PaperSource

logger = get_logger(__name__)

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
BATCH_SIZE = 20


class PubMedConnector(BaseConnector):
    source = PaperSource.PUBMED
    base_url = ESEARCH_URL
    rate_limit_delay = 0.35  # 3 req/s without key, 10/s with key

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()
        self._api_key = settings.ncbi_api_key
        if self._api_key:
            self.rate_limit_delay = 0.11

    async def fetch_latest(
        self, since: Optional[datetime], limit: int
    ) -> AsyncIterator[Paper]:
        # Build date range
        min_date = since.strftime("%Y/%m/%d") if since else "2020/01/01"
        max_date = datetime.utcnow().strftime("%Y/%m/%d")

        # Step 1: eSearch to get PMIDs
        pmids = await self._search(min_date, max_date, limit)
        if not pmids:
            logger.info("PubMed: no new PMIDs found")
            return

        logger.info("PubMed eSearch", pmid_count=len(pmids))

        # Step 2: eFetch in batches
        for i in range(0, len(pmids), BATCH_SIZE):
            batch = pmids[i: i + BATCH_SIZE]
            papers = await self._fetch_batch(batch)
            for paper in papers:
                yield paper

    async def _search(self, min_date: str, max_date: str, limit: int) -> list[str]:
        params = {
            "db": "pubmed",
            "term": f"has abstract[text] AND {min_date}:{max_date}[dp]",
            "retmax": min(limit, 10000),
            "retmode": "json",
            "sort": "pub date",
            "datetype": "pdat",
            "mindate": min_date,
            "maxdate": max_date,
        }
        if self._api_key:
            params["api_key"] = self._api_key

        try:
            resp = await self._get(ESEARCH_URL, params=params)
            data = resp.json()
            return data.get("esearchresult", {}).get("idlist", [])
        except Exception as exc:
            logger.error("PubMed eSearch failed", error=str(exc))
            return []

    async def _fetch_batch(self, pmids: list[str]) -> list[Paper]:
        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "rettype": "abstract",
        }
        if self._api_key:
            params["api_key"] = self._api_key

        try:
            resp = await self._get(EFETCH_URL, params=params)
            return self._parse_xml(resp.text)
        except Exception as exc:
            logger.error("PubMed eFetch failed", pmids=pmids[:3], error=str(exc))
            return []

    def _parse_xml(self, xml_text: str) -> list[Paper]:
        papers = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("PubMed XML parse error", error=str(exc))
            return []

        for article_node in root.findall(".//PubmedArticle"):
            try:
                paper = self._parse_article(article_node)
                if paper:
                    papers.append(paper)
            except Exception as exc:
                logger.warning("PubMed article parse failed", error=str(exc))
        return papers

    def _parse_article(self, node: ET.Element) -> Optional[Paper]:
        medline = node.find("MedlineCitation")
        if medline is None:
            return None

        article = medline.find("Article")
        if article is None:
            return None

        # PMID
        pmid_node = medline.find("PMID")
        pmid = pmid_node.text if pmid_node is not None else None
        if not pmid:
            return None

        # Title
        title_node = article.find("ArticleTitle")
        title = "".join(title_node.itertext()).strip() if title_node is not None else ""
        if not title:
            return None

        # Abstract
        abstract_parts = []
        for text_node in article.findall(".//AbstractText"):
            label = text_node.get("Label", "")
            text = "".join(text_node.itertext()).strip()
            if text:
                abstract_parts.append(f"{label}: {text}" if label else text)
        abstract = " ".join(abstract_parts) or None

        # Authors
        authors = []
        for author_node in article.findall(".//Author"):
            last = author_node.findtext("LastName", "")
            first = author_node.findtext("ForeName", "")
            name = f"{first} {last}".strip() or author_node.findtext("CollectiveName", "Unknown")
            affil_node = author_node.find(".//AffiliationInfo/Affiliation")
            affil = affil_node.text if affil_node is not None else None
            authors.append(Author(name=name, affiliation=affil))

        # DOI
        doi = None
        for id_node in article.findall(".//ELocationID"):
            if id_node.get("EIdType") == "doi":
                doi = id_node.text

        # Journal
        journal_node = article.find(".//Journal/Title")
        journal = journal_node.text if journal_node is not None else None

        # Publication date
        pub_date = self._parse_date(article)

        # Keywords / MeSH
        keywords = [
            d.text for d in node.findall(".//MeshHeadingList//DescriptorName")
            if d.text
        ]

        return Paper(
            source=PaperSource.PUBMED,
            external_id=pmid,
            doi=doi,
            title=title,
            abstract=abstract,
            authors=authors,
            publication_date=pub_date,
            journal=journal,
            citation_count=0,  # enriched later via Semantic Scholar
            keywords=keywords,
            source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        )

    @staticmethod
    def _parse_date(article: ET.Element) -> Optional[datetime]:
        for xpath in [
            ".//PubDate",
            ".//ArticleDate",
            ".//PubMedPubDate[@PubStatus='pubmed']",
        ]:
            node = article.find(xpath)
            if node is not None:
                year = node.findtext("Year")
                month = node.findtext("Month", "1")
                day = node.findtext("Day", "1")
                if year:
                    try:
                        month_int = int(month) if month.isdigit() else _month_str_to_int(month)
                        return datetime(int(year), month_int, int(day))
                    except (ValueError, TypeError):
                        try:
                            return datetime(int(year), 1, 1)
                        except Exception:
                            pass
        return None


def _month_str_to_int(month: str) -> int:
    mapping = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    return mapping.get(month.lower()[:3], 1)
