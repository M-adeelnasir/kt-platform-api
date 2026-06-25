"""Employee endpoints: list/create employees, view detail (with KB + sources), change status.

Going `on_notice` auto-creates the employee's knowledge base (see core.employees).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.deps import RequestContext, db_session, get_request_context
from app.schemas import (
    EmployeeCreate,
    EmployeeDetailOut,
    EmployeeOut,
    EmployeeStatusUpdate,
    EmployeeUpdate,
    SourceOut,
)
from core.employees import set_employee_status
from data.repositories import (
    EmployeeRepository,
    KnowledgeBaseRepository,
    SourceRepository,
)

router = APIRouter(prefix="/employees", tags=["employees"])


@router.get("", response_model=list[EmployeeOut])
def list_employees(
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> list[EmployeeOut]:
    rows = EmployeeRepository(session, ctx.workspace_id).list()
    return [EmployeeOut.model_validate(e) for e in rows]


@router.post("", response_model=EmployeeOut)
def create_employee(
    body: EmployeeCreate,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> EmployeeOut:
    repo = EmployeeRepository(session, ctx.workspace_id)
    if repo.get_by_email(body.email) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")
    if body.status not in EmployeeRepository.VALID_STATUSES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Bad status")
    employee = repo.create(
        name=body.name,
        email=body.email,
        title=body.title,
        github_username=body.github_username,
        status=body.status,
        connectors=body.connectors,
    )
    # If created directly as on_notice, open their KB immediately.
    if body.status == "on_notice":
        set_employee_status(session, ctx.workspace_id, employee.id, "on_notice")
    session.commit()
    return EmployeeOut.model_validate(employee)


def _detail(session: Session, ctx: RequestContext, employee_id: uuid.UUID) -> EmployeeDetailOut:
    employee = EmployeeRepository(session, ctx.workspace_id).get(employee_id)
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    kb = KnowledgeBaseRepository(session, ctx.workspace_id).get_by_employee(employee_id)
    sources = (
        SourceRepository(session, ctx.workspace_id).list_for_kb(kb.id) if kb is not None else []
    )
    return EmployeeDetailOut(
        employee=EmployeeOut.model_validate(employee),
        knowledge_base_id=kb.id if kb is not None else None,
        sources=[SourceOut.model_validate(s) for s in sources],
    )


@router.get("/{employee_id}", response_model=EmployeeDetailOut)
def get_employee(
    employee_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> EmployeeDetailOut:
    return _detail(session, ctx, employee_id)


@router.patch("/{employee_id}", response_model=EmployeeDetailOut)
def update_employee(
    employee_id: uuid.UUID,
    body: EmployeeUpdate,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> EmployeeDetailOut:
    repo = EmployeeRepository(session, ctx.workspace_id)
    employee = repo.get(employee_id)
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    # Guard the unique email constraint with a friendly error.
    if body.email and body.email != employee.email:
        clash = repo.get_by_email(body.email)
        if clash is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")
    repo.update(
        employee,
        name=body.name,
        email=body.email,
        title=body.title,
        github_username=body.github_username,
        connectors=body.connectors,
    )
    session.commit()
    return _detail(session, ctx, employee_id)


@router.post("/{employee_id}/status", response_model=EmployeeDetailOut)
def change_status(
    employee_id: uuid.UUID,
    body: EmployeeStatusUpdate,
    ctx: RequestContext = Depends(get_request_context),
    session: Session = Depends(db_session),
) -> EmployeeDetailOut:
    try:
        set_employee_status(session, ctx.workspace_id, employee_id, body.status)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    session.commit()
    return _detail(session, ctx, employee_id)
