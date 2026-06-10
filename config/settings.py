"""Application settings, loaded once from the environment via pydantic-settings.

Per plan §12 / non-negotiables: the backend reads env ONLY through this module, and no
real secret values are ever committed (see .env.example). Everything has a safe local
default so the scaffold runs without cloud keys; secrets default to empty and the code
degrades gracefully (e.g. Pinecone runs in an in-memory stub until a key is provided).

Auth note (MVP): the product is single-workspace with NO external auth provider. Every
request resolves the single seeded member. Real auth is deferred to a later phase (plan §3).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    environment: str = Field(default="development")
    cors_allow_origins: list[str] = Field(default=["http://localhost:3001"])
    # Frontend base URL — OAuth callbacks redirect the browser back here after connecting.
    web_base_url: str = Field(default="http://localhost:3001")

    # --- Postgres (local install for MVP; no pgvector needed) ---
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/continuity"
    )

    # --- Redis / Celery (local install for MVP) ---
    redis_url: str = Field(default="redis://localhost:6379/0")
    # How often the Celery Beat poller auto-syncs every connected source (seconds).
    sync_poll_seconds: int = Field(default=300)

    # --- Pinecone (one namespace = the workspace) ---
    pinecone_api_key: str = Field(default="")
    pinecone_index_name: str = Field(default="kt")  # dim=768, metric=cosine
    pinecone_host: str = Field(default="")
    # Dev fallback when Pinecone is unreachable: a local file-backed vector store shared by
    # the API and Celery worker. Set a real PINECONE_API_KEY to use Pinecone instead.
    vector_store_path: str = Field(default=".local_vectors")

    # --- AI providers (MVP = local Ollama) ---
    ollama_base_url: str = Field(default="http://localhost:11434")
    llm_model: str = Field(default="qwen2.5:7b")
    llm_num_ctx: int = Field(default=8192)  # do NOT rely on Ollama's small default
    embed_model: str = Field(default="nomic-embed-text:latest")
    embed_dim: int = Field(default=768)

    # --- Hosted-model upgrade (leave blank until plan §17 trigger) ---
    openai_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")

    # --- Connector secrets ---
    token_encryption_key: str = Field(default="")
    github_client_id: str = Field(default="")
    github_client_secret: str = Field(default="")
    github_redirect_uri: str = Field(default="http://localhost:8000/connectors/github/callback")

    # Google OAuth (Drive + Docs + Gmail). One consent covers all scopes.
    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")
    google_redirect_uri: str = Field(default="http://localhost:8000/connectors/google/callback")

    # Atlassian OAuth (Jira + Confluence). 3LO; one consent covers both products.
    atlassian_client_id: str = Field(default="")
    atlassian_client_secret: str = Field(default="")
    atlassian_redirect_uri: str = Field(
        default="http://localhost:8000/connectors/atlassian/callback"
    )

    # Microsoft 365 OAuth (Azure AD). One consent covers Outlook mail + OneDrive/SharePoint
    # files + Teams messages. tenant_id "common" allows any org; set a specific tenant for
    # single-org apps. Some scopes (channel messages) require admin consent in Azure.
    microsoft_client_id: str = Field(default="")
    microsoft_client_secret: str = Field(default="")
    microsoft_tenant_id: str = Field(default="common")
    microsoft_redirect_uri: str = Field(
        default="http://localhost:8000/connectors/microsoft/callback"
    )

    # --- Object storage (Supabase Storage S3-compatible / R2 / S3) ---
    storage_endpoint: str = Field(default="")
    storage_region: str = Field(default="")
    storage_access_key_id: str = Field(default="")
    storage_secret_access_key: str = Field(default="")
    storage_bucket: str = Field(default="")

    # --- Observability ---
    sentry_dsn: str = Field(default="")

    @property
    def pinecone_enabled(self) -> bool:
        return bool(self.pinecone_api_key)

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)

    @property
    def atlassian_enabled(self) -> bool:
        return bool(self.atlassian_client_id and self.atlassian_client_secret)

    @property
    def microsoft_enabled(self) -> bool:
        return bool(self.microsoft_client_id and self.microsoft_client_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Use this everywhere; never read os.environ directly."""
    return Settings()
