"""In-memory MemoryRepository stub (CRUD, listing and re-embedding no-ops)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.models.memory_models import Memory, MemoryCreate, MemoryUpdate
from app.protocols.memory_protocol import MemoryRepository
from tests.integration.stubs.helpers import (
    filter_items,
    find_item,
    patch_item,
    provenance_kwargs,
    shares_any,
    store_item,
)
from tests.integration.stubs.memory_links import MemoryLinkMixin
from tests.integration.stubs.memory_search import MemorySearchMixin

_LIST_SORT_KEYS = {
    "created_at": lambda m: m.created_at,
    "updated_at": lambda m: m.updated_at,
    "importance": lambda m: m.importance,
}


def sort_memories(
    memories: list[Memory], sort_by: str, sort_order: str,
) -> list[Memory]:
    """Sort in place; importance ties fall back to newest-first."""
    descending = sort_order == "desc"
    if sort_by == "importance":
        memories.sort(key=lambda m: (m.created_at, m.id), reverse=True)
    key = _LIST_SORT_KEYS.get(sort_by, _LIST_SORT_KEYS["created_at"])
    memories.sort(key=key, reverse=descending)
    return memories


class InMemoryMemoryRepository(MemorySearchMixin, MemoryLinkMixin, MemoryRepository):
    """In-memory implementation of MemoryRepository for testing"""

    def __init__(self):
        self._memories: dict[
            UUID, dict[int, Memory],
        ] = {}  # user_id -> {memory_id -> Memory}
        self._links: dict[int, set[int]] = {}  # memory_id -> set of linked memory_ids
        self._next_id = 1

    async def create_memory(self, user_id: UUID, memory: MemoryCreate) -> Memory:
        """Create a new memory"""
        memory_id = self._next_id
        self._next_id += 1

        now = datetime.now(UTC) + timedelta(microseconds=memory_id)

        new_memory = Memory(
            id=memory_id,
            title=memory.title,
            content=memory.content,
            context=memory.context,
            keywords=memory.keywords,
            tags=memory.tags,
            importance=memory.importance,
            project_ids=memory.project_ids or [],
            linked_memory_ids=[],
            **provenance_kwargs(memory),
            created_at=now,
            updated_at=now,
        )

        self._links[memory_id] = set()
        return store_item(self._memories, user_id, memory_id, new_memory)

    async def get_memory_by_id(self, user_id: UUID, memory_id: int) -> Memory | None:
        """Retrieve memory by ID"""
        return find_item(self._memories, user_id, memory_id)

    async def update_memory(
        self,
        user_id: UUID,
        memory_id: int,
        updated_memory: MemoryUpdate,
        existing_memory: Memory,
        search_fields_changed: bool,
    ) -> Memory | None:
        """Update an existing memory"""
        memory = await self.get_memory_by_id(user_id, memory_id)
        if not memory:
            return None

        return patch_item(memory, updated_memory.model_dump(exclude_unset=True))

    async def mark_obsolete(
        self,
        user_id: UUID,
        memory_id: int,
        reason: str,
        superseded_by: int | None = None,
    ) -> bool:
        """Mark memory as obsolete (soft delete)"""
        memory = await self.get_memory_by_id(user_id, memory_id)
        if not memory:
            return False

        # Mark as obsolete but keep in storage for audit trail
        memory.is_obsolete = True
        memory.obsolete_reason = reason
        memory.superseded_by = superseded_by
        memory.obsoleted_at = datetime.now(UTC)

        return True

    async def restore_obsolete(self, user_id: UUID, memory_id: int) -> bool:
        memory = await self.get_memory_by_id(user_id, memory_id)
        if not memory or not memory.is_obsolete:
            return False
        memory.is_obsolete = False
        memory.obsolete_reason = None
        memory.superseded_by = None
        memory.obsoleted_at = None
        return True

    async def list_memories(
        self,
        user_id: UUID,
        limit: int,
        offset: int = 0,
        project_ids: list[int] | None = None,
        include_obsolete: bool = False,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        tags: list[str] | None = None,
        importance_min: int | None = None,
        created_since: datetime | None = None,
    ) -> tuple[list[Memory], int]:
        """Get memories with pagination, sorting, and filtering"""
        memories = filter_items(
            self._memories.get(user_id, {}).values(),
            (not include_obsolete, lambda m: not m.is_obsolete),
            (project_ids, shares_any("project_ids", project_ids or [])),
            (tags, shares_any("tags", tags or [])),
            (importance_min is not None, lambda m: m.importance >= importance_min),
            (created_since is not None, lambda m: m.created_at >= created_since),
        )
        sort_memories(memories, sort_by, sort_order)

        # Total is counted before pagination
        return memories[offset : offset + limit], len(memories)

    # Re-embedding support stubs

    async def count_all_memories(self) -> int:
        return sum(len(user_mems) for user_mems in self._memories.values())

    async def get_memories_for_reembedding(
        self, limit: int, offset: int,
    ) -> list[Memory]:
        all_memories = [
            m for user_mems in self._memories.values()
            for m in user_mems.values()
        ]
        all_memories.sort(key=lambda m: m.id)
        return all_memories[offset:offset + limit]

    async def reset_embedding_storage(self) -> None:
        pass  # no-op for in-memory

    async def bulk_update_embeddings(self, updates) -> None:
        pass  # no-op for in-memory

    async def validate_embedding_count(self) -> bool:
        return True

    async def validate_embedding_dimensions(self) -> bool:
        return True

    async def validate_search_works(self) -> bool:
        return True
