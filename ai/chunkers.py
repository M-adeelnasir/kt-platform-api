"""Chunking strategies for A/B comparison (plan §7).

All return a plain ``list[str]`` so the eval harness can swap them against the same golden set:

- ``window_chunks``    — the current fixed-size sliding word window (baseline).
- ``recursive_chunks`` — structure-aware: split on paragraphs, then sentences, packed to a size
  budget with overlap (keeps topics/paragraphs intact).
- ``semantic_chunks``  — sentence-embedding breakpoints: start a new chunk where meaning shifts.

Pick the winner with the eval harness, then wire it into the ingest pipeline.
"""

from __future__ import annotations

import math
import re

from ai.chunking import WORDS_PER_CHUNK, chunk_text
from ai.embedder import Embedder

_PARA_RE = re.compile(r"\n\s*\n")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def window_chunks(text: str) -> list[str]:
    return [c.text for c in chunk_text(text)]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.split(text.replace("\n", " ")) if s.strip()]


def _split_units(text: str, max_words: int) -> list[str]:
    """Break text into units no larger than max_words: paragraphs, then sentences if needed."""
    units: list[str] = []
    for para in (p.strip() for p in _PARA_RE.split(text) if p.strip()):
        if len(para.split()) <= max_words:
            units.append(para)
            continue
        cur: list[str] = []
        cur_w = 0
        for sent in _sentences(para):
            w = len(sent.split())
            if cur_w + w > max_words and cur:
                units.append(" ".join(cur))
                cur, cur_w = [], 0
            cur.append(sent)
            cur_w += w
        if cur:
            units.append(" ".join(cur))
    return units


def recursive_chunks(
    text: str, max_words: int = WORDS_PER_CHUNK, overlap_words: int = 60
) -> list[str]:
    """Pack paragraph/sentence units into chunks up to max_words, with a small word overlap."""
    units = _split_units(text, max_words)
    if not units:
        return []
    chunks: list[str] = []
    cur: list[str] = []
    cur_w = 0
    for unit in units:
        w = len(unit.split())
        if cur_w + w > max_words and cur:
            chunks.append("\n\n".join(cur))
            # Seed the next chunk with the tail of this one for continuity.
            tail = " ".join("\n\n".join(cur).split()[-overlap_words:]) if overlap_words else ""
            cur = [tail] if tail else []
            cur_w = len(tail.split())
        cur.append(unit)
        cur_w += w
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def semantic_chunks(
    text: str,
    embedder: Embedder,
    max_words: int = WORDS_PER_CHUNK,
    breakpoint_percentile: int = 80,
) -> list[str]:
    """Split where consecutive-sentence similarity drops below a percentile (a topic shift),
    with a hard max_words cap so chunks never blow past the context budget.
    """
    sents = _sentences(text)
    if len(sents) <= 1:
        return [text.strip()] if text.strip() else []

    vectors = embedder.embed_documents(sents)
    distances = [1.0 - _cosine(vectors[i], vectors[i + 1]) for i in range(len(sents) - 1)]
    # Breakpoint threshold = the Nth percentile of gap distances.
    ordered = sorted(distances)
    idx = min(len(ordered) - 1, int(len(ordered) * breakpoint_percentile / 100))
    threshold = ordered[idx]

    chunks: list[str] = []
    cur: list[str] = [sents[0]]
    cur_w = len(sents[0].split())
    for i in range(1, len(sents)):
        w = len(sents[i].split())
        boundary = distances[i - 1] >= threshold or cur_w + w > max_words
        if boundary and cur:
            chunks.append(" ".join(cur))
            cur, cur_w = [], 0
        cur.append(sents[i])
        cur_w += w
    if cur:
        chunks.append(" ".join(cur))
    return chunks
