"""Vector search, reranking and similarity lookups for the SQLite memory repository"""
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

import sqlite_vec
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import Memory, MemoryScore
from app.repositories.embeddings.reranker_adapter import RerankAdapter
from app.repositories.sqlite.memory_base import SqliteMemoryBase
from app.repositories.sqlite.memory_queries import (
    fetch_owned_embedding,
    load_memories_in_order,
)
from app.utils.embedding_helpers import build_contextual_query, build_memory_text

# vec_memories is joined to memories so that ownership and state filters apply to the hits
_SEMANTIC_SEARCH_SELECT = """
    SELECT m.id, m.user_id, m.title, m.content, m.context, m.keywords, m.tags,
           m.importance, m.is_obsolete, m.obsolete_reason, m.superseded_by,
           m.obsoleted_at, m.created_at, m.updated_at,
           vec_distance_cosine(vm.embedding, :query_embedding) AS distance
    FROM memories m
    INNER JOIN vec_memories vm ON m.id = vm.memory_id
    WHERE m.user_id = :user_id AND m.is_obsolete = 0
"""

_PROJECT_FILTER = """
    AND EXISTS (
        SELECT 1 FROM memory_project_association mpa
        WHERE mpa.memory_id = m.id
        AND mpa.project_id IN ({})
    )
"""

_ORDER_BY_DISTANCE = """
    ORDER BY distance
    LIMIT :k
"""

_SIMILAR_MEMORIES_SQL = """
    SELECT m.id, vec_distance_cosine(vm.embedding, :source_embedding) AS distance
    FROM memories m
    INNER JOIN vec_memories vm ON m.id = vm.memory_id
    WHERE m.user_id = :user_id
      AND m.is_obsolete = 0
      AND m.id != :memory_id
      AND vec_distance_cosine(vm.embedding, :source_embedding) <= :max_distance
    ORDER BY distance
    LIMIT :max_links
"""

_OBSOLETE_MATCHES_SQL = """
    SELECT m.id, vec_distance_cosine(vm.embedding, :source_embedding) AS distance
    FROM memories m
    INNER JOIN vec_memories vm ON m.id = vm.memory_id
    WHERE m.user_id = :user_id AND m.is_obsolete = 1 AND m.id != :memory_id
      AND vec_distance_cosine(vm.embedding, :source_embedding) <= :max_distance
    ORDER BY distance
    LIMIT :limit
"""


def _bind_list(prefix: str, values: list[int], params: dict[str, Any]) -> str:
    """Add the values to params as prefix_0, prefix_1, ... and return the matching placeholders."""
    for i, value in enumerate(values):
        params[f"{prefix}_{i}"] = value
    return ",".join(f":{prefix}_{i}" for i in range(len(values)))


def _build_semantic_search_query(
    user_id: UUID,
    embedding_bytes: bytes,
    k: int,
    importance_threshold: int | None,
    project_ids: list[int] | None,
    exclude_ids: list[int] | None,
) -> tuple[str, dict[str, Any]]:
    """SQL and parameters of the nearest-memories query, with the optional filters applied."""
    sql_parts = [_SEMANTIC_SEARCH_SELECT]
    params: dict[str, Any] = {"user_id": str(user_id), "query_embedding": embedding_bytes, "k": k}

    if importance_threshold:
        sql_parts.append(" AND m.importance >= :importance_threshold")
        params["importance_threshold"] = importance_threshold

    if project_ids:
        sql_parts.append(
            _PROJECT_FILTER.format(_bind_list("project", project_ids, params)),  # noqa: S608 — safe: builds :named param placeholders, not user input
        )

    if exclude_ids:
        excluded = _bind_list("exclude", exclude_ids, params)
        sql_parts.append(f" AND m.id NOT IN ({excluded})")

    sql_parts.append(_ORDER_BY_DISTANCE)
    return "".join(sql_parts), params


async def _with_similarity(
    session: AsyncSession,
    rows: Sequence[Row[Any]],
) -> list[tuple[Memory, float]]:
    """Memories for (id, cosine distance) rows, in row order, each with its similarity."""
    similarity_by_id = {row[0]: 1.0 - float(row[1]) for row in rows}
    memories = await load_memories_in_order(session, [row[0] for row in rows])
    return [(Memory.model_validate(memory), similarity_by_id[memory.id]) for memory in memories]


class SqliteMemorySearchMixin(SqliteMemoryBase):
    """Semantic search, optional reranking and similar-memory lookups"""

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
        """Search by vector similarity, then optionally rerank candidates."""
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
        """Search by vector similarity, then optionally rerank candidates (with scores)."""
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

        # RERANKING_ENABLED means bootstrap supplied a rerank adapter
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
        """Perform semantic search using vector similarity with sqlite-vec."""
        query_text = query.strip()
        if not query_text:
            # Nothing to embed: providers either return a constant vector (arbitrary hits
            # with real-looking scores) or reject empty input
            return []
        embeddings = await self._generate_embeddings(query_text)

        sql_query, params = _build_semantic_search_query(
            user_id=user_id,
            embedding_bytes=sqlite_vec.serialize_float32(embeddings),
            k=k,
            importance_threshold=importance_threshold,
            project_ids=project_ids,
            exclude_ids=exclude_ids,
        )

        async with self.db_adapter.session(user_id) as session:
            result = await session.execute(text(sql_query), params)
            rows = result.fetchall()

            if not rows:
                return []

            distance_by_id = {row[0]: float(row[-1]) for row in rows}
            memories = await load_memories_in_order(session, [row[0] for row in rows])

            return [
                (Memory.model_validate(memory), distance_by_id[memory.id])
                for memory in memories
            ]

    async def find_similar_memories(self, user_id: UUID, memory_id: int, max_links: int) -> list[Memory]:
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
        async with self.db_adapter.session(user_id) as session:
            source_embedding = await fetch_owned_embedding(session, user_id, memory_id)
            if source_embedding is None:
                raise NotFoundError(f"Memory {memory_id} not found or has no embedding")

            result = await session.execute(
                text(_SIMILAR_MEMORIES_SQL),
                {
                    "user_id": str(user_id),
                    "memory_id": memory_id,
                    "source_embedding": source_embedding,
                    "max_distance": 1 - settings.MEMORY_SIMILARITY_THRESHOLD,
                    "max_links": max_links,
                },
            )
            rows = result.fetchall()
            if not rows:
                return []

            return await _with_similarity(session, rows)

    async def find_obsolete_matches(
        self,
        user_id: UUID,
        memory_id: int,
        limit: int,
        min_similarity: float,
    ) -> list[tuple[Memory, float]]:
        async with self.db_adapter.session(user_id) as session:
            source_embedding = await fetch_owned_embedding(session, user_id, memory_id)
            if source_embedding is None:
                return []

            result = await session.execute(
                text(_OBSOLETE_MATCHES_SQL),
                {
                    "user_id": str(user_id),
                    "memory_id": memory_id,
                    "source_embedding": source_embedding,
                    "max_distance": 1.0 - min_similarity,
                    "limit": limit,
                },
            )
            rows = result.fetchall()
            if not rows:
                return []

            return await _with_similarity(session, rows)
