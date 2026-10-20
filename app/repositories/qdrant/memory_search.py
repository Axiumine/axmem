"""Vector search, reranking and similarity lookups for the Qdrant memory repository"""
from typing import cast
from uuid import UUID

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import Memory, MemoryScore
from app.repositories.embeddings.reranker_adapter import RerankAdapter
from app.repositories.qdrant.memory_base import QdrantMemoryBase
from app.repositories.qdrant.payloads import memory_from_point
from app.repositories.qdrant.store import (
    Point,
    Scope,
    Where,
    at_least,
    equals,
    has_ids,
)
from app.utils.embedding_helpers import build_contextual_query, build_memory_text


def _search_filter(
    importance_threshold: int | None,
    project: str | None,
    exclude_ids: list[UUID] | None,
) -> Where:
    """Conditions of a search besides the tenant: active memories only, narrowed by the optional filters."""
    where = Where().also(equals("is_obsolete", False))
    if importance_threshold:
        where = where.also(at_least("importance", importance_threshold))
    if project:
        where = where.also(equals("project", project))
    if exclude_ids:
        where = where.excluding(has_ids(exclude_ids))
    return where


class QdrantMemorySearchMixin(QdrantMemoryBase):
    """Semantic search, optional reranking and similar-memory lookups"""

    async def search(
        self,
        user_id: UUID,
        query: str,
        query_context: str,
        k: int,
        importance_threshold: int | None,
        project: str | None,
        exclude_ids: list[UUID] | None,
    ) -> list[Memory]:
        """Search by vector similarity, then optionally rerank candidates."""
        return [memory for memory, _ in await self.search_scored(
            user_id=user_id,
            query=query,
            query_context=query_context,
            k=k,
            importance_threshold=importance_threshold,
            project=project,
            exclude_ids=exclude_ids,
        )]

    async def search_scored(
        self,
        user_id: UUID,
        query: str,
        query_context: str,
        k: int,
        importance_threshold: int | None,
        project: str | None,
        exclude_ids: list[UUID] | None,
    ) -> list[tuple[Memory, MemoryScore]]:
        """Search by vector similarity, then optionally rerank candidates (with scores)."""
        scope = Scope.of(user_id)
        # Never fetch fewer candidates than the caller asked for when reranking
        wanted = max(k, settings.DENSE_SEARCH_CANDIDATES) if settings.RERANKING_ENABLED else k
        candidates = await self._dense_candidates(
            scope, query, wanted, _search_filter(importance_threshold, project, exclude_ids),
        )
        chosen = await self._rerank(candidates, query, query_context, k)

        memories = await self._memories(scope, [point for point, _, _ in chosen])
        return [
            (memory, MemoryScore(memory_id=memory.id, similarity=similarity, rerank_score=rerank_score))
            for memory, (_, similarity, rerank_score) in zip(memories, chosen, strict=True)
        ]

    async def _rerank(
        self, candidates: list[Point], query: str, query_context: str, k: int,
    ) -> list[tuple[Point, float, float | None]]:
        """The ``k`` best of the dense candidates with their similarity and, when reranked, their rerank score."""
        if not candidates or not settings.RERANKING_ENABLED or len(candidates) <= k:
            return [(point, point.score, None) for point in candidates]
        documents = [build_memory_text(memory_from_point(point)) for point in candidates]
        rerank_query = build_contextual_query(query=query, context=query_context) if query_context else query
        # RERANKING_ENABLED means bootstrap supplied a rerank adapter
        reranker = cast(RerankAdapter, self.rerank_adapter)
        ranked = await reranker.rerank(query=rerank_query, documents=documents)
        return [(candidates[index], candidates[index].score, float(score)) for index, score in ranked[:k]]

    async def _dense_candidates(self, scope: Scope, query: str, limit: int, where: Where) -> list[Point]:
        query_text = query.strip()
        if not query_text:
            # Nothing to embed: providers either return a constant vector (arbitrary hits
            # with real-looking scores) or reject empty input
            return []
        vector = await self._generate_embeddings(query_text)
        return await self.store.search(scope, self.memories, query=vector, where=where, limit=limit)

    async def find_similar_memories(self, user_id: UUID, memory_id: UUID, max_links: int) -> list[Memory]:
        return [m for m, _ in await self.find_similar_memories_scored(
            user_id=user_id,
            memory_id=memory_id,
            max_links=max_links,
        )]

    async def find_similar_memories_scored(
        self,
        user_id: UUID,
        memory_id: UUID,
        max_links: int,
    ) -> list[tuple[Memory, float]]:
        """The active memories most similar to a stored one, at or above MEMORY_SIMILARITY_THRESHOLD.

        Raises:
            NotFoundError: If the tenant has no such memory
        """
        scope = Scope.of(user_id)
        vector = await self._stored_vector(scope, memory_id)
        if vector is None:
            raise NotFoundError(f"Memory {memory_id} not found or has no embedding")
        return await self._similar_to(
            scope,
            memory_id,
            vector,
            Where().also(equals("is_obsolete", False)),
            limit=max_links,
            min_similarity=settings.MEMORY_SIMILARITY_THRESHOLD,
        )

    async def find_obsolete_matches(
        self,
        user_id: UUID,
        memory_id: UUID,
        limit: int = 3,
        min_similarity: float = 0.89,
    ) -> list[tuple[Memory, float]]:
        """The obsolete memories most similar to a stored one, at or above ``min_similarity``."""
        scope = Scope.of(user_id)
        vector = await self._stored_vector(scope, memory_id)
        if vector is None:
            return []
        return await self._similar_to(
            scope,
            memory_id,
            vector,
            Where().also(equals("is_obsolete", True)),
            limit=limit,
            min_similarity=min_similarity,
        )

    async def _stored_vector(self, scope: Scope, memory_id: UUID) -> list[float] | None:
        """The stored embedding of a memory the tenant owns, or None.

        The lookups that call this follow the write that created the memory, and a request may reach another node
        than the write did, so a majority of the replicas answers (a replica that has not caught up cannot hide it).
        """
        points = await self.store.get(
            scope, self.memories, [memory_id], with_payload=False, with_vectors=True, quorum=True,
        )
        return points[0].vector if points else None

    async def _similar_to(
        self,
        scope: Scope,
        memory_id: UUID,
        vector: list[float],
        where: Where,
        limit: int,
        min_similarity: float,
    ) -> list[tuple[Memory, float]]:
        points = await self.store.search(
            scope,
            self.memories,
            query=vector,
            where=where.excluding(has_ids([memory_id])),
            limit=limit,
            score_threshold=min_similarity,
        )
        memories = await self._memories(scope, points)
        return [(memory, point.score) for memory, point in zip(memories, points, strict=True)]
