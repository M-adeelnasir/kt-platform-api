"""AI interview engine (plan §7 — the differentiator).

Reads the employee's ingested corpus and asks grounded, gap-driven questions one at a time to
capture the tacit knowledge that isn't in any tool (the *why*, gotchas, unfinished work, who to
ask). Multi-turn + resumable. On finish, the transcript is indexed like any other source so the
oracle can answer from interview knowledge too.

Free/local: uses the same Ollama LLMProvider + Embedder + Pinecone as the rest of the app.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ai.providers import get_llm
from ai.types import LLMMessage
from core.ingest import ingest_text
from data.models import Workspace
from data.repositories import (
    DocumentRepository,
    InterviewMessageRepository,
    InterviewRepository,
    KnowledgeBaseRepository,
    KnowledgeItemRepository,
    SourceRepository,
)

DOC_DIGEST_MAX = 20
SNIPPET_CHARS = 400

_SYSTEM_PROMPT = (
    "You are an expert knowledge-transfer interviewer. You are interviewing {subject}, who is "
    "leaving the company, to capture knowledge their successor will need.\n"
    "Rules:\n"
    "- Ask exactly ONE concise question at a time. Output ONLY the question, nothing else.\n"
    "- Ground each question in the provided artifacts — reference a real system, file, repo, PR, "
    "decision, or incident by name.\n"
    "- Prioritize TACIT knowledge that is NOT already written down: the reasons/why behind "
    "decisions, gotchas and failure modes, unfinished or risky work, and who else to ask.\n"
    "- Prefer areas that look important but thin on explanation (knowledge gaps).\n"
    "- Do NOT repeat questions already asked, and do NOT ask things the artifacts already fully "
    "answer. Build on the person's previous answers when useful."
)


@dataclass(frozen=True)
class InterviewTurn:
    interview_id: uuid.UUID
    question: str


def _corpus_digest(session: Session, workspace_id: uuid.UUID, kb_id: uuid.UUID) -> str:
    docs = DocumentRepository(session, workspace_id).list_for_kb(kb_id)
    if not docs:
        return "(No source artifacts have been ingested yet.)"
    parts: list[str] = []
    for d in docs[:DOC_DIGEST_MAX]:
        title = d.title or d.external_id
        snippet = (d.text or "").strip().replace("\n", " ")[:SNIPPET_CHARS]
        parts.append(f"- {title}: {snippet}")
    return "\n".join(parts)


def _history(session: Session, workspace_id: uuid.UUID, interview_id: uuid.UUID) -> str:
    msgs = InterviewMessageRepository(session, workspace_id).list(interview_id)
    lines = []
    for m in msgs:
        who = "Q" if m.role == "ai" else "A"
        lines.append(f"{who}: {m.content}")
    return "\n".join(lines) if lines else "(no questions asked yet)"


def _subject(session: Session, workspace_id: uuid.UUID, kb_id: uuid.UUID) -> str:
    kb = KnowledgeBaseRepository(session, workspace_id).get(kb_id)
    return kb.subject_person_name if kb else "the departing employee"


def _gap_hint(session: Session, workspace_id: uuid.UUID, kb_id: uuid.UUID) -> str:
    """The synthesis 'gap' artifact (identified gaps + suggested questions), if it exists, so
    the interview prioritizes the knowledge most at risk of being lost. Empty if not synthesized.
    """
    items = KnowledgeItemRepository(session, workspace_id).list_for_kb(kb_id)
    for it in items:
        if it.kind == "gap":
            return it.body[:1500]
    return ""


def _generate_question(subject: str, digest: str, history: str, gaps: str = "") -> str:
    gap_block = (
        f"\n\nKnowledge gaps identified earlier (prioritize filling these):\n{gaps}\n"
        if gaps
        else ""
    )
    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT.format(subject=subject)),
        LLMMessage(
            role="user",
            content=(
                f"Artifacts from {subject}'s work:\n{digest}\n"
                f"{gap_block}\n"
                f"Interview so far:\n{history}\n\n"
                "Ask the next single most valuable question."
            ),
        ),
    ]
    result = get_llm().generate(messages, temperature=0.5)
    # Keep just the first non-empty line in case the model adds extra prose.
    for line in result.text.strip().splitlines():
        if line.strip():
            return line.strip().lstrip("-*0123456789. ").strip()
    return result.text.strip()


def start_interview(
    session: Session, *, workspace: Workspace, knowledge_base_id: uuid.UUID
) -> InterviewTurn:
    interview = InterviewRepository(session, workspace.id).create(knowledge_base_id)
    subject = _subject(session, workspace.id, knowledge_base_id)
    digest = _corpus_digest(session, workspace.id, knowledge_base_id)
    gaps = _gap_hint(session, workspace.id, knowledge_base_id)
    question = _generate_question(subject, digest, "(no questions asked yet)", gaps)
    InterviewMessageRepository(session, workspace.id).add(interview.id, "ai", question)
    session.commit()
    return InterviewTurn(interview_id=interview.id, question=question)


def submit_answer(
    session: Session, *, workspace: Workspace, interview_id: uuid.UUID, answer: str
) -> str:
    msg_repo = InterviewMessageRepository(session, workspace.id)
    interview = InterviewRepository(session, workspace.id).get(interview_id)
    if interview is None:
        raise ValueError("interview not found")
    msg_repo.add(interview_id, "human", answer)

    subject = _subject(session, workspace.id, interview.knowledge_base_id)
    digest = _corpus_digest(session, workspace.id, interview.knowledge_base_id)
    history = _history(session, workspace.id, interview_id)
    gaps = _gap_hint(session, workspace.id, interview.knowledge_base_id)
    question = _generate_question(subject, digest, history, gaps)
    msg_repo.add(interview_id, "ai", question)
    session.commit()
    return question


def finish_interview(
    session: Session, *, workspace: Workspace, interview_id: uuid.UUID
) -> dict[str, int]:
    """Mark the interview done and index its Q&A pairs as searchable documents."""
    iv_repo = InterviewRepository(session, workspace.id)
    interview = iv_repo.get(interview_id)
    if interview is None:
        raise ValueError("interview not found")
    kb_id = interview.knowledge_base_id
    subject = _subject(session, workspace.id, kb_id)

    source = SourceRepository(session, workspace.id).get_or_create(kb_id, type="interview")
    doc_repo = DocumentRepository(session, workspace.id)
    msgs = list(InterviewMessageRepository(session, workspace.id).list(interview_id))

    indexed = 0
    chunks = 0
    pending_q: str | None = None
    for i, m in enumerate(msgs):
        if m.role == "ai":
            pending_q = m.content
        elif m.role == "human" and pending_q:
            external_id = f"interview:{interview_id}:{i}"
            if doc_repo.get_by_external(source.id, external_id) is None:
                result = ingest_text(
                    session,
                    workspace=workspace,
                    knowledge_base_id=kb_id,
                    source_id=source.id,
                    external_id=external_id,
                    title=f"Interview with {subject}: {pending_q[:80]}",
                    text=f"Interview question: {pending_q}\nAnswer from {subject}: {m.content}",
                )
                indexed += 1
                chunks += result.chunk_count
            pending_q = None

    interview.status = "done"
    session.commit()
    return {"indexed_answers": indexed, "chunks": chunks}
