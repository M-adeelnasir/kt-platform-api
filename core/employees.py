"""Employee lifecycle service.

The key rule (plan §1): when an employee goes ON NOTICE, the system opens a knowledge base
for them so their knowledge transfer can begin. This is the seam connectors hang off later.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from data.models import Employee, KnowledgeBase
from data.repositories import EmployeeRepository, KnowledgeBaseRepository

ON_NOTICE = "on_notice"


def ensure_kb_for_employee(
    session: Session, workspace_id: uuid.UUID, employee: Employee
) -> KnowledgeBase:
    """Find-or-create the knowledge base linked to an employee."""
    repo = KnowledgeBaseRepository(session, workspace_id)
    existing = repo.get_by_employee(employee.id)
    if existing is not None:
        return existing
    return repo.create(subject_person_name=employee.name, employee_id=employee.id)


def set_employee_status(
    session: Session, workspace_id: uuid.UUID, employee_id: uuid.UUID, status: str
) -> tuple[Employee, KnowledgeBase | None]:
    """Update an employee's status. Going on_notice auto-creates their KB.

    Returns (employee, kb) where kb is the linked knowledge base when on notice (else None).
    """
    if status not in EmployeeRepository.VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")

    repo = EmployeeRepository(session, workspace_id)
    employee = repo.get(employee_id)
    if employee is None:
        raise ValueError(f"employee {employee_id} not found")

    employee.status = status
    session.flush()

    kb: KnowledgeBase | None = None
    if status == ON_NOTICE:
        kb = ensure_kb_for_employee(session, workspace_id, employee)
    else:
        kb = KnowledgeBaseRepository(session, workspace_id).get_by_employee(employee_id)
    return employee, kb
