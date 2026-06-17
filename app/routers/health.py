"""Unauthenticated liveness endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas import HealthResponse
from config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", environment=settings.environment)
