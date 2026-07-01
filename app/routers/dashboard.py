"""Dashboard endpoint: workspace overview (KPIs, on-notice people, connector health, live jobs)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import RequestContext, db_session, get_request_context
from app.schemas import DashboardOut
from core.dashboard import build_dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
def get_dashboard(
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> DashboardOut:
    return build_dashboard(session, ctx.workspace_id)
