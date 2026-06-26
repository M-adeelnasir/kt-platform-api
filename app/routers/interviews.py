"""Interview endpoints: start a grounded interview for a KB, answer turns, finish (which indexes
the transcript so the oracle can use it).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.deps import RequestContext, db_session, get_request_context
from app.schemas import (
    InterviewAnswerRequest,
    InterviewDetailOut,
    InterviewFinishResponse,
    InterviewMessageOut,
    InterviewQuestionResponse,
    InterviewStartResponse,
)
from core.interview import finish_interview, start_interview, submit_answer
from data.repositories import (
    InterviewMessageRepository,
    InterviewRepository,
    KnowledgeBaseRepository,
)

router = APIRouter(tags=["interviews"])


@router.post("/knowledge-bases/{kb_id}/interviews", response_model=InterviewStartResponse)
def start(
    kb_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> InterviewStartResponse:
    if KnowledgeBaseRepository(session, ctx.workspace_id).get(kb_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found"
        )
    turn = start_interview(session, workspace=ctx.workspace, knowledge_base_id=kb_id)
    return InterviewStartResponse(interview_id=turn.interview_id, question=turn.question)


@router.get("/interviews/{interview_id}", response_model=InterviewDetailOut)
def get_interview(
    interview_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> InterviewDetailOut:
    iv = InterviewRepository(session, ctx.workspace_id).get(interview_id)
    if iv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found")
    msgs = InterviewMessageRepository(session, ctx.workspace_id).list(interview_id)
    return InterviewDetailOut(
        id=iv.id,
        status=iv.status,
        messages=[InterviewMessageOut.model_validate(m) for m in msgs],
    )


@router.post("/interviews/{interview_id}/answer", response_model=InterviewQuestionResponse)
def answer(
    interview_id: uuid.UUID,
    body: InterviewAnswerRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> InterviewQuestionResponse:
    try:
        question = submit_answer(
            session, workspace=ctx.workspace, interview_id=interview_id, answer=body.answer
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return InterviewQuestionResponse(question=question)


@router.post("/interviews/{interview_id}/finish", response_model=InterviewFinishResponse)
def finish(
    interview_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> InterviewFinishResponse:
    try:
        stats = finish_interview(session, workspace=ctx.workspace, interview_id=interview_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return InterviewFinishResponse(**stats)
