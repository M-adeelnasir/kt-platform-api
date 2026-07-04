"""Insights endpoints: oracle usage analytics + knowledge-risk (bus-factor) views."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import RequestContext, db_session, get_request_context
from app.schemas import AnalyticsOut, KnowledgeRiskOut

router = APIRouter(tags=["insights"])


@router.get("/analytics", response_model=AnalyticsOut)
def get_analytics(
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> AnalyticsOut:
    from core.insights import build_analytics

    return AnalyticsOut.model_validate(
        build_analytics(session, ctx.workspace_id), from_attributes=True
    )


@router.get("/knowledge-risk", response_model=KnowledgeRiskOut)
def get_knowledge_risk(
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> KnowledgeRiskOut:
    from core.insights import build_knowledge_risk

    return KnowledgeRiskOut.model_validate(
        build_knowledge_risk(session, ctx.workspace_id), from_attributes=True
    )
