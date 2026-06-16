"""Celery tasks. Ingestion runs here, decoupled from request handlers (plan §5)."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

_API_ROOT = str(Path(__file__).resolve().parent.parent)


def _ensure_api_on_path() -> None:
    """Put api/ on sys.path at task-execution time.

    The project runs from source (not pip-installed). Celery loads the app with cwd briefly on
    the path, then executes tasks with a cleaned path, so a module-level insert gets wiped —
    we re-assert it here, inside the task, where the lazy domain imports happen.
    """
    if _API_ROOT not in sys.path:
        sys.path.insert(0, _API_ROOT)


# Import the app after defining the bootstrap (the app import itself happens during the brief
# window where Celery has cwd on the path).
_ensure_api_on_path()
from jobs.celery_app import celery_app  # noqa: E402


@celery_app.task(name="jobs.health_check")  # type: ignore[untyped-decorator]  # celery is untyped
def health_check(echo: str = "ok") -> str:
    """Trivial task used to verify the worker + broker are alive end-to-end."""
    return f"pong:{echo}"


@celery_app.task(name="jobs.ingest_text")  # type: ignore[untyped-decorator]  # celery is untyped
def ingest_text_task(
    workspace_id: str,
    knowledge_base_id: str,
    source_id: str,
    external_id: str,
    title: str,
    text: str,
    url: str | None = None,
) -> dict[str, object]:
    """Chunk -> embed -> upsert a text document. Returns {document_id, chunk_count}."""
    _ensure_api_on_path()
    from core.ingest import ingest_text_standalone

    result = ingest_text_standalone(
        workspace_id=uuid.UUID(workspace_id),
        knowledge_base_id=uuid.UUID(knowledge_base_id),
        source_id=uuid.UUID(source_id),
        external_id=external_id,
        title=title,
        text=text,
        url=url,
    )
    return {"document_id": str(result.document_id), "chunk_count": result.chunk_count}


@celery_app.task(name="jobs.sync_source")  # type: ignore[untyped-decorator]  # celery is untyped
def sync_source_task(workspace_id: str, source_id: str) -> dict[str, object]:
    """Pull a connected source and ingest new documents. Returns SyncStats as a dict."""
    _ensure_api_on_path()
    from core.connectors import sync_source

    stats = sync_source(uuid.UUID(workspace_id), uuid.UUID(source_id))
    return stats.model_dump()


@celery_app.task(name="jobs.synthesize")  # type: ignore[untyped-decorator]  # celery is untyped
def synthesize_task(workspace_id: str, knowledge_base_id: str) -> dict[str, object]:
    """Regenerate a KB's knowledge artifacts (overview/gotchas/glossary + gaps)."""
    _ensure_api_on_path()
    from core.synthesis import synthesize

    return synthesize(uuid.UUID(workspace_id), uuid.UUID(knowledge_base_id))


@celery_app.task(name="jobs.poll_sources")  # type: ignore[untyped-decorator]  # celery is untyped
def poll_sources_task() -> dict[str, object]:
    """Beat-scheduled: enqueue a sync for every connected source (idempotent — only new items
    get ingested). This is the automatic-capture loop.
    """
    _ensure_api_on_path()
    from data.db import session_scope
    from data.repositories import SourceRepository, WorkspaceRepository

    enqueued = 0
    with session_scope() as session:
        for ws in WorkspaceRepository(session).list_all():
            for src in SourceRepository(session, ws.id).list_connected():
                sync_source_task.delay(str(ws.id), str(src.id))
                enqueued += 1
    return {"enqueued": enqueued}
