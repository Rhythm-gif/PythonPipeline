"""
PACR Pipeline — Next.js API Client
Handles communication with the PACR backend to verify and publish papers.
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from app.config.settings import get_settings
from app.common.logging import get_logger

logger = get_logger(__name__)


class PacrClient:
    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.pacr_backend_url.rstrip("/")
        self.api_key = self.settings.pacr_internal_api_key

    def _auth_headers(self) -> dict:
        """Return auth-only headers (no Content-Type — let httpx set it for multipart)."""
        return {"Authorization": f"Bearer {self.api_key}"}

    async def get_presigned_upload_url(self, identifier: str) -> dict:
        """
        Request a presigned S3 upload URL from the Node.js backend.
        Returns: {"uploadUrl": "https://...", "fileKey": "..."}
        """
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                url = f"{self.base_url}/api/internal/generate-upload-url"
                resp = await client.post(
                    url,
                    json={"identifier": identifier},
                    headers=self._auth_headers()
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error(
                "Failed to request presigned upload URL from backend",
                identifier=identifier,
                error=str(exc),
            )
            raise

    async def publish_single_with_pdf(
        self, paper_dict: dict
    ) -> dict:
        """
        Publish a single approved paper to the Next.js backend.

        Sends a multipart/form-data request with:
          - metadata: stringified JSON of the paper details (including s3_key)

        The backend handles deduplication and attaches the S3 file automatically.
        Returns {"success": true, "published": 1} for new papers,
                {"success": true, "published": 0} for duplicates.
        """
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                url = f"{self.base_url}/api/internal/publish-single-paper"

                # Map Python's 'source_url' to NestJS's expected 'url'
                if paper_dict.get("source_url"):
                    paper_dict["url"] = paper_dict["source_url"]

                # Always multipart — backend expects a consistent format
                files: dict = {
                    "metadata": (None, json.dumps(paper_dict), "application/json"),
                }

                resp = await client.post(
                    url, files=files, headers=self._auth_headers()
                )
                resp.raise_for_status()
                return resp.json()

        except Exception as exc:
            logger.error(
                "Failed to publish single paper to PACR backend",
                title=str(paper_dict.get("title", ""))[:60],
                error=str(exc),
            )
            raise


pacr_client = PacrClient()
