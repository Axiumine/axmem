"""Search and similarity behaviour of the in-memory MemoryRepository stub."""

from uuid import UUID

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import Memory, MemoryScore
from tests.integration.stubs.helpers import filter_items


def keyword_overlap(first: Memory, second: Memory) -> int:
    """Number of keywords two memories share (the stub's similarity proxy)."""
    return len(set(first.keywords) & set(second.keywords))


def take_within_distance(
    ranked: list[tuple[Memory, float]], max_distance: float, max_links: int,
) -> list[tuple[Memory, float]]:
    """Keep ranked matches within ``max_distance`` until ``max_links`` are kept."""
    kept: list[tuple[Memory, float]] = []
    for candidate, similarity in ranked:
        if (1.0 - similarity) <= max_distance:
            kept.append((candidate, similarity))
        if len(kept) >= max_links:
            break
    return kept


class MemorySearchMixin:
    """search / similarity methods; expects ``self._memories`` from the host class."""

    _memories: dict[UUID, dict[int, Memory]]

    async def search(
        self,
        user_id: UUID,
        query: str,
        query_context: str,
        k: int,
        importance_threshold: int | None,
        project: str | None,
        exclude_ids: list[int] | None = None,
    ) -> list[Memory]:
        """Mock semantic search - returns memories sorted by importance"""
        memories = filter_items(
            self._memories.get(user_id, {}).values(),
            (True, lambda m: not m.is_obsolete),
            (importance_threshold, lambda m: m.importance >= importance_threshold),
            (project, lambda m: m.project == project),
            (exclude_ids, lambda m: m.id not in exclude_ids),
        )

        # Sort by importance (higher first) then by created_at (newer first)
        memories.sort(key=lambda m: (m.importance, m.created_at), reverse=True)

        return memories[:k]

    async def search_scored(
        self,
        user_id: UUID,
        query: str,
        query_context: str,
        k: int,
        importance_threshold: int | None,
        project: str | None,
        exclude_ids: list[int] | None = None,
    ):
        result = await self.search(
            user_id=user_id,
            query=query,
            query_context=query_context,
            k=k,
            importance_threshold=importance_threshold,
            project=project,
            exclude_ids=exclude_ids,
        )
        return [
            (m, MemoryScore(memory_id=m.id, similarity=1.0 - 0.01 * i))
            for i, m in enumerate(result)
        ]

    async def find_similar_memories(
        self, user_id: UUID, memory_id: int, max_links: int,
    ) -> list[Memory]:
        """Find similar memories - uses keyword overlap as proxy for similarity"""
        user_memories = self._memories.get(user_id, {})

        source = user_memories.get(memory_id)
        if not source:
            return []

        # Every other live memory sharing at least one keyword with the source
        similar = [
            m
            for m in user_memories.values()
            if m.id != memory_id
            and not m.is_obsolete
            and keyword_overlap(source, m) > 0
        ]

        # Sort by importance
        similar.sort(key=lambda m: m.importance, reverse=True)

        return similar[:max_links]

    async def find_similar_memories_scored(
        self,
        user_id: UUID,
        memory_id: int,
        max_links: int,
    ):
        user_memories = self._memories.get(user_id, {})

        source = user_memories.get(memory_id)
        if not source:
            raise NotFoundError(f"Memory {memory_id} not found")

        ranked: list[tuple[Memory, float]] = []
        for candidate in user_memories.values():
            overlap = keyword_overlap(source, candidate)
            if candidate.id != memory_id and not candidate.is_obsolete and overlap > 0:
                distance = max(0.02, 0.15 / overlap)
                ranked.append((candidate, 1.0 - distance))

        ranked.sort(key=lambda item: -item[1])

        max_distance = 1 - settings.MEMORY_SIMILARITY_THRESHOLD
        return take_within_distance(ranked, max_distance, max_links)

    async def find_obsolete_matches(
        self,
        user_id: UUID,
        memory_id: int,
        limit: int,
        min_similarity: float,
    ):
        user_memories = self._memories.get(user_id, {})
        source = user_memories.get(memory_id)
        if not source:
            return []

        ranked: list[tuple[Memory, float]] = []
        for candidate in user_memories.values():
            if candidate.id == memory_id or not candidate.is_obsolete:
                continue
            distance = 0.05 if keyword_overlap(source, candidate) > 0 else 0.5
            similarity = 1.0 - distance
            if similarity >= min_similarity:
                ranked.append((candidate, similarity))

        ranked.sort(key=lambda item: -item[1])
        return ranked[:limit]
