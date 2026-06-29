"""Eval runner / report.

    uv run python -m evals.run

Prints a per-question table and aggregate scores. Exits non-zero if any metric falls below its
threshold — so this can gate CI once the LLM is hosted (locally it needs Ollama running).

Thresholds are intentionally lenient for the small local model; tighten them as quality improves.
"""

from __future__ import annotations

from evals.harness import QResult, run_eval, summarize

THRESHOLDS = {
    "abstention_accuracy": 70.0,
    "retrieval_recall": 70.0,
    "fact_coverage": 60.0,
    "citation_rate": 70.0,
}


def _row(r: QResult) -> str:
    kind = "ANS" if r.answerable else "UNANS"
    abst = "abstained" if r.abstained else "answered"
    ok = "ok " if r.abstention_correct else "BAD"
    recall = "-" if r.retrieval_hit is None else ("hit" if r.retrieval_hit else "MISS")
    facts = f"{r.facts_found}/{r.facts_total}" if r.answerable else "-"
    return (
        f"  [{ok}] {kind:5} | {abst:9} | recall={recall:4} | facts={facts:4} | "
        f"cites={r.citations} | {r.question[:54]}"
    )


def main() -> int:
    print("Running eval (local Ollama)…\n")
    results = run_eval()
    for r in results:
        print(_row(r))

    summary = summarize(results)
    print("\n=== Scores ===")
    failed = []
    for metric, value in summary.items():
        threshold = THRESHOLDS.get(metric, 0.0)
        mark = "PASS" if value >= threshold else "FAIL"
        if value < threshold:
            failed.append(metric)
        print(f"  {metric:20}: {value:5.1f}%   (threshold {threshold:.0f}%)  {mark}")

    if failed:
        print(f"\nFAILED: {', '.join(failed)}")
        return 1
    print("\nAll eval thresholds met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
