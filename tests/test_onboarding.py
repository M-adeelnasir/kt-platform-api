"""Unit tests for the successor onboarding reading-path logic (no DB / no LLM)."""

from __future__ import annotations

from core.onboarding import ARTIFACT_ORDER, _ordered, _read_minutes
from data.models import KnowledgeItem


def test_read_minutes_rounds_up_and_has_floor() -> None:
    assert _read_minutes("") == 1  # never zero
    assert _read_minutes("word " * 50) == 1
    assert _read_minutes("word " * 200) == 1
    assert _read_minutes("word " * 201) == 2
    assert _read_minutes("word " * 450) == 3


def test_ordered_follows_reading_path() -> None:
    # Synthesis emits in overview/gotcha/glossary/gap order; the reading path reorders so the
    # successor learns vocabulary (glossary) before the landmines (gotcha).
    items = [KnowledgeItem(kind=k) for k in ("gap", "gotcha", "glossary", "overview")]
    assert [i.kind for i in _ordered(items)] == ARTIFACT_ORDER
    assert ARTIFACT_ORDER == ["overview", "glossary", "gotcha", "gap"]


def test_ordered_puts_unknown_kinds_last() -> None:
    items = [KnowledgeItem(kind="mystery"), KnowledgeItem(kind="overview")]
    assert [i.kind for i in _ordered(items)] == ["overview", "mystery"]
