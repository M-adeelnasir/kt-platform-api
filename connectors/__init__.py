"""Connector framework (plan §9): one OAuthConnector interface; new sources are plugins.

Admin connects on the employee's behalf; capture is manual ("Sync now"). GitHub and the rest
slot in by registering here.
"""

from __future__ import annotations

from connectors.atlassian import AtlassianConnector
from connectors.base import NormalizedDoc, OAuthConnector, SyncStats
from connectors.github import GitHubConnector
from connectors.google import GoogleConnector
from connectors.microsoft import MicrosoftConnector

_REGISTRY: dict[str, type[OAuthConnector]] = {
    "google": GoogleConnector,
    "github": GitHubConnector,
    "atlassian": AtlassianConnector,
    "microsoft": MicrosoftConnector,
}


def get_connector(source_type: str) -> OAuthConnector:
    cls = _REGISTRY.get(source_type)
    if cls is None:
        raise ValueError(f"unknown connector type: {source_type}")
    return cls()


def available_connectors() -> list[str]:
    return list(_REGISTRY.keys())


__all__ = [
    "NormalizedDoc",
    "OAuthConnector",
    "SyncStats",
    "available_connectors",
    "get_connector",
]
