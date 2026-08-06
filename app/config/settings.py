"""
PACR Pipeline — Core Configuration
All settings are loaded from environment variables / .env file.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Next.js PACR Backend ─────────────────────────────────────────────────
    pacr_backend_url: str = "http://localhost:3000"
    pacr_internal_api_key: str = "change-me-in-production"

    # ── External APIs ─────────────────────────────────────────
    semantic_scholar_api_key: str = ""
    ncbi_api_key: str = ""
    # Email address required by Unpaywall API Terms of Service.
    # Leave empty to skip Unpaywall lookups entirely.
    unpaywall_email: str = ""

    # ── PDF Resolver ──────────────────────────────────────────
    # Maximum PDF download size in megabytes (default: 50 MB)
    pdf_max_size_mb: int = Field(default=50, ge=1, le=500)
    # Maximum concurrent outbound requests per hostname
    pdf_domain_concurrency: int = Field(default=2, ge=1, le=10)

    # ── Scheduler ─────────────────────────────────────────────
    cron_expression: str = "0 0 * * *"
    papers_per_source: int = Field(default=50, ge=1, le=500)

    # ── API Server ────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_debug: bool = False

    # ── Logging ───────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
