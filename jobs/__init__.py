"""Celery jobs package. Ingestion and synthesis run here, decoupled from request handlers."""

from jobs.celery_app import celery_app

__all__ = ["celery_app"]
