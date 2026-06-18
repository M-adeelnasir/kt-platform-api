"""Shared AI types (Pydantic at the boundaries, per plan §3)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class LLMMessage(BaseModel):
    role: Role
    content: str


class LLMResult(BaseModel):
    """Raw model output plus light metadata. RAG/grounding logic lives ABOVE this."""

    text: str
    model: str
    prompt_eval_count: int | None = None
    eval_count: int | None = None


class Citation(BaseModel):
    """A reference back to a retrieved chunk that supports the answer."""

    chunk_id: str
    document_id: str
    title: str | None = None
    url: str | None = None
    snippet: str | None = None


class GroundedAnswer(BaseModel):
    """The oracle's contract object (plan §7). Always returned by the grounded-answer path."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    abstained: bool = False
