"""Top up oracle Q&A activity so the Analytics chart shows a real 30-day trend (demo only).

Idempotent guard: skips if the workspace already has plenty of Q&A. Additive to seed_demo.
    uv run python -m scripts.seed_activity
"""

from __future__ import annotations

import datetime as dt
import random

from data.db import session_scope
from data.models import QaQuery
from data.repositories import KnowledgeBaseRepository, QaQueryRepository, WorkspaceRepository

UTC = dt.UTC
random.seed(42)  # reproducible

# Generic-but-plausible oracle questions (answers are illustrative; abstained ones return the
# standard "not covered" line). Spread across whichever KBs exist.
GROUNDED = [
    ("What is Cognita's rate limit?", "60 requests per minute per API key."),
    ("What score auto-advances a candidate?", "A score of 75 or higher."),
    ("How many scoring retries before manual review?", "Up to 3, with backoff."),
    ("Where are the Cognita credentials stored?", "Vault at secret/hiring/cognita."),
    ("What caused incident HR-114?", "Re-scoring candidates already in interview/hired."),
    ("When does the reminder job run?", "Daily at 09:00 UTC, 24h before the interview."),
    ("How do I roll back a bad deploy?", "kubectl rollout undo deploy/<svc>."),
    ("Which table dedupes re-scores?", "assessments, via a unique index on (candidate_id, assessment_id)."),
    ("Who owns the Cognita vendor relationship?", "The platform team; see the contacts artifact."),
    ("What's the on-call handoff process?", "Review alerts, hand off context in the on-call doc."),
    ("Can we auto-reject low scorers?", "No — below-threshold candidates get human review."),
    ("How is candidate data stored?", "In the ats_prod Postgres database."),
]
ABSTAINED = [
    "What is the company's vacation policy?",
    "Who approves salary bands?",
    "What's the office wifi password?",
    "When is the next company offsite?",
    "What's the parental leave policy?",
]

TARGET = 95  # total Q&A to aim for across the last 30 days


def seed_activity() -> None:
    with session_scope() as session:
        ws = WorkspaceRepository(session).get_default()
        if ws is None:
            raise SystemExit("No workspace. Seed the workspace first.")
        qa_repo = QaQueryRepository(session, ws.id)
        existing = list(qa_repo.list_all())
        if len(existing) >= 70:
            raise SystemExit(f"Already have {len(existing)} Q&A — skipping (looks enriched).")

        kbs = list(KnowledgeBaseRepository(session, ws.id).list())
        if not kbs:
            raise SystemExit("No knowledge bases to attach activity to. Run seed_demo first.")
        # weight the hero KBs (earlier-created ones with richer content) a bit heavier
        weights = [max(1, len(kbs) - i) for i in range(len(kbs))]

        to_add = max(0, TARGET - len(existing))
        now = dt.datetime.now(UTC)
        added = 0
        for _ in range(to_add):
            # Upward trend: more questions in recent days (bias toward small day offsets).
            day = int(abs(random.gauss(0, 9))) % 30
            when = (now - dt.timedelta(days=day)).replace(
                hour=random.randint(8, 18), minute=random.randint(0, 59), second=0, microsecond=0
            )
            kb = random.choices(kbs, weights=weights, k=1)[0]
            abstain = random.random() < 0.18
            if abstain:
                q = random.choice(ABSTAINED)
                a = "The knowledge base does not cover this."
            else:
                q, a = random.choice(GROUNDED)
            row = QaQuery(
                workspace_id=ws.id,
                knowledge_base_id=kb.id,
                question=q,
                answer=a,
                citations_json=[],
                abstained=abstain,
            )
            row.created_at = when
            session.add(row)
            added += 1
        print(f"added {added} Q&A across {len(kbs)} KBs (target {TARGET}).")


if __name__ == "__main__":
    seed_activity()
