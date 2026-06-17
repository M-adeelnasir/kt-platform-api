"""FastAPI application entrypoint.

Run locally:
    uv run uvicorn app.main:app --reload
OpenAPI schema (for the generated TS client) is served at /openapi.json.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.observability import init_sentry
from app.routers import (
    connectors,
    dashboard,
    employees,
    health,
    insights,
    interviews,
    jobs,
    knowledge,
    workspace,
)
from config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    init_sentry()

    app = FastAPI(
        title="Continuity API",
        version="0.1.0",
        description="KT Platform backend — ingestion, RAG, interview, synthesis, evals.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(workspace.router)
    app.include_router(jobs.router)
    app.include_router(knowledge.router)
    app.include_router(employees.router)
    app.include_router(connectors.router)
    app.include_router(interviews.router)
    app.include_router(dashboard.router)
    app.include_router(insights.router)
    return app


app = create_app()
