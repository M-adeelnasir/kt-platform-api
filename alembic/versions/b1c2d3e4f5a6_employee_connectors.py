"""employee connectors

Revision ID: b1c2d3e4f5a6
Revises: 6a03f1a09e9c
Create Date: 2026-07-01 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "6a03f1a09e9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep a DB-level default of [] so inserts that omit the column (older code paths) still work.
    op.add_column(
        "employees",
        sa.Column(
            "connectors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("employees", "connectors")
