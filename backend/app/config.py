"""Application configuration, loaded from environment via pydantic-settings.

All cross-cutting knobs (DB, Redis, storage path, upload limits, embedding model)
live here so there's one place to find them. Settings are read once at startup
and injected as a dependency — no scattered `os.getenv` calls in handlers.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Infrastructure ---
    database_url: str = Field(
        default="postgresql+asyncpg://tenderiq:tenderiq@localhost:5432/tenderiq"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    # --- File storage (Architecture §5.1: local volume for MVP) ---
    storage_dir: str = Field(default="./storage")

    # --- Upload limits (REQ-001 Preconditions) ---
    max_upload_mb: int = Field(default=50)

    # --- Rate limiting (Redis sliding window, per company_id) ---
    rate_limit_rpm: int = Field(default=60)
    default_monthly_doc_limit: int = Field(default=100)

    # --- Embeddings (Gemini free-tier model) ---
    google_api_key: str = Field(default="")
    embedding_model: str = Field(default="gemini-embedding-001")
    embedding_dimensions: int = Field(default=768)

    # --- CORS (Frontend origin only — never "*" with credentials) ---
    cors_origins: str = Field(default="http://localhost:3000")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Override in tests via `get_settings.cache_clear()`."""
    return Settings()
