"""Create the application database if it does not exist.

Reads DATABASE_URL from settings, connects to the server's `postgres` maintenance database,
and issues CREATE DATABASE for the target db. Idempotent. Run once before the first migration:

    uv run python -m scripts.create_database
"""

from __future__ import annotations

import psycopg
from sqlalchemy.engine import make_url

from config import get_settings


def main() -> None:
    url = make_url(get_settings().database_url)
    target_db = url.database
    if not target_db:
        raise SystemExit("DATABASE_URL has no database name")

    # Connect to the maintenance db (CREATE DATABASE cannot run inside a transaction).
    with psycopg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        dbname="postgres",
        autocommit=True,
    ) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (target_db,)
        ).fetchone()
        if exists:
            print(f"database '{target_db}' already exists")
            return
        # Identifier can't be parameterized; target_db comes from our own config, not user input.
        conn.execute(f'CREATE DATABASE "{target_db}"')
        print(f"created database '{target_db}'")


if __name__ == "__main__":
    main()
