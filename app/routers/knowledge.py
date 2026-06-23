"""Knowledge base endpoints: create/list KBs, ingest text, and ask grounded questions.

Ingest is enqueued to Celery (bursty/slow); ask is synchronous (retrieval + one LLM call).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from ai.rag import answer_question
from ai.types import GroundedAnswer
from app.deps import RequestContext, db_session, get_request_context
from app.schemas import (
    AskRequest,
    EmployeeOut,
    IngestTextRequest,
    JobEnqueuedResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseDetailOut,
    KnowledgeBaseOut,
    KnowledgeItemOut,
    OnboardingViewOut,
    UnansweredQuestionOut,
)
from data.repositories import (
    EmployeeRepository,
    KnowledgeBaseRepository,
    KnowledgeItemRepository,
    SourceRepository,
)
from jobs.tasks import ingest_text_task, synthesize_task

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])


@router.post("", response_model=KnowledgeBaseOut)
def create_knowledge_base(
    body: KnowledgeBaseCreate,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> KnowledgeBaseOut:
    kb = KnowledgeBaseRepository(session, ctx.workspace_id).create(body.subject_person_name)
    session.commit()
    return KnowledgeBaseOut.model_validate(kb)


@router.get("", response_model=list[KnowledgeBaseOut])
def list_knowledge_bases(
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> list[KnowledgeBaseOut]:
    kbs = KnowledgeBaseRepository(session, ctx.workspace_id).list()
    return [KnowledgeBaseOut.model_validate(kb) for kb in kbs]


def _require_kb(session: Session, ctx: RequestContext, kb_id: uuid.UUID) -> None:
    if KnowledgeBaseRepository(session, ctx.workspace_id).get(kb_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found"
        )


@router.get("/{kb_id}", response_model=KnowledgeBaseDetailOut)
def get_knowledge_base(
    kb_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> KnowledgeBaseDetailOut:
    kb = KnowledgeBaseRepository(session, ctx.workspace_id).get(kb_id)
    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found"
        )
    employee = None
    if kb.employee_id is not None:
        emp = EmployeeRepository(session, ctx.workspace_id).get(kb.employee_id)
        employee = EmployeeOut.model_validate(emp) if emp else None
    return KnowledgeBaseDetailOut(
        id=kb.id,
        subject_person_name=kb.subject_person_name,
        status=kb.status,
        created_at=kb.created_at,
        employee=employee,
    )


@router.post("/{kb_id}/ingest", response_model=JobEnqueuedResponse)
def ingest_text(
    kb_id: uuid.UUID,
    body: IngestTextRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> JobEnqueuedResponse:
    _require_kb(session, ctx, kb_id)
    source = SourceRepository(session, ctx.workspace_id).get_or_create(kb_id, type="upload")
    session.commit()
    async_result = ingest_text_task.delay(
        workspace_id=str(ctx.workspace_id),
        knowledge_base_id=str(kb_id),
        source_id=str(source.id),
        external_id=str(uuid.uuid4()),
        title=body.title,
        text=body.text,
        url=body.url,
    )
    return JobEnqueuedResponse(task_id=async_result.id)


@router.post("/{kb_id}/ask", response_model=GroundedAnswer)
def ask(
    kb_id: uuid.UUID,
    body: AskRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> GroundedAnswer:
    _require_kb(session, ctx, kb_id)
    return answer_question(
        session, workspace=ctx.workspace, knowledge_base_id=kb_id, question=body.question
    )


@router.post("/{kb_id}/synthesize", response_model=JobEnqueuedResponse)
def synthesize(
    kb_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> JobEnqueuedResponse:
    """Regenerate the KB's knowledge artifacts (overview/gotchas/glossary + gaps), in background."""
    _require_kb(session, ctx, kb_id)
    async_result = synthesize_task.delay(str(ctx.workspace_id), str(kb_id))
    return JobEnqueuedResponse(task_id=async_result.id)


@router.get("/{kb_id}/export")
def export_knowledge_base(
    kb_id: uuid.UUID,
    format: str = Query("md", pattern="^(md|html)$"),
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> Response:
    """Download a knowledge-transfer report for the KB (Markdown or print-ready HTML)."""
    from core.export import build_html, build_markdown

    try:
        if format == "html":
            slug, content = build_html(session, ctx.workspace_id, kb_id)
            # inline so the styled report renders in the browser (Print → Save as PDF).
            media, ext, disposition = "text/html; charset=utf-8", "html", "inline"
        else:
            slug, content = build_markdown(session, ctx.workspace_id, kb_id)
            media, ext, disposition = "text/markdown; charset=utf-8", "md", "attachment"
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'{disposition}; filename="continuity-{slug}.{ext}"'},
    )


@router.get("/{kb_id}/onboarding", response_model=OnboardingViewOut)
def get_onboarding(
    kb_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> OnboardingViewOut:
    """Guided successor reading path over the KB's synthesized knowledge (plan §13.16).

    Returns `ready=False` with no steps when nothing has been synthesized yet.
    """
    from core.onboarding import build_onboarding

    try:
        view = build_onboarding(session, ctx.workspace_id, kb_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return OnboardingViewOut.model_validate(view, from_attributes=True)


@router.get("/{kb_id}/unanswered", response_model=list[UnansweredQuestionOut])
def list_unanswered(
    kb_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> list[UnansweredQuestionOut]:
    """Questions the oracle abstained on for this KB — the gaps worth capturing next."""
    _require_kb(session, ctx, kb_id)
    from core.insights import unanswered_questions

    items = unanswered_questions(session, ctx.workspace_id, kb_id)
    return [UnansweredQuestionOut.model_validate(i, from_attributes=True) for i in items]


@router.get("/{kb_id}/knowledge-items", response_model=list[KnowledgeItemOut])
def list_knowledge_items(
    kb_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> list[KnowledgeItemOut]:
    _require_kb(session, ctx, kb_id)
    items = KnowledgeItemRepository(session, ctx.workspace_id).list_for_kb(kb_id)
    return [KnowledgeItemOut.model_validate(i) for i in items]
