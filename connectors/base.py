"""Connector framework core (plan §9).

Every connector normalizes external artifacts into a common `NormalizedDoc` before they hit
the ingest pipeline. Connectors are plugins implementing one interface; the core pipeline does
not special-case any source.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from pydantic import BaseModel, Field


class NormalizedDoc(BaseModel):
    """A source artifact normalized to text + metadata, ready for ingestion."""

    external_id: str  # stable id within the source (dedupe key per source)
    title: str
    text: str
    author: str | None = None
    url: str | None = None
    kind: str = "document"  # drive | docs | gmail | github | ...


class OAuthConnector(Protocol):
    """OAuth-based connector: build a consent URL, exchange the code, then sync documents."""

    type: str

    # Returns (consent_url, code_verifier); the verifier (PKCE) is persisted and passed back.
    def auth_url(self, state: str) -> tuple[str, str]: ...

    def exchange_code(self, code: str, code_verifier: str | None = None) -> dict[str, object]: ...

    # Optionally refresh the access token before a sync. Return the new token dict to persist
    # (e.g. providers with rotating refresh tokens), or None if no refresh is needed.
    def refresh(self, tokens: dict[str, object]) -> dict[str, object] | None: ...

    # Yields normalized documents from the source using stored (decrypted) tokens.
    def sync(self, tokens: dict[str, object]) -> Iterator[NormalizedDoc]: ...


class SyncStats(BaseModel):
    fetched: int = 0
    ingested: int = 0
    chunks: int = 0
    errors: list[str] = Field(default_factory=list)
