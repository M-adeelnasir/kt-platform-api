"""Job endpoints: enqueue the health-check task and read its result (Phase 0 DoD: a Celery
task runs). Auth-gated so it lives behind the workspace context like the rest of the API.
"""

from __future__ import annotations

from celery.result import AsyncResult
from fastapi import APIRouter, Depends

from app.deps import RequestContext, get_request_context
from app.schemas import JobEnqueuedResponse, JobResultResponse
from jobs.celery_app import celery_app
from jobs.tasks import health_check

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/health-check", response_model=JobEnqueuedResponse)
def enqueue_health_check(
    _ctx: RequestContext = Depends(get_request_context),
) -> JobEnqueuedResponse:
    async_result = health_check.delay("hello")
    return JobEnqueuedResponse(task_id=async_result.id)


@router.get("/{task_id}", response_model=JobResultResponse)
def get_job_result(
    task_id: str,
    _ctx: RequestContext = Depends(get_request_context),
) -> JobResultResponse:
    result: AsyncResult = AsyncResult(task_id, app=celery_app)
    ready = result.ready()
    failed = bool(ready and result.failed())
    return JobResultResponse(
        task_id=task_id,
        ready=ready,
        failed=failed,
        result=str(result.result) if ready else None,
    )
