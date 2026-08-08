"""
AWS S3 Service for direct file streaming.
Mirrors the architecture of the PACR Backend's S3.service.ts
"""
from __future__ import annotations

import logging
import uuid
from typing import AsyncGenerator

import aioboto3

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class S3Service:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.bucket = self.settings.aws_s3_bucket
        
        # Configure the aioboto3 session mirroring Node.js Config
        self.session = aioboto3.Session(
            aws_access_key_id=self.settings.aws_access_key_id,
            aws_secret_access_key=self.settings.aws_secret_access_key,
            region_name=self.settings.aws_region,
        )
        
        # S3 client kwargs, matching forcePathStyle logic
        self.client_kwargs = {
            "endpoint_url": self.settings.aws_s3_endpoint or None,
            "config": None, # boto3 handles forcePathStyle based on endpoint automatically for most custom endpoints
        }

    async def upload_stream(
        self, stream: AsyncGenerator[bytes, None], content_type: str = "application/pdf"
    ) -> str:
        """
        Stream an async generator (e.g. from httpx) directly to S3.
        Generates a uuid filename and returns the S3 key.
        """
        file_key = f"pdf/journal/{uuid.uuid4().hex}.pdf"
        logger.info("Starting direct S3 stream upload", key=file_key)

        class AsyncGeneratorReader:
            """Adapter to convert an async generator into an async file-like object for boto3."""
            def __init__(self, async_gen: AsyncGenerator[bytes, None]):
                self.async_gen = async_gen
                self.buffer = bytearray()

            async def read(self, n: int = -1) -> bytes:
                # If n is -1, drain the entire generator
                if n == -1:
                    async for chunk in self.async_gen:
                        self.buffer.extend(chunk)
                    data, self.buffer = bytes(self.buffer), bytearray()
                    return data
                
                # Loop until we have enough bytes in buffer, or generator is exhausted
                while len(self.buffer) < n:
                    try:
                        chunk = await self.async_gen.__anext__()
                        self.buffer.extend(chunk)
                    except StopAsyncIteration:
                        break
                
                # Take up to n bytes
                data = bytes(self.buffer[:n])
                del self.buffer[:n]
                return data

        async with self.session.client("s3", **self.client_kwargs) as s3:
            # upload_fileobj uses multipart uploads automatically under the hood
            # matching the PACR Node.js Upload module
            await s3.upload_fileobj(
                AsyncGeneratorReader(stream),
                self.bucket,
                file_key,
                ExtraArgs={"ContentType": content_type}
            )
            
        logger.info("S3 stream upload complete", key=file_key)
        return file_key


s3_service = S3Service()
