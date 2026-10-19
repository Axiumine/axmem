"""Memory/project link methods of the in-memory EntityRepository stub."""

from uuid import UUID

from app.exceptions import NotFoundError
from app.models.entity_models import Entity
from tests.integration.stubs.helpers import find_item, require_item


def _add_link(links: dict[int, set[int]], entity_id: int, target_id: int) -> bool:
    """Add target to the entity's link set (created on demand); False if already linked."""
    targets = links.setdefault(entity_id, set())
    if target_id in targets:
        return False
    targets.add(target_id)
    return True


def _drop_link(links: dict[int, set[int]], entity_id: int, target_id: int) -> bool:
    """Remove a link; report whether it existed."""
    if target_id not in links.get(entity_id, set()):
        return False
    links[entity_id].discard(target_id)
    return True


class EntityLinkMixin:
    """Entity <-> memory/project links; expects the stores from the host class."""

    _entities: dict[UUID, dict[int, Entity]]
    _entity_memory_links: dict[int, set[int]]
    _entity_project_links: dict[int, set[int]]
    _memory_titles: dict[int, str]
    _memory_owners: dict[int, UUID]

    def set_memory_title(self, memory_id: int, title: str) -> None:
        """Test helper: register a title for a memory_id (mimics a real Memory row)"""
        self._memory_titles[memory_id] = title

    def register_memory(self, user_id: UUID, memory_id: int) -> None:
        """Test helper: mark memory_id as owned by user_id (mimics a real Memory row)"""
        self._memory_owners[memory_id] = user_id

    def _require_entity(self, user_id: UUID, entity_id: int) -> Entity:
        """Return the user's entity or raise NotFoundError."""
        return require_item(
            find_item(self._entities, user_id, entity_id),
            f"Entity {entity_id} not found",
        )

    async def link_entity_to_memory(
        self, user_id: UUID, entity_id: int, memory_id: int,
    ) -> bool:
        self._require_entity(user_id, entity_id)
        return _add_link(self._entity_memory_links, entity_id, memory_id)

    async def unlink_entity_from_memory(
        self, user_id: UUID, entity_id: int, memory_id: int,
    ) -> bool:
        return _drop_link(self._entity_memory_links, entity_id, memory_id)

    async def link_entity_to_project(
        self, user_id: UUID, entity_id: int, project_id: int,
    ) -> bool:
        # The mock repo does not verify that the project exists
        self._require_entity(user_id, entity_id)
        return _add_link(self._entity_project_links, entity_id, project_id)

    async def unlink_entity_from_project(
        self, user_id: UUID, entity_id: int, project_id: int,
    ) -> bool:
        return _drop_link(self._entity_project_links, entity_id, project_id)

    async def get_entity_memories(
        self, user_id: UUID, entity_id: int,
    ) -> list[tuple[int, str]]:
        """Get all (memory_id, title) pairs linked to a specific entity"""
        self._require_entity(user_id, entity_id)

        memory_ids = self._entity_memory_links.get(entity_id, set())
        return [
            (mid, self._memory_titles.get(mid, f"Memory {mid}")) for mid in memory_ids
        ]

    async def get_memory_entities(
        self, user_id: UUID, memory_id: int,
    ) -> list[tuple[int, str, str]]:
        """Get all (entity_id, name, entity_type) triples linked to a specific memory"""
        user_entities = self._entities.get(user_id, {})
        linked = sorted(
            entity_id
            for entity_id, memory_ids in self._entity_memory_links.items()
            if memory_id in memory_ids and entity_id in user_entities
        )
        if self._memory_owners.get(memory_id) != user_id:
            raise NotFoundError(f"Memory {memory_id} not found")
        return [
            (eid, user_entities[eid].name, user_entities[eid].entity_type)
            for eid in linked
        ]
