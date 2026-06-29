"""A/B chunking comparison (plan §7).

Runs the eval harness over the HARDER corpus with each chunking strategy and prints a side-by-side
score table, so the choice of chunker is data-driven, not a guess. Uses a small top_k so retrieval
is selective (chunk boundaries actually matter).

    uv run python -m evals.ab_chunking
"""

from __future__ import annotations

from ai.chunkers import recursive_chunks, semantic_chunks, window_chunks
from ai.providers import get_embedder
from evals.corpus_hard import CORPUS_HARD
from evals.golden_hard import GOLDEN_HARD
from evals.harness import Chunker, run_eval, summarize

TOP_K = 3
METRICS = ["abstention_accuracy", "retrieval_recall", "fact_coverage", "citation_rate"]


def _strategies() -> dict[str, Chunker]:
    embedder = get_embedder()
    return {
        "window": window_chunks,
        "recursive": recursive_chunks,
        "semantic": lambda t: semantic_chunks(t, embedder),
    }


def main() -> int:
    print(f"A/B chunking on the hard corpus (top_k={TOP_K})…\n")
    rows: dict[str, dict[str, float]] = {}
    chunk_counts: dict[str, int] = {}
    for name, chunker in _strategies().items():
        chunk_counts[name] = sum(len(chunker(d.text)) for d in CORPUS_HARD)
        results = run_eval(chunker, top_k=TOP_K, corpus=CORPUS_HARD, golden=GOLDEN_HARD)
        rows[name] = summarize(results)
        print(f"  ran '{name}' ({chunk_counts[name]} chunks)")

    print("\n=== Scores by chunking strategy (hard corpus) ===")
    header = f"{'metric':22}" + "".join(f"{n:>12}" for n in rows)
    print(header)
    print("-" * len(header))
    for metric in METRICS:
        line = f"{metric:22}" + "".join(f"{rows[n][metric]:>11.1f}%" for n in rows)
        print(line)
    print(f"{'# chunks':22}" + "".join(f"{chunk_counts[n]:>12}" for n in rows))

    # Pick a winner by total score (ties broken toward the simpler/cheaper strategy).
    order = ["window", "recursive", "semantic"]  # simpler first
    best = max(order, key=lambda n: (sum(rows[n][m] for m in METRICS), -order.index(n)))
    print(f"\nWinner (by summed metrics, ties to simpler): {best}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
