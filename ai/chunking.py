"""Text chunking for the RAG pipeline (plan §7: ~500-800 tokens, ~15% overlap).

Token counting is approximated by word count (~1.33 tokens/word for English) to avoid a
tokenizer dependency on the MVP. Chunks carry their ordinal so retrieval can cite position.
"""

from __future__ import annotations

from dataclasses import dataclass

WORDS_PER_CHUNK = 450  # ~600 tokens
OVERLAP_WORDS = 68  # ~15% overlap
_TOKENS_PER_WORD = 1.33


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    text: str
    token_count: int


def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * _TOKENS_PER_WORD)


def chunk_text(
    text: str,
    words_per_chunk: int = WORDS_PER_CHUNK,
    overlap_words: int = OVERLAP_WORDS,
) -> list[TextChunk]:
    """Split text into overlapping word-windows. Returns [] for empty/whitespace input."""
    words = text.split()
    if not words:
        return []
    if overlap_words >= words_per_chunk:
        raise ValueError("overlap_words must be smaller than words_per_chunk")

    step = words_per_chunk - overlap_words
    chunks: list[TextChunk] = []
    ordinal = 0
    for start in range(0, len(words), step):
        window = words[start : start + words_per_chunk]
        if not window:
            break
        chunk_str = " ".join(window)
        chunks.append(
            TextChunk(ordinal=ordinal, text=chunk_str, token_count=estimate_tokens(chunk_str))
        )
        ordinal += 1
        if start + words_per_chunk >= len(words):
            break
    return chunks
