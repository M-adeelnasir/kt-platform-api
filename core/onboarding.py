"""Successor onboarding view (plan §13.16): a guided reading path over a KB's synthesized
knowledge.

Where the admin KB workspace is about *building* the knowledge asset (synthesize, interview,
ingest, export), this assembles the *consumer* experience: an ordered sequence of reading steps
a successor walks through to get up to speed on what their predecessor knew, ending with the
oracle for anything the reading didn't cover.

Cheap and deterministic — it reads existing `knowledge_items` and counts the corpus; no LLM
calls. The grounded oracle (`/ask`) handles follow-up questions separately.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from data.models import KnowledgeItem
from data.repositories import (
    DocumentRepository,
    EmployeeRepository,
    InterviewRepository,
    KnowledgeBaseRepository,
    KnowledgeItemRepository,
)

# Reading order for the synthesized artifacts: understand the system, learn the vocabulary,
# learn what bites you, then see the edges of what's known.
ARTIFACT_ORDER = ["overview", "glossary", "gotcha", "gap"]

# A short, successor-facing framing for each artifact kind (the synthesis titles are written for
# the admin/handover report; these set up the reader).
_KIND_INTRO: dict[str, str] = {
    "overview": "Start here: what this person owned and how the pieces fit together.",
    "glossary": "The vocabulary you'll need — terms, acronyms, and system names.",
    "gotcha": "What will bite you: fragile areas, landmines, and explicit warnings.",
    "gap": "The edges of what was captured — open questions worth chasing down.",
}

_WORDS_PER_MINUTE = 200


@dataclass(frozen=True)
class OnboardingStep:
    kind: str
    title: str
    intro: str
    body: str
    read_minutes: int


@dataclass(frozen=True)
class OnboardingView:
    knowledge_base_id: uuid.UUID
    subject_person_name: str
    subject_title: str | None
    source_count: int
    interview_count: int
    steps: list[OnboardingStep]
    total_read_minutes: int
    ready: bool


def _read_minutes(text: str) -> int:
    words = len(text.split())
    return max(1, math.ceil(words / _WORDS_PER_MINUTE))


def _ordered(items: list[KnowledgeItem]) -> list[KnowledgeItem]:
    return sorted(
        items,
        key=lambda i: ARTIFACT_ORDER.index(i.kind) if i.kind in ARTIFACT_ORDER else 99,
    )


def build_onboarding(
    session: Session, workspace_id: uuid.UUID, kb_id: uuid.UUID
) -> OnboardingView:
    """Assemble the successor reading path for a KB. Raises ValueError if the KB is missing.

    Returns an empty/`ready=False` view when nothing has been synthesized yet (the UI then
    points the successor's admin back to run synthesis).
    """
    kb = KnowledgeBaseRepository(session, workspace_id).get(kb_id)
    if kb is None:
        raise ValueError("knowledge base not found")

    employee = (
        EmployeeRepository(session, workspace_id).get(kb.employee_id) if kb.employee_id else None
    )
    source_count = len(DocumentRepository(session, workspace_id).list_for_kb(kb_id))
    interview_count = len(InterviewRepository(session, workspace_id).list_for_kb(kb_id))

    items = _ordered(list(KnowledgeItemRepository(session, workspace_id).list_for_kb(kb_id)))
    steps = [
        OnboardingStep(
            kind=it.kind,
            title=it.title,
            intro=_KIND_INTRO.get(it.kind, ""),
            body=it.body.strip(),
            read_minutes=_read_minutes(it.body),
        )
        for it in items
    ]

    return OnboardingView(
        knowledge_base_id=kb_id,
        subject_person_name=kb.subject_person_name,
        subject_title=employee.title if employee else None,
        source_count=source_count,
        interview_count=interview_count,
        steps=steps,
        total_read_minutes=sum(s.read_minutes for s in steps),
        ready=bool(steps),
    )
