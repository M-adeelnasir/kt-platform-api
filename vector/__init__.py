"""Vector store access. The ONLY place that talks to Pinecone (plan §10).

Every operation is scoped to a namespace (one namespace == one workspace).
"""

from vector.client import VectorMatch, VectorRecord, VectorStore, get_vector_store

__all__ = ["VectorMatch", "VectorRecord", "VectorStore", "get_vector_store"]
