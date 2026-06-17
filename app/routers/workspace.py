"""Workspace-scoped endpoints. The `/me` route satisfies the Phase 0 DoD: a logged-in user
hits a FastAPI endpoint that returns workspace-scoped data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import RequestContext, get_request_context
from app.schemas import MemberOut, WorkspaceContextOut

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("/me", response_model=WorkspaceContextOut)
def get_me(ctx: RequestContext = Depends(get_request_context)) -> WorkspaceContextOut:
    return WorkspaceContextOut(
        workspace_id=ctx.workspace.id,
        workspace_name=ctx.workspace.name,
        pinecone_namespace=ctx.workspace.pinecone_namespace,
        member=MemberOut.model_validate(ctx.member),
    )
