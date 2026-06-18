"""Embedder interface + the local Ollama `nomic-embed-text` adapter (plan §7).

`nomic-embed-text` requires task prefixes: documents are embedded as `search_document: <text>`
and queries as `search_query: <text>`. Skipping this measurably hurts retrieval, so the
prefixes are baked in here and callers just say embed_documents / embed_query.

Dimension is locked at 768 (the Pinecone index must match). Changing the embedding model is a
migration, not a swap (plan §17).
"""

from __future__ import annotations

from typing import Protocol

from ollama import Client

from config import get_settings

Vector = list[float]


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[Vector]: ...

    def embed_query(self, text: str) -> Vector: ...

    @property
    def dim(self) -> int: ...


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, dim: int) -> None:
        self._client = Client(host=base_url)
        self._model = model
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def _embed(self, inputs: list[str]) -> list[Vector]:
        if not inputs:
            return []
        response = self._client.embed(model=self._model, input=inputs)
        return [list(v) for v in response["embeddings"]]

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        return self._embed([f"search_document: {t}" for t in texts])

    def embed_query(self, text: str) -> Vector:
        return self._embed([f"search_query: {text}"])[0]


def build_embedder() -> Embedder:
    s = get_settings()
    return OllamaEmbedder(base_url=s.ollama_base_url, model=s.embed_model, dim=s.embed_dim)
