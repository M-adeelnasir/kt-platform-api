"""Idempotent seed for the single-workspace MVP (plan §13 step 3).

Creates exactly one workspace and one default member if none exist. Safe to run repeatedly
(e.g. from a migration or a CLI). The Pinecone namespace is derived from the workspace id so
it is stable and unique.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from data.models import Member, Workspace
from data.repositories import MemberRepository, WorkspaceRepository

DEFAULT_WORKSPACE_NAME = "Default Workspace"
DEFAULT_MEMBER_EXTERNAL_ID = "local-user"
DEFAULT_MEMBER_NAME = "Local User"


def seed_default_workspace(session: Session, name: str = DEFAULT_WORKSPACE_NAME) -> Workspace:
    """Ensure the single MVP workspace exists; return it."""
    repo = WorkspaceRepository(session)
    existing = repo.get_default()
    if existing is not None:
        return existing

    # Create with a placeholder namespace, then set it to a stable id-derived value.
    ws = repo.create(name=name, pinecone_namespace="pending")
    ws.pinecone_namespace = f"ws_{ws.id.hex}"
    session.flush()
    return ws


def seed_default_member(session: Session, workspace: Workspace) -> Member:
    """Ensure the single MVP member exists (owner role); return it."""
    repo = MemberRepository(session, workspace.id)
    return repo.upsert(
        external_user_id=DEFAULT_MEMBER_EXTERNAL_ID,
        role="owner",
        name=DEFAULT_MEMBER_NAME,
    )


def seed_all(session: Session) -> tuple[Workspace, Member]:
    """Seed the workspace and its default member together."""
    workspace = seed_default_workspace(session)
    member = seed_default_member(session, workspace)
    return workspace, member


def main() -> None:
    """CLI entry: `uv run python -m data.seed` (run after `alembic upgrade head`)."""
    from data.db import session_scope

    with session_scope() as session:
        workspace, member = seed_all(session)
        print(
            f"Seeded workspace {workspace.id} ({workspace.name}), "
            f"namespace={workspace.pinecone_namespace}, member={member.external_user_id}"
        )


if __name__ == "__main__":
    main()
