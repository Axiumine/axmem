"""Link management of the in-memory MemoryRepository stub."""

from uuid import UUID

from app.models.memory_models import Memory
from tests.integration.stubs.helpers import find_item


def _in_project(memory: Memory, project: str | None) -> bool:
    """True when no project filter is given or the memory is in that project scope."""
    return not project or memory.project == project


def _is_linkable(
    memory: Memory | None, project: str | None, excluded: set[int],
) -> bool:
    """True for a live (not obsolete), non-excluded memory inside the project filter."""
    if memory is None or memory.is_obsolete or memory.id in excluded:
        return False
    return _in_project(memory, project)


class MemoryLinkMixin:
    """Bidirectional memory links; expects ``_memories``/``_links`` from the host."""

    _memories: dict[UUID, dict[int, Memory]]
    _links: dict[int, set[int]]

    async def create_links_batch(
        self, user_id: UUID, source_id: int, target_ids: list[int],
    ) -> list[int]:
        """Create bidirectional links between memories"""
        source = find_item(self._memories, user_id, source_id)
        if not target_ids or not source:
            return []

        created_links = []
        for target_id in target_ids:
            target = find_item(self._memories, user_id, target_id)
            # Skip self-links and targets that do not exist
            if target_id == source_id or not target:
                continue

            source_links = self._links.setdefault(source_id, set())
            target_links = self._links.setdefault(target_id, set())

            if target_id not in source_links:
                source_links.add(target_id)
                target_links.add(source_id)

                # Update linked_memory_ids in both memories
                source.linked_memory_ids.append(target_id)
                target.linked_memory_ids.append(source_id)

                created_links.append(target_id)

        return created_links

    async def get_linked_memories(
        self,
        user_id: UUID,
        memory_id: int,
        project: str | None,
        max_links: int = 5,
        exclude_ids: list[int] | None = None,
    ) -> list[Memory]:
        """Get linked memories (1-hop neighbors)"""
        excluded = set(exclude_ids or ())
        memories = []
        for linked_id in self._links.get(memory_id, set()):
            memory = find_item(self._memories, user_id, linked_id)
            # Obsolete memories are soft-deleted and never returned
            if _is_linkable(memory, project, excluded):
                memories.append(memory)

            if len(memories) >= max_links:
                break

        return memories

    async def unlink_memories(
        self,
        user_id: UUID,
        source_id: int,
        target_id: int,
    ) -> bool:
        """Remove bidirectional link between two memories"""
        if target_id not in self._links.get(source_id, set()):
            return False

        # Remove from link tracking
        self._links[source_id].discard(target_id)
        self._links[target_id].discard(source_id)

        # Update linked_memory_ids in both memories
        source = find_item(self._memories, user_id, source_id)
        target = find_item(self._memories, user_id, target_id)

        if source and target_id in source.linked_memory_ids:
            source.linked_memory_ids.remove(target_id)
        if target and source_id in target.linked_memory_ids:
            target.linked_memory_ids.remove(source_id)

        return True
