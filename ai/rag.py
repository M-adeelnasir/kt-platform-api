"""RAG retrieval + the grounded-answer contract (plan §7).

The oracle answers ONLY from retrieved context, cites the chunks it used, and abstains when
the context is insufficient — never from the base model's own knowledge (plan §2, §3). On the
small local model this discipline is enforced with a strict prompt + JSON output and validated
downstream; the eval harness (later) measures how well it holds.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ai.providers import get_embedder, get_llm
from ai.types import Citation, GroundedAnswer, LLMMessage
from data.models import Workspace
from data.repositories import (
    ChunkRepository,
    DocumentRepository,
    KnowledgeBaseRepository,
    QaQueryRepository,
)
from vector import get_vector_store

logger = logging.getLogger(__name__)

TOP_K = 8

_SYSTEM_PROMPT = (
    "You are a knowledge-transfer assistant. Answer the user's question USING ONLY the "
    "numbered context passages provided. Do not use any outside or prior knowledge.\n"
    "- If the context does not contain enough information to answer, you MUST abstain.\n"
    "- Cite the passages you used by their numbers.\n"
    "Respond with a JSON object of exactly this shape:\n"
    '{"answer": string, "citations": number[], "abstained": boolean}\n'
    'When abstaining, set "abstained" true, "citations" to [], and let "answer" briefly say '
    "the knowledge base does not cover this."
)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    text: str
    score: float
    title: str | None
    url: str | None


def retrieve(
    session: Session,
    *,
    workspace: Workspace,
    knowledge_base_id: uuid.UUID,
    query: str,
    top_k: int = TOP_K,
) -> list[RetrievedChunk]:
    vector = get_embedder().embed_query(query)
    matches = get_vector_store().query(
        workspace.pinecone_namespace,
        vector=vector,
        top_k=top_k,
        metadata_filter={"knowledge_base_id": str(knowledge_base_id)},
    )
    if not matches:
        return []

    chunk_ids = [uuid.UUID(m.id) for m in matches]
    chunks = ChunkRepository(session, workspace.id).get_many(chunk_ids)
    doc_repo = DocumentRepository(session, workspace.id)
    doc_cache: dict[uuid.UUID, tuple[str | None, str | None]] = {}

    retrieved: list[RetrievedChunk] = []
    for m in matches:
        chunk = chunks.get(uuid.UUID(m.id))
        if chunk is None:
            continue  # vector exists but row was deleted — skip
        if chunk.document_id not in doc_cache:
            doc = doc_repo.get(chunk.document_id)
            doc_cache[chunk.document_id] = (doc.title if doc else None, doc.url if doc else None)
        title, url = doc_cache[chunk.document_id]
        retrieved.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                text=chunk.text,
                score=m.score,
                title=title,
                url=url,
            )
        )
    return retrieved


@dataclass(frozen=True)
class ContextChunk:
    """A retrieved chunk ready to feed the grounding prompt (DB-independent — string ids)."""

    chunk_id: str
    document_id: str
    text: str
    title: str | None = None
    url: str | None = None


def _build_context(chunks: list[ContextChunk]) -> str:
    blocks = []
    for i, c in enumerate(chunks, start=1):
        label = c.title or f"document {c.document_id}"
        blocks.append(f"[{i}] (source: {label})\n{c.text}")
    return "\n\n".join(blocks)


def _system_prompt(subject: str | None) -> str:
    prompt = _SYSTEM_PROMPT
    if subject:
        # Single-person KB: every source was collected from this person's accounts/tools, so
        # "who did/wrote/owns/worked on X" is this person unless a passage credits someone else.
        prompt += (
            f"\n\nIMPORTANT CONTEXT: this knowledge base captures the work and knowledge of "
            f"{subject}. All passages were collected from {subject}'s own accounts and tools "
            f"(their documents, emails, code, commit messages, and pull requests). When the "
            f"question asks who did, wrote, built, owns, or worked on something the passages "
            f"describe, the answer is {subject}, unless a passage explicitly credits a different "
            f"person. People who only reviewed or are merely mentioned are NOT the author."
        )
    return prompt


def generate_grounded_answer(
    question: str, context: list[ContextChunk], subject: str | None = None
) -> GroundedAnswer:
    """The grounded-answers contract, independent of the database (plan §7).

    Given the retrieved context, produce {answer, citations, abstained} using ONLY that context.
    `subject` (the knowledge base's person) lets the oracle attribute "who" questions correctly
    in a single-person KB. Reused by both the live oracle path and the eval harness.
    """
    if not context:
        return GroundedAnswer(
            answer="This knowledge base does not contain information to answer that question.",
            citations=[],
            abstained=True,
        )
    messages = [
        LLMMessage(role="system", content=_system_prompt(subject)),
        LLMMessage(
            role="user",
            content=f"Context passages:\n\n{_build_context(context)}\n\nQuestion: {question}",
        ),
    ]
    raw = get_llm().generate(messages, json_mode=True, temperature=0.0)
    return _parse_answer(raw.text, context)


def answer_question(
    session: Session,
    *,
    workspace: Workspace,
    knowledge_base_id: uuid.UUID,
    question: str,
    top_k: int = TOP_K,
) -> GroundedAnswer:
    retrieved = retrieve(
        session,
        workspace=workspace,
        knowledge_base_id=knowledge_base_id,
        query=question,
        top_k=top_k,
    )
    context = [
        ContextChunk(
            chunk_id=str(c.chunk_id),
            document_id=str(c.document_id),
            text=c.text,
            title=c.title,
            url=c.url,
        )
        for c in retrieved
    ]
    kb = KnowledgeBaseRepository(session, workspace.id).get(knowledge_base_id)
    subject = kb.subject_person_name if kb else None
    result = generate_grounded_answer(question, context, subject=subject)
    _log(session, workspace, knowledge_base_id, question, result)
    return result


def _parse_answer(raw_text: str, chunks: list[ContextChunk]) -> GroundedAnswer:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        # Model didn't return valid JSON; treat the text as the answer with no citations.
        logger.warning("grounded answer was not valid JSON; returning unparsed text")
        return GroundedAnswer(answer=raw_text.strip(), citations=[], abstained=False)

    abstained = bool(data.get("abstained", False))
    answer_text = str(data.get("answer", "")).strip()
    citations: list[Citation] = []
    for idx in data.get("citations", []) or []:
        try:
            pos = int(idx)
        except (TypeError, ValueError):
            continue
        if 1 <= pos <= len(chunks):
            c = chunks[pos - 1]
            citations.append(
                Citation(
                    chunk_id=str(c.chunk_id),
                    document_id=str(c.document_id),
                    title=c.title,
                    url=c.url,
                    snippet=c.text[:240],
                )
            )
    # If the model cited nothing and didn't abstain, it likely guessed — abstain defensively.
    if not citations and not abstained:
        abstained = True
    return GroundedAnswer(answer=answer_text, citations=citations, abstained=abstained)


def _log(
    session: Session,
    workspace: Workspace,
    knowledge_base_id: uuid.UUID,
    question: str,
    result: GroundedAnswer,
) -> None:
    QaQueryRepository(session, workspace.id).record(
        knowledge_base_id=knowledge_base_id,
        question=question,
        answer=result.answer,
        citations=[c.model_dump() for c in result.citations],
        abstained=result.abstained,
    )
    session.commit()
