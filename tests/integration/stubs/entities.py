"""In-memory EntityRepository stub (entity CRUD, listing and search)."""

from datetime import UTC, datetime
from uuid import UUID

from app.models.entity_models import (
    Entity,
    EntityCreate,
    EntityRelationship,
    EntitySummary,
    EntityType,
    EntityUpdate,
)
from app.protocols.entity_protocol import EntityRepository
from tests.integration.stubs.entity_links import EntityLinkMixin
from tests.integration.stubs.entity_relationships import EntityRelationshipMixin
from tests.integration.stubs.helpers import (
    attr_equals,
    filter_items,
    find_item,
    newest_first,
    patch_item,
    provenance_kwargs,
    remove_item,
    require_item,
    shares_any,
    store_item,
)


def _matches_search(entity: Entity, search_lower: str) -> bool:
    """Case-insensitive substring match on the entity name or any alias."""
    return search_lower in entity.name.lower() or any(
        search_lower in alias.lower() for alias in entity.aka
    )


class InMemoryEntityRepository(
    EntityLinkMixin, EntityRelationshipMixin, EntityRepository,
):
    """In-memory implementation of EntityRepository for testing"""

    def __init__(self):
        self._entities: dict[UUID, dict[int, Entity]] = {}
        self._relationships: dict[UUID, dict[int, EntityRelationship]] = {}
        self._entity_memory_links: dict[
            int, set[int],
        ] = {}  # entity_id -> set of memory_ids
        self._entity_project_links: dict[
            int, set[int],
        ] = {}  # entity_id -> set of project_ids
        self._memory_titles: dict[int, str] = {}  # memory_id -> title
        self._memory_owners: dict[int, UUID] = {}  # memory_id -> owning user_id
        self._next_entity_id = 1
        self._next_relationship_id = 1

    async def create_entity(self, user_id: UUID, entity_data: EntityCreate) -> Entity:
        entity_id = self._next_entity_id
        self._next_entity_id += 1
        now = datetime.now(UTC)

        new_entity = Entity(
            id=entity_id,
            name=entity_data.name,
            entity_type=entity_data.entity_type,
            custom_type=entity_data.custom_type,
            notes=entity_data.notes,
            tags=entity_data.tags,
            aka=entity_data.aka,
            project_ids=entity_data.project_ids or [],
            created_at=now,
            updated_at=now,
            **provenance_kwargs(entity_data),
        )

        self._entity_memory_links[entity_id] = set()
        return store_item(self._entities, user_id, entity_id, new_entity)

    async def get_entity_by_id(self, user_id: UUID, entity_id: int) -> Entity | None:
        return find_item(self._entities, user_id, entity_id)

    async def list_entities(
        self,
        user_id: UUID,
        project_ids: list[int] | None = None,
        entity_type: EntityType | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[EntitySummary], int]:
        entities = filter_items(
            self._entities.get(user_id, {}).values(),
            (project_ids, shares_any("project_ids", project_ids or [])),
            (entity_type, attr_equals("entity_type", entity_type)),
            (tags, shares_any("tags", tags or [])),
        )

        # Newest first, then by id for deterministic ordering
        entities = newest_first(entities, key=lambda e: (e.created_at, e.id))
        page = entities[offset : offset + limit]

        # The total is counted before pagination
        return [EntitySummary.model_validate(e) for e in page], len(entities)

    async def search_entities(
        self,
        user_id: UUID,
        search_query: str,
        entity_type: EntityType | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[EntitySummary]:
        """Search entities by name or aka using case-insensitive text matching"""
        search_lower = search_query.lower()
        entities = filter_items(
            self._entities.get(user_id, {}).values(),
            (True, lambda e: _matches_search(e, search_lower)),
            (entity_type, attr_equals("entity_type", entity_type)),
            (tags, shares_any("tags", tags or [])),
        )

        return [EntitySummary.model_validate(e) for e in newest_first(entities)[:limit]]

    async def update_entity(
        self, user_id: UUID, entity_id: int, entity_data: EntityUpdate,
    ) -> Entity:
        entity = require_item(
            await self.get_entity_by_id(user_id, entity_id),
            f"Entity {entity_id} not found",
        )
        return patch_item(entity, entity_data.model_dump(exclude_unset=True))

    async def delete_entity(self, user_id: UUID, entity_id: int) -> bool:
        deleted = remove_item(self._entities, user_id, entity_id)
        if deleted:
            self._entity_memory_links.pop(entity_id, None)
            # Drop relationships where this entity is involved
            user_relationships = self._relationships.get(user_id, {})
            for rid in [
                rid
                for rid, rel in user_relationships.items()
                if entity_id in (rel.source_entity_id, rel.target_entity_id)
            ]:
                del user_relationships[rid]
        return deleted
