"""
PACR Pipeline — Next.js API Client
Handles communication with the PACR backend to verify and publish papers.
"""
from __future__ import annotations

import httpx

from app.config.settings import get_settings
from app.common.logging import get_logger

logger = get_logger(__name__)


class PacrClient:
    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.pacr_backend_url.rstrip("/")
        self.api_key = self.settings.pacr_internal_api_key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def check_exists_batch(self, dois: list[str]) -> list[str]:
        """
        Check which DOIs already exist in the Next.js database using a batch request.
        Returns a list of DOIs that exist.
        """
        if not dois:
            return []
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                url = f"{self.base_url}/api/internal/check-exists-batch"
                payload = {"dois": dois}
                resp = await client.post(url, json=payload, headers=self._headers())
                
                # If 404, we assume endpoint doesn't exist yet, return empty
                if resp.status_code == 404:
                    return []
                    
                resp.raise_for_status()
                response_json = resp.json()
                
                # NestJS wraps responses in { "data": { "existing_dois": [...] } }
                inner_data = response_json.get("data", {})
                return inner_data.get("existing_dois", [])
        except Exception as exc:
            logger.warning("Failed to check batch duplicates from PACR backend", error=str(exc))
            # On error, safely assume false (empty) so we don't drop potentially good papers
            return []

    async def publish_batch(self, batch: list[dict]) -> dict:
        """
        Publish a batch of approved papers to the Next.js backend.
        """
        if not batch:
            return {"success": True, "published": 0}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                url = f"{self.base_url}/api/internal/publish-research-papers-batch"
                payload = {"papers": batch}
                resp = await client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("Failed to publish batch to PACR backend", error=str(exc))
            raise

pacr_client = PacrClient()
