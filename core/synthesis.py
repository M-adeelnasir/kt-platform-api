"""Synthesis (plan §7): turn the ingested + interview corpus into structured, browsable
knowledge artifacts a successor can read — overview, gotchas, glossary — plus a gap detector.

Each artifact is generated ONLY from the person's actual material (same grounding discipline as
the oracle). Free/local: uses the Ollama LLMProvider. Runs as a Celery job (several LLM calls).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ai.providers import get_llm
from ai.types import LLMMessage
from data.db import session_scope
from data.models import Workspace
from data.repositories import (
    DocumentRepository,
    KnowledgeBaseRepository,
    KnowledgeItemRepository,
    WorkspaceRepository,
)

DOC_MAX = 30
PER_DOC_CHARS = 800
DIGEST_CHAR_BUDGET = 7000  # keep prompt within the local model's context window


@dataclass(frozen=True)
class _Artifact:
    kind: str
    title: str
    instruction: str


_ARTIFACTS: list[_Artifact] = [
    _Artifact(
        "overview",
        "System Overview",
        "Write a clear overview of what this person owned and how their systems/work fit "
        "together. Use short paragraphs and bullet points. Cover the main systems, what they do, "
        "and how data/work flows between them.",
    ),
    _Artifact(
        "gotcha",
        "Gotchas & Landmines",
        "Extract the gotchas, landmines, risks, fragile areas, and explicit 'never do X' "
        "warnings. Return a bullet list, each item specific and actionable. Include the reason "
        "or consequence where the material gives one.",
    ),
    _Artifact(
        "glossary",
        "Glossary",
        "Extract domain-specific terms, acronyms, system names, and jargon, and define each "
        "briefly. Format each line as 'Term — definition'. Only include terms that appear in the "
        "material.",
    ),
]

_GAP_ARTIFACT = _Artifact(
    "gap",
    "Knowledge Gaps",
    "Identify the most important topics that appear in the material but are thin on explanation, "
    "unanswered, or look risky to lose. Then propose 3-7 specific questions to ask this person "
    "before they leave to fill those gaps. Return two sections: 'Gaps' (bullets) and 'Suggested "
    "interview questions' (numbered).",
)

_SYSTEM_PROMPT = (
    "You are a knowledge-transfer analyst producing a handover document for {subject}, who is "
    "leaving. Use ONLY the provided material about their work — do not invent facts or use "
    "outside knowledge. If the material is insufficient for a section, say so briefly rather "
    "than guessing. Be concise and concrete; reference real system, file, and decision names."
)

_VERIFY_SYSTEM = (
    "You are a strict fact-checker for a handover document. You are given the SOURCE MATERIAL "
    "and a DRAFT section. Return a corrected version of the draft that keeps ONLY statements "
    "directly supported by the source material, in the same format.\n"
    "- Remove any sentence, bullet, or 'Term — definition' line not supported by the material.\n"
    "- Fix mislabeled facts (e.g. a person's name wrongly described as a tool or system).\n"
    "- Do NOT add new information or commentary. Output only the corrected section text."
)


def _verify(subject: str, digest: str, title: str, body: str) -> str:
    """Second pass: strip/correct any claim in `body` not supported by the source material.

    Falls back to the original body if the check fails or returns nothing (never lose content
    to a flaky check)."""
    try:
        messages = [
            LLMMessage(role="system", content=_VERIFY_SYSTEM),
            LLMMessage(
                role="user",
                content=(
                    f"SOURCE MATERIAL about {subject}:\n\n{digest}\n\n"
                    f"DRAFT section ({title}):\n\n{body}\n\n"
                    "Return the corrected, fully-supported version of the draft section."
                ),
            ),
        ]
        verified = get_llm().generate(messages, temperature=0.0).text.strip()
        return verified or body
    except Exception:
        return body


def _digest(
    session: Session, workspace_id: uuid.UUID, kb_id: uuid.UUID
) -> tuple[str, list[dict[str, object]]]:
    docs = DocumentRepository(session, workspace_id).list_for_kb(kb_id)
    parts: list[str] = []
    refs: list[dict[str, object]] = []
    total = 0
    for d in docs[:DOC_MAX]:
        title = d.title or d.external_id
        snippet = (d.text or "").strip().replace("\r", "")[:PER_DOC_CHARS]
        block = f"### {title}\n{snippet}"
        if total + len(block) > DIGEST_CHAR_BUDGET:
            break
        parts.append(block)
        refs.append({"document_id": str(d.id), "title": title})
        total += len(block)
    return ("\n\n".join(parts) if parts else "(no material)"), refs


def _generate(subject: str, digest: str, artifact: _Artifact) -> str:
    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT.format(subject=subject)),
        LLMMessage(
            role="user",
            content=(
                f"Material about {subject}'s work:\n\n{digest}\n\nTask: {artifact.instruction}"
            ),
        ),
    ]
    return get_llm().generate(messages, temperature=0.3).text.strip()


def synthesize(workspace_id: uuid.UUID, knowledge_base_id: uuid.UUID) -> dict[str, object]:
    """Regenerate the KB's knowledge artifacts (overview, gotchas, glossary + gaps)."""
    with session_scope() as session:
        workspace: Workspace | None = WorkspaceRepository(session).get(workspace_id)
        kb = KnowledgeBaseRepository(session, workspace_id).get(knowledge_base_id)
        if workspace is None or kb is None:
            raise ValueError("workspace or knowledge base not found")
        subject = kb.subject_person_name

        digest, refs = _digest(session, workspace_id, knowledge_base_id)
        if digest == "(no material)":
            raise ValueError("no ingested material to synthesize yet")

        item_repo = KnowledgeItemRepository(session, workspace_id)
        item_repo.delete_for_kb(knowledge_base_id)  # fresh regeneration

        count = 0
        for artifact in [*_ARTIFACTS, _GAP_ARTIFACT]:
            body = _generate(subject, digest, artifact)
            # Verify factual artifacts against the source; the gap section is speculative by
            # design (gaps + suggested questions), so it's not fact-checked.
            if body and artifact.kind != "gap":
                body = _verify(subject, digest, artifact.title, body)
            if body:
                item_repo.create(
                    knowledge_base_id=knowledge_base_id,
                    kind=artifact.kind,
                    title=artifact.title,
                    body=body,
                    source_refs=refs,
                )
                count += 1
        return {"items": count}
