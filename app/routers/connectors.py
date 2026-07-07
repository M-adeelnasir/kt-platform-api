"""Connector endpoints: start OAuth (admin connects on the employee's behalf), OAuth callback,
and trigger a manual sync.

The callback is hit by the browser (Google redirect), so it 302s back to the web app. It is
intentionally NOT behind the workspace dependency — it carries a signed `state` instead.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import RequestContext, db_session, get_request_context
from app.schemas import ConnectStartResponse, JobEnqueuedResponse
from config import get_settings
from core.connectors import complete_connection, start_connection
from data.repositories import SourceRepository
from jobs.tasks import sync_source_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.post("/{source_type}/start", response_model=ConnectStartResponse)
def start(
    source_type: str,
    employee_id: uuid.UUID = Query(...),
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> ConnectStartResponse:
    settings = get_settings()
    if source_type == "google" and not settings.google_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google is not configured (set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).",
        )
    if source_type == "github" and not settings.github_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub is not configured (set GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET).",
        )
    if source_type == "atlassian" and not settings.atlassian_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Atlassian is not configured (set ATLASSIAN_CLIENT_ID/SECRET).",
        )
    if source_type == "microsoft" and not settings.microsoft_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft 365 is not configured (set MICROSOFT_CLIENT_ID/SECRET).",
        )
    try:
        url = start_connection(
            session, workspace_id=ctx.workspace_id, employee_id=employee_id, source_type=source_type
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ConnectStartResponse(auth_url=url)


@router.get("/{source_type}/callback")
def oauth_callback(
    source_type: str,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    web = get_settings().web_base_url
    if error or not code or not state:
        return RedirectResponse(url=f"{web}/employees?connect=error")
    try:
        employee_id = complete_connection(state, code)
    except Exception:
        logger.exception("%s oauth callback failed", source_type)
        return RedirectResponse(url=f"{web}/employees?connect=error")
    return RedirectResponse(url=f"{web}/employees/{employee_id}?connect=success")


@router.post("/sources/{source_id}/sync", response_model=JobEnqueuedResponse)
def sync(
    source_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> JobEnqueuedResponse:
    source = SourceRepository(session, ctx.workspace_id).get(source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if not source.encrypted_tokens:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Source is not connected"
        )
    async_result = sync_source_task.delay(str(ctx.workspace_id), str(source_id))
    return JobEnqueuedResponse(task_id=async_result.id)
