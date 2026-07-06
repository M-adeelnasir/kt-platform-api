"""Seed sample employees, including one on notice whose KT we run.

Idempotent (keyed by email). Run after migrations:
    uv run python -m scripts.seed_employees
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.employees import set_employee_status
from data.db import session_scope
from data.repositories import EmployeeRepository, WorkspaceRepository

# (name, email, title, github_username, status)
SEED_EMPLOYEES = [
    (
        "Adeel Nasir",
        "adnasirkbw@gmail.com",
        "Senior Backend Engineer",
        "adeelnasir",
        "on_notice",
    ),
    ("Sara Khan", "sara.khan@example.com", "Product Manager", None, "active"),
    ("John Doe", "john.doe@example.com", "DevOps Engineer", "johndoe", "active"),
    ("Mei Lin", "mei.lin@example.com", "Data Scientist", "meilin", "active"),
    ("Carlos Reyes", "carlos.reyes@example.com", "Frontend Engineer", "creyes", "departed"),
]


def seed_employees(session: Session) -> None:
    workspace = WorkspaceRepository(session).get_default()
    if workspace is None:
        raise SystemExit("No workspace seeded. Run `uv run python -m data.seed` first.")

    repo = EmployeeRepository(session, workspace.id)
    for name, email, title, gh, status in SEED_EMPLOYEES:
        existing = repo.get_by_email(email)
        if existing is not None:
            print(f"exists: {email} ({existing.status})")
            continue
        emp = repo.create(name=name, email=email, title=title, github_username=gh, status="active")
        # Use the lifecycle service so on_notice auto-creates the KB.
        if status != "active":
            set_employee_status(session, workspace.id, emp.id, status)
        print(f"created: {email} -> {status}")


def main() -> None:
    with session_scope() as session:
        seed_employees(session)


if __name__ == "__main__":
    main()
