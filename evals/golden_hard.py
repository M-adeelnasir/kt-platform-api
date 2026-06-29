"""Golden Q&A for the harder corpus. Each answerable question targets ONE precise fact buried in
a long multi-topic document, so retrieval must surface the right chunk.
"""

from __future__ import annotations

from evals.golden import GoldenQ

GOLDEN_HARD: list[GoldenQ] = [
    GoldenQ("What port does the auth-service run on?", True, "platform-handbook", ["8081"]),
    GoldenQ("How long are auth JWT access tokens valid?", True, "platform-handbook", ["30 min"]),
    GoldenQ("Which Redis database stores auth sessions?", True, "platform-handbook", ["2"]),
    GoldenQ(
        "What Elasticsearch index does catalog-search use?",
        True,
        "platform-handbook",
        ["catalog-v3"],
    ),
    GoldenQ("What is the product catalog cache TTL?", True, "platform-handbook", ["15 min"]),
    GoldenQ("How many times does paygate retry a failed charge?", True, "platform-handbook", ["3"]),
    GoldenQ(
        "What is the gateway's per-API-key rate limit?",
        True,
        "platform-handbook",
        ["200"],
    ),
    GoldenQ("What caused incident INC-4471?", True, "ops-and-incidents", ["reindex"]),
    GoldenQ(
        "What was the root cause of the auth token storm (INC-4502)?",
        True,
        "ops-and-incidents",
        ["allkeys-lru"],
    ),
    GoldenQ("How do you roll back a production deploy?", True, "ops-and-incidents", ["rollback"]),
    GoldenQ("Why did we choose Kong for the gateway?", True, "decisions-and-gotchas", ["plugin"]),
    GoldenQ(
        "What is the gotcha with notifier email templates?",
        True,
        "decisions-and-gotchas",
        ["ascii"],
    ),
    GoldenQ(
        "Are auth refresh tokens revoked when a user changes their password?",
        True,
        "decisions-and-gotchas",
        ["not"],
    ),
    # Unanswerable — must abstain.
    GoldenQ("What is the auth-service maintainer's phone number?", False),
    GoldenQ("What Kubernetes namespace does paygate run in?", False),
]
