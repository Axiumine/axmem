# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""State and helpers shared by the mixins behind QdrantMemoryRepository"""
from uuid import UUID

from app.models.memory_models import Memory
from app.protocols.embedding_protocol import EmbeddingsAdapter
from app.repositories.embeddings.reranker_adapter import RerankAdapter
from app.repositories.qdrant.payloads import memory_from_point
from app.repositories.qdrant.schema import CollectionNames, QdrantSchema
from app.repositories.qdrant.store import Point, QdrantStore, Scope, Where, any_of

# Attempts an edit or an obsolete toggle makes while concurrent writers keep changing the same memory
MAX_CAS_ATTEMPTS = 5


class QdrantMemoryBase:
    """Collaborators every memory repository mixin works with"""

    def __init__(
            self,
            store: QdrantStore,
            schema: QdrantSchema,
            embedding_adapter: EmbeddingsAdapter,
            rerank_adapter: RerankAdapter | None = None,
    ) -> None:
        self.store = store
        self.schema = schema
        self.names: CollectionNames = schema.names
        self.embedding_adapter = embedding_adapter
        self.rerank_adapter = rerank_adapter

    @property
    def memories(self) -> str:
        """The live memory collection (an alias onto the current generation)."""
        return self.names.live

    @property
    def links(self) -> str:
        return self.names.links

    async def _generate_embeddings(self, text: str) -> list[float]:
        return await self.embedding_adapter.generate_embedding(text=text)

    async def _linked_ids(self, scope: Scope, memory_ids: list[UUID]) -> dict[UUID, list[UUID]]:
        """The ids each memory is linked to (links count in both directions)."""
        linked: dict[UUID, list[UUID]] = {memory_id: [] for memory_id in memory_ids}
        if not memory_ids:
            return linked
        wanted = [str(memory_id) for memory_id in memory_ids]
        touching = Where().either(any_of("a", wanted), any_of("b", wanted))
        async for link in self.store.iterate(scope, self.links, touching, with_payload=["a", "b"]):
            first, second = UUID(link.payload["a"]), UUID(link.payload["b"])
            if first in linked:
                linked[first].append(second)
            if second in linked:
                linked[second].append(first)
        return linked

    async def _memories(self, scope: Scope, points: list[Point]) -> list[Memory]:
        """The memories the points store, in order, each with the ids it is linked to."""
        linked = await self._linked_ids(scope, [point.id for point in points])
        return [memory_from_point(point, linked[point.id]) for point in points]
