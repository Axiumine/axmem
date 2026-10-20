"""Memory repository on Qdrant: memories and links as points, every operation scoped to a tenant

The operations live in mixins grouped by concern (search, writes, reads, links, re-embedding);
QdrantMemoryRepository composes them. None of them holds a Qdrant client: they go through the
QdrantStore, which adds the tenant to every query and write (see store.py).
"""
from dataclasses import dataclass

from app.protocols.embedding_protocol import EmbeddingsAdapter
from app.repositories.embeddings.reranker_adapter import RerankAdapter
from app.repositories.qdrant.client import NodePool
from app.repositories.qdrant.memory_links import QdrantMemoryLinkMixin
from app.repositories.qdrant.memory_reembedding import QdrantMemoryReembeddingMixin
from app.repositories.qdrant.memory_search import QdrantMemorySearchMixin
from app.repositories.qdrant.memory_writes import QdrantMemoryWriteMixin
from app.repositories.qdrant.schema import QdrantSchema
from app.repositories.qdrant.store import QdrantStore


class QdrantMemoryRepository(
    QdrantMemorySearchMixin,
    QdrantMemoryWriteMixin,
    QdrantMemoryLinkMixin,
    QdrantMemoryReembeddingMixin,
):
    """Repository for Memory entity operations in Qdrant

    Constructed with a QdrantStore, the QdrantSchema that names and switches the collections, an
    EmbeddingsAdapter and an optional RerankAdapter (see QdrantMemoryBase).

    - A memory is one point: the vector is its embedding, the payload holds everything else
    - Links are points of their own in a second collection
    - Edits and obsolete toggles are compare-and-set writes on the payload's version
    """


@dataclass
class QdrantStack:
    """The Qdrant connections and what is built on them, closed together."""

    pool: NodePool
    store: QdrantStore
    schema: QdrantSchema

    @classmethod
    def from_settings(cls) -> QdrantStack:
        pool = NodePool()
        return cls(pool=pool, store=QdrantStore(pool), schema=QdrantSchema(pool))

    def memory_repository(
        self,
        embedding_adapter: EmbeddingsAdapter,
        rerank_adapter: RerankAdapter | None = None,
    ) -> QdrantMemoryRepository:
        return QdrantMemoryRepository(self.store, self.schema, embedding_adapter, rerank_adapter)

    async def close(self) -> None:
        await self.pool.close()
