"""Dashboard aggregation (workspace overview). All metrics derive from real data; the live
"active jobs" list is a best-effort Celery inspect (empty if no worker / broker hiccup).
"""

from __future__ import annotations

import datetime
import logging
import uuid
from datetime import UTC, timedelta

from sqlalchemy.orm import Session

from app.schemas import (
    DashboardConnector,
    DashboardJob,
    DashboardKpis,
    DashboardOut,
    DashboardPerson,
)
from data.repositories import (
    DocumentRepository,
    EmployeeRepository,
    InterviewRepository,
    KnowledgeBaseRepository,
    KnowledgeItemRepository,
    SourceRepository,
)

logger = logging.getLogger(__name__)

CONNECTOR_TYPES = ["google", "github", "atlassian", "microsoft"]
_TASK_KINDS = {
    "jobs.synthesize": "synthesis",
    "jobs.sync_source": "sync",
    "jobs.ingest_text": "ingest",
}


def _capture_progress(session: Session, workspace_id: uuid.UUID, kb_id: uuid.UUID) -> int:
    """Milestone heuristic (0-100): connected source, documents, synthesized, interviewed."""
    milestones = [
        any(
            s.status == "connected"
            for s in SourceRepository(session, workspace_id).list_for_kb(kb_id)
        ),
        len(DocumentRepository(session, workspace_id).list_for_kb(kb_id)) > 0,
        len(KnowledgeItemRepository(session, workspace_id).list_for_kb(kb_id)) > 0,
        len(InterviewRepository(session, workspace_id).list_for_kb(kb_id)) > 0,
    ]
    return round(100 * sum(milestones) / len(milestones))


def build_dashboard(session: Session, workspace_id: uuid.UUID) -> DashboardOut:
    emp_repo = EmployeeRepository(session, workspace_id)
    kb_repo = KnowledgeBaseRepository(session, workspace_id)
    item_repo = KnowledgeItemRepository(session, workspace_id)

    employees = list(emp_repo.list())
    on_notice = [e for e in employees if e.status == "on_notice"]
    kbs = list(kb_repo.list())
    kbs_ready = sum(1 for kb in kbs if len(item_repo.list_for_kb(kb.id)) > 0)

    soon_cutoff = datetime.datetime.now(UTC) + timedelta(days=30)
    departing_soon = sum(
        1 for e in on_notice if e.notice_end_date is not None and e.notice_end_date <= soon_cutoff
    )

    # On-notice people with capture progress + their KB.
    persons: list[DashboardPerson] = []
    for e in on_notice:
        kb = kb_repo.get_by_employee(e.id)
        persons.append(
            DashboardPerson(
                employee_id=e.id,
                name=e.name,
                title=e.title,
                notice_end_date=e.notice_end_date,
                knowledge_base_id=kb.id if kb else None,
                capture_progress=_capture_progress(session, workspace_id, kb.id) if kb else 0,
            )
        )

    # Connector health, aggregated across the workspace.
    all_sources = list(SourceRepository(session, workspace_id).list_all())
    connectors: list[DashboardConnector] = []
    needs_attention = 0
    for ctype in CONNECTOR_TYPES:
        of_type = [s for s in all_sources if s.type == ctype]
        connected = [s for s in of_type if s.status == "connected"]
        last_synced = max(
            (s.last_synced_at for s in of_type if s.last_synced_at is not None),
            default=None,
        )
        if not of_type:
            status = "not_connected"
        elif connected:
            status = "connected"
        else:
            status = "action_needed"
            needs_attention += 1
        connectors.append(
            DashboardConnector(
                type=ctype,
                status=status,
                employee_count=len(connected),
                last_synced_at=last_synced,
            )
        )

    active_jobs = _active_jobs(session, workspace_id)

    kpis = DashboardKpis(
        on_notice=len(on_notice),
        departing_soon=departing_soon,
        kbs_ready=kbs_ready,
        kbs_total=len(kbs),
        jobs_running=len(active_jobs),
        needs_attention=needs_attention,
    )
    return DashboardOut(
        kpis=kpis, on_notice=persons, connectors=connectors, active_jobs=active_jobs
    )


def _active_jobs(session: Session, workspace_id: uuid.UUID) -> list[DashboardJob]:
    """Best-effort: inspect Celery for running tasks. Empty if no worker / unreachable."""
    try:
        from jobs.celery_app import celery_app

        active = celery_app.control.inspect(timeout=1.5).active() or {}
    except Exception as exc:  # broker down / no worker — not an error for the dashboard
        logger.debug("dashboard active-jobs inspect failed: %s", exc)
        return []

    jobs: list[DashboardJob] = []
    for tasks in active.values():
        for t in tasks or []:
            kind = _TASK_KINDS.get(t.get("name", ""))
            if not kind:
                continue
            jobs.append(
                DashboardJob(
                    id=str(t.get("id", "")),
                    kind=kind,
                    subject=_job_subject(session, workspace_id, kind, t.get("args") or []),
                )
            )
    return jobs


def _job_subject(
    session: Session, workspace_id: uuid.UUID, kind: str, args: list[object]
) -> str | None:
    """Resolve a running task's subject person from its args, best-effort."""
    try:
        kb_repo = KnowledgeBaseRepository(session, workspace_id)
        if kind in ("synthesis", "ingest") and len(args) >= 2:
            kb = kb_repo.get(uuid.UUID(str(args[1])))
            return kb.subject_person_name if kb else None
        if kind == "sync" and len(args) >= 2:
            src = SourceRepository(session, workspace_id).get(uuid.UUID(str(args[1])))
            if src:
                kb = kb_repo.get(src.knowledge_base_id)
                return kb.subject_person_name if kb else None
    except Exception:
        return None
    return None
