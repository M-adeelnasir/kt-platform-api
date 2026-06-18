"""Cached provider singletons. Import these everywhere instead of constructing adapters."""

from __future__ import annotations

from functools import lru_cache

from ai.embedder import Embedder, build_embedder
from ai.llm import LLMProvider, build_llm_provider


@lru_cache(maxsize=1)
def get_llm() -> LLMProvider:
    return build_llm_provider()


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return build_embedder()
