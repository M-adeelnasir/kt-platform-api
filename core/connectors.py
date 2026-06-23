"""Connector orchestration: OAuth connect flow + manual sync into an employee's KB.

- Admin connects on the employee's behalf (plan decision): we open the employee's KB, create a
  source, and hand back a Google consent URL. The callback stores encrypted tokens.
- Sync is manual: pull NormalizedDocs from the connector and run each through the ingest
  pipeline. Re-sync is idempotent (documents are deduped by (source_id, external_id)).
"""

from __future__ import annotations

import datetime
import logging
import uuid
from datetime import UTC

from sqlalchemy.orm import Session

from connectors import SyncStats, get_connector
from core.crypto import decrypt_json, encrypt_json
from core.employees import ensure_kb_for_employee
from core.ingest import ingest_text
from data.db import session_scope
from data.repositories import (
    DocumentRepository,
    EmployeeRepository,
    KnowledgeBaseRepository,
    SourceRepository,
    WorkspaceRepository,
)

logger = logging.getLogger(__name__)


def _sign_state(payload: dict[str, object]) -> str:
    # Fernet tokens are URL-safe and authenticated — good for an OAuth `state`.
    return encrypt_json(payload).decode("utf-8")


def _verify_state(state: str) -> dict[str, object]:
    return decrypt_json(state.encode("utf-8"))


def start_connection(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    employee_id: uuid.UUID,
    source_type: str,
) -> str:
    """Open the employee's KB, ensure a source row, and return the OAuth consent URL."""
    employee = EmployeeRepository(session, workspace_id).get(employee_id)
    if employee is None:
        raise ValueError("employee not found")

    kb = ensure_kb_for_employee(session, workspace_id, employee)
    source = SourceRepository(session, workspace_id).get_or_create(kb.id, type=source_type)
    source.status = "connecting"

    connector = get_connector(source_type)
    state = _sign_state({"source_id": str(source.id), "ws": str(workspace_id)})
    url, code_verifier = connector.auth_url(state)
    # Persist the PKCE verifier so the callback (a different request) can complete the exchange.
    source.config_json = {**(source.config_json or {}), "code_verifier": code_verifier}
    session.commit()
    return url


def complete_connection(state: str, code: str) -> uuid.UUID:
    """OAuth callback: exchange the code, store encrypted tokens. Returns the employee id."""
    data = _verify_state(state)
    source_id = uuid.UUID(str(data["source_id"]))
    workspace_id = uuid.UUID(str(data["ws"]))

    with session_scope() as session:
        source = SourceRepository(session, workspace_id).get(source_id)
        if source is None:
            raise ValueError("source not found")

        connector = get_connector(source.type)
        cv = (source.config_json or {}).get("code_verifier")
        code_verifier = str(cv) if cv else None
        tokens = connector.exchange_code(code, code_verifier=code_verifier)

        kb_early = KnowledgeBaseRepository(session, workspace_id).get(source.knowledge_base_id)
        # GitHub sync filters commits by the employee's username when known.
        if source.type == "github" and kb_early and kb_early.employee_id:
            emp = EmployeeRepository(session, workspace_id).get(kb_early.employee_id)
            if emp and emp.github_username:
                tokens["github_username"] = emp.github_username

        source.encrypted_tokens = encrypt_json(tokens)
        source.status = "connected"
        # Clear the one-time verifier now that it's used.
        source.config_json = {
            k: v for k, v in (source.config_json or {}).items() if k != "code_verifier"
        }
        session.flush()

        kb = KnowledgeBaseRepository(session, workspace_id).get(source.knowledge_base_id)
        employee_id = kb.employee_id if kb and kb.employee_id else None

    if employee_id is None:
        raise ValueError("source is not linked to an employee")
    return employee_id


def sync_source(workspace_id: uuid.UUID, source_id: uuid.UUID) -> SyncStats:
    """Pull documents from a connected source and ingest new ones. Self-contained (Celery)."""
    stats = SyncStats()

    with session_scope() as session:
        workspace = WorkspaceRepository(session).get(workspace_id)
        source = SourceRepository(session, workspace_id).get(source_id)
        if workspace is None or source is None:
            raise ValueError("workspace or source not found")
        if not source.encrypted_tokens:
            raise ValueError("source is not connected")
        tokens = decrypt_json(source.encrypted_tokens)
        kb_id = source.knowledge_base_id
        source_type = source.type

    connector = get_connector(source_type)

    # Refresh the access token if the connector needs it (e.g. Atlassian's rotating tokens),
    # and persist the new token set so the next sync still works.
    refreshed = connector.refresh(tokens)
    if refreshed:
        tokens = refreshed
        with session_scope() as session:
            src = SourceRepository(session, workspace_id).get(source_id)
            if src is not None:
                src.encrypted_tokens = encrypt_json(tokens)

    for doc in connector.sync(tokens):
        stats.fetched += 1
        try:
            with session_scope() as session:
                # Re-fetch workspace inside this session for the ingest call.
                ws = WorkspaceRepository(session).get(workspace_id)
                if ws is None:
                    raise ValueError("workspace disappeared")
                doc_repo = DocumentRepository(session, workspace_id)
                if doc_repo.get_by_external(source_id, doc.external_id) is not None:
                    continue  # already ingested — idempotent re-sync
                result = ingest_text(
                    session,
                    workspace=ws,
                    knowledge_base_id=kb_id,
                    source_id=source_id,
                    external_id=doc.external_id,
                    title=doc.title,
                    text=doc.text,
                    url=doc.url,
                )
                stats.ingested += 1
                stats.chunks += result.chunk_count
        except Exception as exc:  # one doc failing shouldn't abort the whole sync
            logger.warning("ingest failed for %s: %s", doc.external_id, exc)
            stats.errors.append(f"{doc.external_id}: {exc}")

    # Stamp last_synced_at.
    with session_scope() as session:
        src = SourceRepository(session, workspace_id).get(source_id)
        if src is not None:
            src.last_synced_at = datetime.datetime.now(UTC)

    return stats
