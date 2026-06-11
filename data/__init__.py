"""Data layer. The ONLY place in the backend that talks to Postgres.

All access goes through repositories that require a `workspace_id` (plan §3, §10).
"""
