"""Ingest pipeline (plan §7, §13 step 8): chunk -> embed -> upsert to the vector store +
store chunk text/metadata in Postgres.

The Pinecone vector id == `chunks.id` (plan §7). Vectors are upserted into the workspace
namespace, with `workspace_id`, `knowledge_base_id`, `document_id` in vector metadata so we
can filter inside the namespace later.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ai.chunking import chunk_text
from ai.providers import get_embedder
from data.models import Workspace
from data.repositories import ChunkRepository, DocumentRepository, WorkspaceRepository
from vector import VectorRecord, get_vector_store


@dataclass(frozen=True)
class IngestResult:
    document_id: uuid.UUID
    chunk_count: int


def ingest_text(
    session: Session,
    *,
    workspace: Workspace,
    knowledge_base_id: uuid.UUID,
    source_id: uuid.UUID,
    external_id: str,
    title: str,
    text: str,
    url: str | None = None,
) -> IngestResult:
    """Ingest one text document. Caller owns the transaction (commits after)."""
    workspace_id = workspace.id
    namespace = workspace.pinecone_namespace

    doc = DocumentRepository(session, workspace_id).create(
        source_id=source_id, external_id=external_id, text=text, title=title, url=url
    )

    pieces = chunk_text(text)
    if not pieces:
        return IngestResult(document_id=doc.id, chunk_count=0)

    chunk_repo = ChunkRepository(session, workspace_id)
    chunks = [
        chunk_repo.add(
            document_id=doc.id, ordinal=p.ordinal, text=p.text, token_count=p.token_count
        )
        for p in pieces
    ]

    embedder = get_embedder()
    vectors = embedder.embed_documents([c.text for c in chunks])

    records = [
        VectorRecord(
            id=str(chunk.id),
            values=vec,
            metadata={
                "workspace_id": str(workspace_id),
                "knowledge_base_id": str(knowledge_base_id),
                "document_id": str(doc.id),
            },
        )
        for chunk, vec in zip(chunks, vectors, strict=True)
    ]
    get_vector_store().upsert(namespace, records)

    now = datetime.now(UTC)
    for chunk in chunks:
        chunk.embedded_at = now
    session.flush()

    return IngestResult(document_id=doc.id, chunk_count=len(chunks))


def ingest_text_standalone(
    *,
    workspace_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    source_id: uuid.UUID,
    external_id: str,
    title: str,
    text: str,
    url: str | None = None,
) -> IngestResult:
    """Self-contained variant for Celery tasks: opens its own session + transaction."""
    from data.db import session_scope

    with session_scope() as session:
        workspace = WorkspaceRepository(session).get(workspace_id)
        if workspace is None:
            raise ValueError(f"workspace {workspace_id} not found")
        return ingest_text(
            session,
            workspace=workspace,
            knowledge_base_id=knowledge_base_id,
            source_id=source_id,
            external_id=external_id,
            title=title,
            text=text,
            url=url,
        )
