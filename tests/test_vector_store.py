"""Vector wrapper unit tests — no cloud dependency (exercises the in-memory fallback)."""

from __future__ import annotations

import pytest

from vector.client import InMemoryVectorStore, VectorRecord


def test_namespace_is_required() -> None:
    store = InMemoryVectorStore()
    with pytest.raises(ValueError):
        store.upsert("", [VectorRecord(id="a", values=[1.0, 0.0], metadata={})])


def test_upsert_and_query_ranks_by_cosine() -> None:
    store = InMemoryVectorStore()
    ns = "ws_test"
    store.upsert(
        ns,
        [
            VectorRecord(id="near", values=[1.0, 0.0], metadata={"document_id": "d1"}),
            VectorRecord(id="far", values=[0.0, 1.0], metadata={"document_id": "d2"}),
        ],
    )
    matches = store.query(ns, vector=[1.0, 0.0], top_k=2)
    assert [m.id for m in matches] == ["near", "far"]
    assert matches[0].score > matches[1].score


def test_metadata_filter() -> None:
    store = InMemoryVectorStore()
    ns = "ws_test"
    store.upsert(
        ns,
        [
            VectorRecord(id="a", values=[1.0, 0.0], metadata={"document_id": "d1"}),
            VectorRecord(id="b", values=[1.0, 0.0], metadata={"document_id": "d2"}),
        ],
    )
    matches = store.query(ns, vector=[1.0, 0.0], metadata_filter={"document_id": "d2"})
    assert [m.id for m in matches] == ["b"]


def test_namespaces_are_isolated() -> None:
    store = InMemoryVectorStore()
    store.upsert("ws_a", [VectorRecord(id="x", values=[1.0], metadata={})])
    assert store.query("ws_b", vector=[1.0]) == []
