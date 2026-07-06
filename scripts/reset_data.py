"""DANGER: wipe ALL application data and start fresh (dev only).

Truncates every domain table, clears the Pinecone namespaces, deletes the local fallback
vector store, then re-seeds the single workspace + default member (so the app still works).
Sample employees are NOT re-seeded — you add your fresh test employee yourself.

Run:  uv run python -m scripts.reset_data

Guarded to ENVIRONMENT=development so it can't nuke a real database by accident.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import text

from config import get_settings
from data.db import engine, session_scope
from data.seed import seed_all

# Order doesn't matter with CASCADE, but list every table so nothing is missed.
TABLES = [
    "qa_queries",
    "knowledge_items",
    "interview_messages",
    "interviews",
    "chunks",
    "documents",
    "sources",
    "knowledge_bases",
    "employees",
    "audit_log",
    "members",
    "workspaces",
]


def wipe_postgres() -> None:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    print(f"postgres: truncated {len(TABLES)} tables")


def wipe_pinecone() -> None:
    s = get_settings()
    if not s.pinecone_api_key:
        print("pinecone: no key, skipping")
        return
    try:
        from pinecone import Pinecone

        idx = Pinecone(api_key=s.pinecone_api_key).Index(s.pinecone_index_name)
        stats = idx.describe_index_stats()
        namespaces = list(dict(stats.namespaces).keys())
        for ns in namespaces:
            idx.delete(delete_all=True, namespace=ns)
        print(f"pinecone: cleared {len(namespaces)} namespace(s): {namespaces or '(none)'}")
    except Exception as exc:  # don't let a vector-store hiccup block the reset
        print(f"pinecone: skipped ({type(exc).__name__}: {exc})")


def wipe_local_vectors() -> None:
    p = Path(get_settings().vector_store_path)
    if p.exists():
        shutil.rmtree(p)
        print(f"local vectors: removed {p}")
    else:
        print("local vectors: nothing to remove")


def main() -> None:
    settings = get_settings()
    if settings.environment != "development":
        raise SystemExit(
            f"Refusing to reset: ENVIRONMENT={settings.environment!r} (only 'development')."
        )

    print("=== RESETTING ALL DATA (dev) ===")
    wipe_postgres()
    wipe_pinecone()
    wipe_local_vectors()

    with session_scope() as session:
        ws, member = seed_all(session)
        print(
            f"reseeded workspace {ws.id} ({ws.name}), namespace={ws.pinecone_namespace}, "
            f"member={member.external_user_id}"
        )
    print("=== DONE — fresh slate (no employees; add your test employee in the UI) ===")


if __name__ == "__main__":
    main()
