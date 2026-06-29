"""Eval engine (plan §7).

Hermetic: it chunks the fixture corpus, embeds it into an in-memory vector store, retrieves per
question, and runs the real grounded-answer path — no Postgres, no Pinecone. Uses the real local
Ollama embedder + LLM, so it measures the actual stack. Swap the `chunker` to A/B chunking
strategies against the same golden set.

Metrics (deterministic, no LLM judge):
- abstention accuracy: answerable -> answered, unanswerable -> abstained
- retrieval recall: did the expected source document get retrieved
- fact coverage: fraction of expected key facts present in the answer (groundedness proxy)
- citation rate: answerable answers that cite at least one source
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ai.chunking import chunk_text
from ai.providers import get_embedder
from ai.rag import ContextChunk, generate_grounded_answer
from evals.corpus import CORPUS, FixtureDoc
from evals.golden import GOLDEN, GoldenQ
from vector.client import InMemoryVectorStore, VectorRecord

Chunker = Callable[[str], list[str]]
NS = "eval"


def default_chunker(text: str) -> list[str]:
    return [c.text for c in chunk_text(text)]


@dataclass
class QResult:
    question: str
    answerable: bool
    abstained: bool
    abstention_correct: bool
    retrieval_hit: bool | None  # None when not applicable
    facts_found: int
    facts_total: int
    citations: int
    answer: str


def _build_index(
    chunker: Chunker, corpus: list[FixtureDoc]
) -> tuple[InMemoryVectorStore, dict[str, ContextChunk]]:
    embedder = get_embedder()
    store = InMemoryVectorStore()
    id_map: dict[str, ContextChunk] = {}
    for doc in corpus:
        pieces = chunker(doc.text)
        if not pieces:
            continue
        vectors = embedder.embed_documents(pieces)
        records: list[VectorRecord] = []
        for i, (piece, vec) in enumerate(zip(pieces, vectors, strict=True)):
            cid = f"{doc.doc_id}:{i}"
            id_map[cid] = ContextChunk(
                chunk_id=cid, document_id=doc.doc_id, text=piece, title=doc.title
            )
            records.append(VectorRecord(id=cid, values=vec, metadata={"doc_id": doc.doc_id}))
        store.upsert(NS, records)
    return store, id_map


def run_eval(
    chunker: Chunker = default_chunker,
    top_k: int = 8,
    corpus: list[FixtureDoc] | None = None,
    golden: list[GoldenQ] | None = None,
) -> list[QResult]:
    embedder = get_embedder()
    store, id_map = _build_index(chunker, corpus or CORPUS)

    results: list[QResult] = []
    for g in golden or GOLDEN:
        qvec = embedder.embed_query(g.question)
        matches = store.query(NS, qvec, top_k=top_k)
        ctx = [id_map[m.id] for m in matches if m.id in id_map]
        ans = generate_grounded_answer(g.question, ctx)

        retrieved_docs = {c.document_id for c in ctx}
        retrieval_hit = (
            (g.expect_doc in retrieved_docs) if (g.answerable and g.expect_doc) else None
        )
        answer_lower = ans.answer.lower()
        facts_found = sum(1 for s in g.expect_substrings if s.lower() in answer_lower)
        abstention_correct = ans.abstained == (not g.answerable)

        results.append(
            QResult(
                question=g.question,
                answerable=g.answerable,
                abstained=ans.abstained,
                abstention_correct=abstention_correct,
                retrieval_hit=retrieval_hit,
                facts_found=facts_found,
                facts_total=len(g.expect_substrings),
                citations=len(ans.citations),
                answer=ans.answer,
            )
        )
    return results


def summarize(results: list[QResult]) -> dict[str, float]:
    answerable = [r for r in results if r.answerable]
    with_expect = [r for r in answerable if r.retrieval_hit is not None]
    fact_total = sum(r.facts_total for r in answerable)
    fact_found = sum(r.facts_found for r in answerable)

    def pct(num: int, den: int) -> float:
        return round(100.0 * num / den, 1) if den else 0.0

    return {
        "abstention_accuracy": pct(sum(r.abstention_correct for r in results), len(results)),
        "retrieval_recall": pct(sum(bool(r.retrieval_hit) for r in with_expect), len(with_expect)),
        "fact_coverage": pct(fact_found, fact_total),
        "citation_rate": pct(sum(r.citations > 0 for r in answerable), len(answerable)),
    }
