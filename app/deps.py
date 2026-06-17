"""Request dependencies: DB session + the request context.

MVP has no external auth provider (Clerk was dropped for simplicity). Every request resolves
the single seeded workspace and its default member, so `workspace_id` is always present and
scoping still holds (plan §3, §10). `get_request_context` is the seam where real auth slots
in later — swap the default-member lookup for a verified-identity lookup, nothing else changes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from data.db import get_db
from data.models import Member, Workspace
from data.repositories import MemberRepository, WorkspaceRepository


@dataclass(frozen=True)
class RequestContext:
    """Everything a workspace-scoped handler needs about the caller."""

    workspace: Workspace
    member: Member

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.workspace.id


def db_session() -> Iterator[Session]:
    yield from get_db()


def get_request_context(session: Session = Depends(db_session)) -> RequestContext:
    workspace = WorkspaceRepository(session).get_default()
    if workspace is None:
        # The seed migration should have created this. Fail loudly rather than guess.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No workspace is seeded. Run `alembic upgrade head`.",
        )
    member = MemberRepository(session, workspace.id).get_default()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No member is seeded. Run `alembic upgrade head`.",
        )
    return RequestContext(workspace=workspace, member=member)
