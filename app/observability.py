"""Sentry initialization. No-op when SENTRY_DSN is unset (local dev)."""

from __future__ import annotations

from config import get_settings


def init_sentry() -> None:
    settings = get_settings()
    if not settings.sentry_dsn:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        # Conservative default sampling; tune in Phase 2 with OpenTelemetry.
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
