"""
Application configuration using pydantic-settings.
All values are read from environment variables or a .env file.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────────────
    app_name: str = "Receipt Intelligence API"
    app_version: str = "0.1.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"

    # ── Server ─────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/receiptdb",
        description="Async PostgreSQL DSN (asyncpg driver)",
    )
    # Sync DSN used only by Alembic migrations (psycopg2)
    database_url_sync: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/receiptdb",
        description="Sync PostgreSQL DSN (psycopg2 driver) — Alembic only",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # ── AI / LLM Providers ────────────────────────────────────────────────────
    llm_provider: Literal["auto", "openrouter", "gemini"] = Field(
        default="auto",
        description="Active LLM provider: 'openrouter', 'gemini', or 'auto' (detects available key)",
    )

    # OpenRouter (google/gemma-3-12b-it)
    openrouter_api_key: str = Field(default="", description="OpenRouter API key")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter API base URL",
    )
    openrouter_model: str = Field(
        default="google/gemma-3-12b-it",
        description="OpenRouter model identifier for multimodal vision & reasoning",
    )
    openrouter_embedding_model: str = Field(
        default="openai/text-embedding-3-small",
        description="OpenRouter embedding model (768 dimensions)",
    )

    # Google Gemini
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    gemini_vision_model: str = "gemini-3.6-flash"
    gemini_embedding_model: str = "gemini-embedding-001"

    def get_effective_provider(self) -> str:
        """Return the active LLM provider ('openrouter' or 'gemini')."""
        if self.llm_provider in ("openrouter", "gemini"):
            return self.llm_provider
        # If Gemini key is set and LLM_PROVIDER is auto, prefer Gemini
        if self.gemini_api_key and not self.gemini_api_key.startswith("your-"):
            return "gemini"
        if self.openrouter_api_key and not self.openrouter_api_key.startswith("your-"):
            return "openrouter"
        return "gemini"


    # ── Authentication ─────────────────────────────────────────────────────────
    auth_secret_key: str = Field(
        default="receipt-intelligence-local-session-secret",
        description=(
            "HMAC secret for signed session cookies. Set a long random value in "
            "production; changing it signs every browser out."
        ),
    )
    auth_session_hours: int = Field(
        default=12,
        ge=1,
        le=24 * 30,
        description="Session lifetime in hours",
    )
    auth_seed_enabled: bool = Field(
        default=True,
        description="Create the demo owner account on startup when no users exist",
    )
    auth_seed_username: str = "meera"
    auth_seed_password: str = "ledger2026"
    auth_seed_display_name: str = "Meera Stores"

    # ── Receipt classifier thresholds ──────────────────────────────────────────
    receipt_confidence_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Min confidence to accept an image as a receipt",
    )

    # ── Product resolution ─────────────────────────────────────────────────────
    fuzzy_match_threshold: int = Field(
        default=85,
        ge=0,
        le=100,
        description="RapidFuzz score threshold (0-100) for product alias matching",
    )
    alias_auto_accept_confidence: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="Confidence above which a new alias is auto-accepted",
    )

    # ── File upload ────────────────────────────────────────────────────────────
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MB
    allowed_image_types: list[str] = ["image/jpeg", "image/png", "image/webp", "image/heic"]

    # ── Discord (Phase 12) ─────────────────────────────────────────────────────
    discord_bot_token: str = Field(default="", description="Discord bot token")
    discord_receipt_channel_id: int = Field(default=0, description="Receipt upload channel ID")
    discord_query_channel_id: int = Field(default=0, description="Query channel ID")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
