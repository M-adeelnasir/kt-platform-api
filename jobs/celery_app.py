"""Celery application wired to local Redis (broker + result backend).

Run a worker with:
    uv run celery -A jobs.celery_app worker --loglevel=info --pool=solo
(`--pool=solo` on Windows; the default prefork pool isn't supported there.)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the api/ root is importable in the worker regardless of launch cwd (the package is
# run from source, not pip-installed). Without this, tasks that import core/ai/data can fail
# with "No module named 'core'" depending on how Celery resolves sys.path.
_API_ROOT = str(Path(__file__).resolve().parent.parent)
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)

from celery import Celery  # noqa: E402  (after sys.path bootstrap)

from config import get_settings  # noqa: E402

_settings = get_settings()

celery_app = Celery(
    "continuity",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=["jobs.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    result_expires=3600,
    timezone="UTC",
    enable_utc=True,
)

# Auto-ingest: a Beat-scheduled poller re-syncs every connected source on an interval.
# Run with `celery -A jobs.celery_app worker --beat --pool=solo` (embedded beat for dev).
celery_app.conf.beat_schedule = {
    "poll-connected-sources": {
        "task": "jobs.poll_sources",
        "schedule": float(_settings.sync_poll_seconds),
    }
}
