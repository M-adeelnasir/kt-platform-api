"""Namespace-scoped Pinecone wrapper.

Non-negotiable (plan §3): every vector op targets the workspace namespace, and nothing else
in the codebase imports the Pinecone SDK directly.

When no Pinecone API key is configured (early scaffolding / offline dev), this transparently
falls back to an in-memory store with the same interface, so the rest of the app can run and
be tested without the cloud dependency. Swap to real Pinecone by setting PINECONE_API_KEY.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorRecord:
    """A vector to upsert. `id` is the Postgres chunks.id (plan §7)."""

    id: str
    values: list[float]
    metadata: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class VectorMatch:
    id: str
    score: float
    metadata: dict[str, str | int | float | bool]


class VectorStore(Protocol):
    def upsert(self, namespace: str, records: list[VectorRecord]) -> None: ...

    def query(
        self,
        namespace: str,
        vector: list[float],
        top_k: int = 8,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[VectorMatch]: ...

    def delete(self, namespace: str, ids: list[str]) -> None: ...

    def delete_namespace(self, namespace: str) -> None: ...


def _require_namespace(namespace: str) -> None:
    if not namespace:
        # Hard guard: a missing namespace is a tenant-isolation bug, not a no-op.
        raise ValueError("namespace is required for every vector operation")


@dataclass
class _InMemoryRecord:
    values: list[float]
    metadata: dict[str, str | int | float | bool]


class InMemoryVectorStore:
    """Cosine-similarity in-memory store used when Pinecone is not configured.

    Not for production — purely a stand-in so dev/tests run without cloud keys.
    """

    def __init__(self) -> None:
        self._ns: dict[str, dict[str, _InMemoryRecord]] = {}

    def upsert(self, namespace: str, records: list[VectorRecord]) -> None:
        _require_namespace(namespace)
        bucket = self._ns.setdefault(namespace, {})
        for r in records:
            bucket[r.id] = _InMemoryRecord(values=list(r.values), metadata=dict(r.metadata))

    def query(
        self,
        namespace: str,
        vector: list[float],
        top_k: int = 8,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[VectorMatch]:
        _require_namespace(namespace)
        bucket = self._ns.get(namespace, {})
        scored: list[VectorMatch] = []
        for vid, rec in bucket.items():
            if metadata_filter and not _matches_filter(rec.metadata, metadata_filter):
                continue
            scored.append(
                VectorMatch(id=vid, score=_cosine(vector, rec.values), metadata=rec.metadata)
            )
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:top_k]

    def delete(self, namespace: str, ids: list[str]) -> None:
        _require_namespace(namespace)
        bucket = self._ns.get(namespace, {})
        for vid in ids:
            bucket.pop(vid, None)

    def delete_namespace(self, namespace: str) -> None:
        _require_namespace(namespace)
        self._ns.pop(namespace, None)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _matches_filter(metadata: Mapping[str, object], flt: Mapping[str, object]) -> bool:
    return all(metadata.get(k) == v for k, v in flt.items())


class LocalFileVectorStore:
    """File-backed cosine store: one JSON file per namespace under a base directory.

    Used as the dev fallback when Pinecone isn't configured/reachable. Unlike the in-memory
    store, it is shared across processes (API + Celery worker) and survives restarts. Not for
    production — Pinecone takes over the moment a valid PINECONE_API_KEY is set.
    """

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, namespace: str) -> Path:
        # Namespaces are our own `ws_<hex>` strings — safe as filenames.
        return self._base / f"{namespace}.json"

    def _load(self, namespace: str) -> dict[str, dict[str, object]]:
        path = self._path(namespace)
        if not path.exists():
            return {}
        data: dict[str, dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def _save(self, namespace: str, data: dict[str, dict[str, object]]) -> None:
        self._path(namespace).write_text(json.dumps(data), encoding="utf-8")

    def upsert(self, namespace: str, records: list[VectorRecord]) -> None:
        _require_namespace(namespace)
        if not records:
            return
        with self._lock:
            data = self._load(namespace)
            for r in records:
                data[r.id] = {"values": list(r.values), "metadata": dict(r.metadata)}
            self._save(namespace, data)

    def query(
        self,
        namespace: str,
        vector: list[float],
        top_k: int = 8,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[VectorMatch]:
        _require_namespace(namespace)
        with self._lock:
            data = self._load(namespace)
        scored: list[VectorMatch] = []
        for vid, rec in data.items():
            metadata = cast("dict[str, str | int | float | bool]", rec["metadata"])
            values = cast("list[float]", rec["values"])
            if metadata_filter and not _matches_filter(metadata, metadata_filter):
                continue
            scored.append(VectorMatch(id=vid, score=_cosine(vector, values), metadata=metadata))
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:top_k]

    def delete(self, namespace: str, ids: list[str]) -> None:
        _require_namespace(namespace)
        with self._lock:
            data = self._load(namespace)
            for vid in ids:
                data.pop(vid, None)
            self._save(namespace, data)

    def delete_namespace(self, namespace: str) -> None:
        _require_namespace(namespace)
        with self._lock:
            self._path(namespace).unlink(missing_ok=True)


class PineconeVectorStore:
    """Real Pinecone-backed store. Created lazily so the SDK/network is only touched when used."""

    def __init__(self, api_key: str, index_name: str) -> None:
        from pinecone import Pinecone  # imported here to keep import cost off the hot path

        self._pc = Pinecone(api_key=api_key)
        self._index_name = index_name
        self._index = self._pc.Index(index_name)

    def describe(self) -> object:
        """Reachability/auth probe. Raises if the key is invalid or the index is missing.

        Returns the raw IndexModel (not dict-converted — IndexModel isn't a mapping).
        """
        return self._pc.describe_index(self._index_name)

    def upsert(self, namespace: str, records: list[VectorRecord]) -> None:
        _require_namespace(namespace)
        if not records:
            return
        self._index.upsert(
            namespace=namespace,
            vectors=[{"id": r.id, "values": r.values, "metadata": r.metadata} for r in records],
        )

    def query(
        self,
        namespace: str,
        vector: list[float],
        top_k: int = 8,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[VectorMatch]:
        _require_namespace(namespace)
        res = self._index.query(
            namespace=namespace,
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter=metadata_filter or None,
        )
        return [
            VectorMatch(id=m["id"], score=float(m["score"]), metadata=dict(m.get("metadata") or {}))
            for m in res.get("matches", [])
        ]

    def delete(self, namespace: str, ids: list[str]) -> None:
        _require_namespace(namespace)
        if not ids:
            return
        self._index.delete(namespace=namespace, ids=ids)

    def delete_namespace(self, namespace: str) -> None:
        _require_namespace(namespace)
        self._index.delete(namespace=namespace, delete_all=True)


# Module-level singleton so the in-memory store persists across calls within a process.
_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """Return the configured vector store.

    Prefers Pinecone when a key is set AND the index is reachable; otherwise falls back to the
    local file-backed store (with a warning) so dev works without a valid cloud key.
    """
    global _store
    if _store is not None:
        return _store
    settings = get_settings()
    if settings.pinecone_enabled:
        try:
            store = PineconeVectorStore(settings.pinecone_api_key, settings.pinecone_index_name)
            # Cheap reachability/auth probe so we fail over instead of erroring at first use.
            store.describe()
            _store = store
            logger.info("Using Pinecone vector store (index=%s)", settings.pinecone_index_name)
            return _store
        except Exception as exc:  # any failure means fall back to local
            logger.warning(
                "Pinecone unavailable (%s: %s); falling back to local file vector store at %s. "
                "Set a valid PINECONE_API_KEY to use Pinecone.",
                type(exc).__name__,
                str(exc)[:120],
                settings.vector_store_path,
            )
    _store = LocalFileVectorStore(settings.vector_store_path)
    return _store
