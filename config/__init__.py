"""Central configuration. Nothing else in the backend reads the environment directly."""

from config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
