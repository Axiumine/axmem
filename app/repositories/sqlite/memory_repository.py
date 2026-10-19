"""Memory repository for SQLite data access operations with sqlite-vec integration

The operations live in mixins grouped by concern (search, writes, reads, links,
re-embedding); SqliteMemoryRepository composes them.
"""
from app.repositories.sqlite.memory_links import SqliteMemoryLinkMixin
from app.repositories.sqlite.memory_reembedding import SqliteMemoryReembeddingMixin
from app.repositories.sqlite.memory_search import SqliteMemorySearchMixin
from app.repositories.sqlite.memory_writes import (
    MAX_EMBEDDING_ATTEMPTS,
    SqliteMemoryWriteMixin,
)

__all__ = ["MAX_EMBEDDING_ATTEMPTS", "SqliteMemoryRepository"]


class SqliteMemoryRepository(
    SqliteMemorySearchMixin,
    SqliteMemoryWriteMixin,
    SqliteMemoryLinkMixin,
    SqliteMemoryReembeddingMixin,
):
    """Repository for Memory entity operations in SQLite with sqlite-vec integration

    Constructed with a SqliteDatabaseAdapter, an EmbeddingsAdapter and an optional
    RerankAdapter (see SqliteMemoryBase).

    Key differences from Postgres:
    - Embeddings stored in separate vec_memories virtual table
    - Vector similarity search uses sqlite-vec's vec_distance_cosine()
    - UUIDs stored as strings
    - No RLS - user isolation via WHERE clauses
    """
