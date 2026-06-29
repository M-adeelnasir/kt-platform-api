"""Golden Q&A set for the eval harness.

Each question declares whether it's answerable from the corpus, which document should be
retrieved (recall check), and the key facts the answer must contain (a cheap, deterministic
groundedness proxy that needs no LLM judge). Unanswerable questions must be abstained on.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GoldenQ:
    question: str
    answerable: bool
    expect_doc: str | None = None  # doc_id that should appear in retrieval
    expect_substrings: list[str] = field(default_factory=list)  # lowercased facts in the answer


GOLDEN: list[GoldenQ] = [
    GoldenQ("What time does the nightly reconciliation job run?", True, "runbook", ["02:30"]),
    GoldenQ(
        "How do I re-run the billing reconciler for a specific day?",
        True,
        "runbook",
        ["billing_reconciler.run"],
    ),
    GoldenQ(
        "How many times does the reconciler retry a failed charge before giving up?",
        True,
        "runbook",
        ["5"],
    ),
    GoldenQ("What is the reconciliation discrepancy tolerance?", True, "runbook", ["0.50"]),
    GoldenQ("What is the Stripe API rate limit?", True, "gotchas", ["100"]),
    GoldenQ(
        "Which two Postgres tables are central to billing?",
        True,
        "overview",
        ["payment_attempts", "ledger_entries"],
    ),
    GoldenQ("Why was Stripe chosen over Adyen?", True, "decisions", ["integration"]),
    GoldenQ("What does DLQ stand for?", True, "contacts", ["dead-letter"]),
    GoldenQ("Who is the Stripe technical account manager?", True, "contacts", ["maria"]),
    GoldenQ(
        "How many customers still use the pending_v1 invoice status?",
        True,
        "gotchas",
        ["3"],
    ),
    GoldenQ(
        "What two jobs must never run at the same time, and why?",
        True,
        "gotchas",
        ["monthly-export"],
    ),
    # Unanswerable — must abstain (no such info in the corpus).
    GoldenQ("What is Adeel's salary?", False),
    GoldenQ("What is our Kubernetes pod autoscaling configuration?", False),
    GoldenQ("What is the office Wi-Fi password?", False),
]
