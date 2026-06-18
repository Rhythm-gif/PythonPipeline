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

    # ── LLM ──────────────────────────────────────────────────
    llm_provider: Literal["openai", "gemini", "openrouter"] = "openai"
    openai_api_key: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    # ── MongoDB ───────────────────────────────────────────────
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "pacr"

    # ── External APIs ─────────────────────────────────────────
    semantic_scholar_api_key: str = ""
    ncbi_api_key: str = ""

    # ── Scheduler ─────────────────────────────────────────────
    fetch_interval_minutes: int = Field(default=60, ge=1)
    papers_per_source: int = Field(default=50, ge=1, le=500)

    # ── Scoring ───────────────────────────────────────────────
    approval_threshold: float = Field(default=80.0, ge=0, le=100)

    # LLM score weight  : 50%
    weight_llm: float = 0.50
    # Citation score    : 20%
    weight_citation: float = 0.20
    # Journal score     : 15%
    weight_journal: float = 0.15
    # Author score      : 15%
    weight_author: float = 0.15

    # ── API Server ────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_debug: bool = False

    # ── Logging ───────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    @field_validator("llm_provider")
    @classmethod
    def validate_llm_key(cls, v: str, info) -> str:  # noqa: ANN001
        # Actual key presence is checked at runtime in the LLM service
        return v

    @property
    def active_llm_key(self) -> str:
        mapping = {
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "openrouter": self.openrouter_api_key,
        }
        key = mapping.get(self.llm_provider, "")
        if not key:
            raise ValueError(
                f"No API key configured for LLM provider '{self.llm_provider}'. "
                f"Set the corresponding environment variable."
            )
        return key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
