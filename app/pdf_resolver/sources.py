"""
PACR Pipeline — PDF Candidate URL Collector
Builds an ordered, deduplicated list of PDF candidate URLs for a given Paper.

Priority order
--------------
1.  OpenAlex primary_location.pdf_url
2.  OpenAlex best_oa_location.pdf_url
3.  OpenAlex locations[i].pdf_url  (all remaining, in API order)
4.  Europe PMC direct PDF  (via PMCID)
5.  Unpaywall best_oa_location.url_for_pdf
6.  Unpaywall oa_locations[i].url_for_pdf
7.  bioRxiv / medRxiv direct PDF  (DOI pattern 10.1101/)
8.  Semantic Scholar openAccessPdf.url  (stored in raw_metadata by enrichment)
9.  OpenAlex open_access.oa_url  (often a landing page → triggers HTML parser)
10. OpenAlex primary_location.landing_page_url  (landing page fallback)
11. https://doi.org/{doi}  (last resort)

Open Access guard
-----------------
If no OA signal is detected from any source, NoCandidatesError is raised
immediately. No network requests are made. Paywalled content is never touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.common.logging import get_logger
from app.config.settings import get_settings
from app.papers.models import Paper
from app.pdf_resolver.exceptions import NoCandidatesError

logger = get_logger(__name__)

_EUROPEPMC_PDF = "https://europepmc.org/backend/ptpmcrender.fcgi"
_UNPAYWALL_API = "https://api.unpaywall.org/v2/{doi}"
_BIORXIV_PDF   = "https://www.biorxiv.org/content/{doi}.full.pdf"
_MEDRXIV_PDF   = "https://www.medrxiv.org/content/{doi}.full.pdf"


@dataclass
class Candidate:
    """A single PDF candidate URL with provenance metadata."""
    url: str
    source: str                    # human-readable label, e.g. "primary_location.pdf_url"
    is_likely_landing_page: bool = False


async def collect_candidates(
    paper: Paper,
    http_client: httpx.AsyncClient,
) -> list[Candidate]:
    """
    Build the complete, ordered, deduplicated candidate list for *paper*.

    Args:
        paper:       Normalized Paper object.  raw_metadata["oa_locations"] is
                     populated by the OpenAlex connector (see openalex.py).
        http_client: Shared httpx client used for Unpaywall API calls.

    Returns:
        Non-empty list of Candidate objects ordered by priority.

    Raises:
        NoCandidatesError: if no OA signal is present from any source.
    """
    candidates: list[Candidate] = []
    seen_urls: set[str] = set()
    has_oa_signal = False

    def _add(
        url: Optional[str],
        source: str,
        *,
        is_landing: bool = False,
    ) -> None:
        if url and url not in seen_urls:
            seen_urls.add(url)
            candidates.append(
                Candidate(url=url, source=source, is_likely_landing_page=is_landing)
            )

    # ── 1–3. OpenAlex OA locations ──────────────────────────────────────────
    # raw_metadata["oa_locations"] is a list of dicts structured by openalex.py:
    # {"url": str, "source": str, "is_oa": bool, "is_landing": bool}
    oa_locations: list[dict] = paper.raw_metadata.get("oa_locations", [])
    for loc in oa_locations:
        url = loc.get("url")
        if url and loc.get("is_oa"):
            has_oa_signal = True
            _add(url, loc.get("source", "openalex"), is_landing=loc.get("is_landing", False))

    # ── 4. Europe PMC ────────────────────────────────────────────────────────
    if paper.pmcid:
        has_oa_signal = True
        pmcid_numeric = paper.pmcid.upper().lstrip("PMC")
        epmc_url = f"{_EUROPEPMC_PDF}?accid=PMC{pmcid_numeric}&blobtype=pdf"
        _add(epmc_url, "europe_pmc")

    # ── 5–6. Unpaywall ───────────────────────────────────────────────────────
    settings = get_settings()
    if paper.doi and settings.unpaywall_email.strip():
        uw_pairs = await _fetch_unpaywall(paper.doi, http_client, settings.unpaywall_email)
        for url, source in uw_pairs:
            has_oa_signal = True
            _add(url, source)

    # ── 7. bioRxiv / medRxiv ─────────────────────────────────────────────────
    if paper.doi and paper.doi.startswith("10.1101/"):
        has_oa_signal = True
        _add(_BIORXIV_PDF.format(doi=paper.doi), "biorxiv_direct")
        _add(_MEDRXIV_PDF.format(doi=paper.doi), "medrxiv_direct")

    # ── 8. Semantic Scholar openAccessPdf ────────────────────────────────────
    s2_pdf = paper.raw_metadata.get("s2_pdf_url")
    if s2_pdf:
        has_oa_signal = True
        _add(s2_pdf, "semantic_scholar_oa_pdf")

    # ── 9. OpenAlex oa_url (landing page tier) ───────────────────────────────
    oa_url = paper.raw_metadata.get("open_access_oa_url")
    if oa_url:
        _add(oa_url, "openalex_oa_url", is_landing=True)

    # ── 10. Primary location landing page ────────────────────────────────────
    landing = paper.raw_metadata.get("primary_landing_page_url")
    if landing:
        _add(landing, "primary_location.landing_page_url", is_landing=True)

    # ── 11. DOI landing page ─────────────────────────────────────────────────
    if paper.doi:
        _add(f"https://doi.org/{paper.doi}", "doi_landing_page", is_landing=True)

    if not has_oa_signal:
        raise NoCandidatesError(
            f"No Open Access signal found — doi={paper.doi!r}, pmcid={paper.pmcid!r}"
        )

    logger.info(
        "PDF candidates collected",
        doi=paper.doi,
        total=len(candidates),
        sources=[c.source for c in candidates],
    )
    return candidates


async def _fetch_unpaywall(
    doi: str,
    client: httpx.AsyncClient,
    email: str,
) -> list[tuple[str, str]]:
    """
    Call the Unpaywall API and return (url, source_label) pairs for OA PDF URLs.
    Returns an empty list on any error or when the paper is not OA.
    """
    api_url = _UNPAYWALL_API.format(doi=doi)
    try:
        resp = await client.get(api_url, params={"email": email}, timeout=10.0)
        if resp.status_code != 200:
            logger.debug("Unpaywall non-200", doi=doi, status=resp.status_code)
            return []
        data = resp.json()
        if not data.get("is_oa"):
            return []

        results: list[tuple[str, str]] = []
        best = data.get("best_oa_location") or {}
        best_pdf = best.get("url_for_pdf")
        if best_pdf:
            results.append((best_pdf, "unpaywall_best_oa_location"))

        for i, loc in enumerate(data.get("oa_locations") or []):
            pdf_url = loc.get("url_for_pdf")
            if pdf_url and pdf_url != best_pdf:
                results.append((pdf_url, f"unpaywall_oa_locations[{i}]"))

        return results

    except Exception as exc:
        logger.debug("Unpaywall fetch failed", doi=doi, error=str(exc))
        return []
