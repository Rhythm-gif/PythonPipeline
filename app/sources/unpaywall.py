"""
PACR Pipeline — Unpaywall Connector
Fetches guaranteed Open Access PDF links from the Unpaywall API.
"""
from __future__ import annotations

import httpx

from app.common.logging import get_logger

logger = get_logger(__name__)

# It's highly recommended to use a real email, but we provide a default for the pipeline.
UNPAYWALL_EMAIL = "pacr-pipeline@example.com"


async def get_unpaywall_pdf(doi: str) -> str | None:
    """
    Query the Unpaywall API to find the best direct PDF link for a DOI.
    Returns the URL as a string, or None if not found or not OA.
    """
    if not doi:
        return None

    url = f"https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_EMAIL}"
    logger.info("Checking Unpaywall for free PDF...", doi=doi)
    
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            
            if resp.status_code == 200:
                data = resp.json()
                oa_location = data.get("best_oa_location")
                if oa_location and isinstance(oa_location, dict):
                    # Always prefer url_for_pdf over url_for_landing_page
                    pdf_url = oa_location.get("url_for_pdf")
                    if pdf_url:
                        logger.info("Unpaywall found direct PDF", doi=doi)
                        return pdf_url
            elif resp.status_code == 404:
                # 404 just means it's not in the Unpaywall DB or not OA
                pass
            else:
                logger.debug(f"Unpaywall returned status {resp.status_code}", doi=doi)
                
    except Exception as exc:
        logger.warning("Unpaywall lookup failed", doi=doi, error=str(exc))

    return None
