"""Semantic search and similarity queries of the Postgres memory repository."""
from typing import cast
from uuid import UUID

from sqlalchemy import select

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import Memory, MemoryScore
from app.repositories.embeddings.reranker_adapter import RerankAdapter
from app.repositories.postgres.memory_base import (
    MemoryRepositoryBase,
    memory_load_options,
    project_membership_filter,
)
from app.repositories.postgres.postgres_tables import MemoryTable
from app.utils.embedding_helpers import build_contextual_query, build_memory_text


class MemorySearchMixin(MemoryRepositoryBase):
    """Vector search over memories, optionally reranked, and nearest-neighbour lookups."""

    async def search(
            self,
            user_id: UUID,
            query: str,
            query_context: str,
            k: int,
            importance_threshold: int | None,
            project_ids: list[int] | None,
            exclude_ids: list[int] | None,
    ) -> list[Memory]:
        return [memory for memory, _ in await self.search_scored(
            user_id=user_id,
            query=query,
            query_context=query_context,
            k=k,
            importance_threshold=importance_threshold,
            project_ids=project_ids,
            exclude_ids=exclude_ids,
        )]

    async def search_scored(
            self,
            user_id: UUID,
            query: str,
            query_context: str,
            k: int,
            importance_threshold: int | None,
            project_ids: list[int] | None,
            exclude_ids: list[int] | None,
    ) -> list[tuple[Memory, MemoryScore]]:
        if settings.RERANKING_ENABLED:
            # Never fetch fewer candidates than the caller asked for.
            candidates_to_return = max(k, settings.DENSE_SEARCH_CANDIDATES)
        else:
            candidates_to_return = k

        dense_candidates = await self.semantic_search_scored(
            user_id=user_id,
            query=query,
            k=candidates_to_return,
            importance_threshold=importance_threshold,
            project_ids=project_ids,
            exclude_ids=exclude_ids,
        )

        if not dense_candidates or not settings.RERANKING_ENABLED or len(dense_candidates) <= k:
            return [
                (m, MemoryScore(memory_id=m.id, similarity=1.0 - d))
                for m, d in dense_candidates
            ]

        documents = []
        for memory, _distance in dense_candidates:
            memory_text = build_memory_text(memory)
            documents.append(memory_text)

        if query_context:
            rerank_query = build_contextual_query(query=query, context=query_context)
        else:
            rerank_query = query

        # The bootstrap wiring supplies the reranker whenever RERANKING_ENABLED is set
        reranker = cast(RerankAdapter, self.rerank_adapter)
        ranked = await reranker.rerank(query=rerank_query, documents=documents)

        return [
            (
                dense_candidates[idx][0],
                MemoryScore(
                    memory_id=dense_candidates[idx][0].id,
                    similarity=1.0 - dense_candidates[idx][1],
                    rerank_score=float(score),
                ),
            )
            for idx, score in ranked[:k]
        ]

    async def semantic_search(
            self,
            user_id: UUID,
            query: str,
            k: int,
            importance_threshold: int | None,
            project_ids: list[int] | None,
            exclude_ids: list[int] | None,
    ) -> list[Memory]:
        return [memory for memory, _ in await self.semantic_search_scored(
            user_id=user_id,
            query=query,
            k=k,
            importance_threshold=importance_threshold,
            project_ids=project_ids,
            exclude_ids=exclude_ids,
        )]

    async def semantic_search_scored(
            self,
            user_id: UUID,
            query: str,
            k: int,
            importance_threshold: int | None,
            project_ids: list[int] | None,
            exclude_ids: list[int] | None,
    ) -> list[tuple[Memory, float]]:
        query_text = query.strip()
        if not query_text:
            # Nothing to embed: providers either return a constant vector (arbitrary hits
            # with real-looking scores) or reject empty input
            return []
        embeddings = await self._generate_embeddings(query_text)
        distance_expr = MemoryTable.embedding.cosine_distance(embeddings)

        stmt = (
            select(MemoryTable, distance_expr.label("distance"))
            .options(*memory_load_options())
            .where(
                MemoryTable.user_id == user_id,
                MemoryTable.is_obsolete.is_(False),
            )
        )

        # Apply filters first to reduce expensive vector call on all memories unless necessary
        if importance_threshold:
            stmt = stmt.where(MemoryTable.importance >= importance_threshold)

        if project_ids:
            stmt = stmt.where(project_membership_filter(project_ids))

        if exclude_ids:
            stmt = stmt.where(MemoryTable.id.not_in(exclude_ids))

        stmt = stmt.order_by(distance_expr).limit(k)

        async with self.db_adapter.session(user_id) as session:
            result = await session.execute(stmt)
            rows = result.all()
            return [(Memory.model_validate(orm), float(dist)) for orm, dist in rows]

    async def find_similar_memories(
            self,
            user_id: UUID,
            memory_id: int,
            max_links: int,
    ) -> list[Memory]:
        return [m for m, _ in await self.find_similar_memories_scored(
            user_id=user_id,
            memory_id=memory_id,
            max_links=max_links,
        )]

    async def find_similar_memories_scored(
            self,
            user_id: UUID,
            memory_id: int,
            max_links: int,
    ) -> list[tuple[Memory, float]]:
        memory_orm = await self.get_memory_table_by_id(user_id=user_id, memory_id=memory_id)

        return await self._nearest_memories(
            user_id=user_id,
            memory_id=memory_id,
            embedding=memory_orm.embedding,
            obsolete=False,
            max_distance=1 - settings.MEMORY_SIMILARITY_THRESHOLD,
            limit=max_links,
        )

    async def find_obsolete_matches(
            self,
            user_id: UUID,
            memory_id: int,
            limit: int,
            min_similarity: float,
    ) -> list[tuple[Memory, float]]:
        try:
            memory_orm = await self.get_memory_table_by_id(user_id=user_id, memory_id=memory_id)
        except NotFoundError:
            return []

        if memory_orm.embedding is None:
            return []

        return await self._nearest_memories(
            user_id=user_id,
            memory_id=memory_id,
            embedding=memory_orm.embedding,
            obsolete=True,
            max_distance=1.0 - min_similarity,
            limit=limit,
        )

    async def _nearest_memories(
            self,
            user_id: UUID,
            memory_id: int,
            embedding: list[float],
            obsolete: bool,
            max_distance: float,
            limit: int,
    ) -> list[tuple[Memory, float]]:
        """Other memories of the user, obsolete or not, within max_distance of embedding.

        Returns (memory, similarity) pairs, nearest first.
        """
        distance_expr = MemoryTable.embedding.cosine_distance(embedding)

        stmt = (
            select(MemoryTable, distance_expr.label("distance"))
            .options(*memory_load_options())
            .where(
                MemoryTable.user_id == user_id,
                MemoryTable.is_obsolete.is_(obsolete),
                MemoryTable.id != memory_id,
                distance_expr <= max_distance,
            )
            .order_by(distance_expr)
            .limit(limit)
        )

        async with self.db_adapter.session(user_id) as session:
            result = await session.execute(stmt)
            rows = result.all()
            return [(Memory.model_validate(orm), 1.0 - float(dist)) for orm, dist in rows]
