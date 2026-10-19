"""Relationship methods of the in-memory EntityRepository stub."""

from datetime import UTC, datetime
from uuid import UUID

from app.exceptions import NotFoundError
from app.models.entity_models import (
    Entity,
    EntityRelationship,
    EntityRelationshipCreate,
    EntityRelationshipUpdate,
)
from tests.integration.stubs.helpers import (
    attr_equals,
    filter_items,
    find_item,
    newest_first,
    patch_item,
    provenance_kwargs,
    remove_item,
    require_item,
    store_item,
)


def _touches_entity(entity_id: int, direction: str | None):
    """Predicate for relationships leaving, entering or touching an entity."""
    if direction == "outgoing":
        return lambda r: r.source_entity_id == entity_id
    if direction == "incoming":
        return lambda r: r.target_entity_id == entity_id
    return lambda r: entity_id in (r.source_entity_id, r.target_entity_id)


class EntityRelationshipMixin:
    """Relationship CRUD; expects the entity/relationship stores from the host."""

    _entities: dict[UUID, dict[int, Entity]]
    _relationships: dict[UUID, dict[int, EntityRelationship]]
    _next_relationship_id: int

    async def create_entity_relationship(
        self, user_id: UUID, relationship_data: EntityRelationshipCreate,
    ) -> EntityRelationship:
        # Verify both entities exist
        source = find_item(
            self._entities, user_id, relationship_data.source_entity_id,
        )
        target = find_item(
            self._entities, user_id, relationship_data.target_entity_id,
        )
        if source is None or target is None:
            raise NotFoundError("Source or target entity not found")

        relationship_id = self._next_relationship_id
        self._next_relationship_id += 1
        now = datetime.now(UTC)

        new_relationship = EntityRelationship(
            id=relationship_id,
            source_entity_id=relationship_data.source_entity_id,
            target_entity_id=relationship_data.target_entity_id,
            relationship_type=relationship_data.relationship_type,
            strength=relationship_data.strength,
            metadata=relationship_data.metadata or {},
            created_at=now,
            updated_at=now,
            **provenance_kwargs(relationship_data),
        )
        return store_item(
            self._relationships, user_id, relationship_id, new_relationship,
        )

    async def get_entity_relationships(
        self,
        user_id: UUID,
        entity_id: int,
        direction: str | None = None,
        relationship_type: str | None = None,
    ) -> list[EntityRelationship]:
        relationships = filter_items(
            self._relationships.get(user_id, {}).values(),
            (True, _touches_entity(entity_id, direction)),
            (relationship_type, attr_equals("relationship_type", relationship_type)),
        )
        return newest_first(relationships)

    async def update_entity_relationship(
        self,
        user_id: UUID,
        relationship_id: int,
        relationship_data: EntityRelationshipUpdate,
    ) -> EntityRelationship:
        relationship = require_item(
            find_item(self._relationships, user_id, relationship_id),
            f"Relationship {relationship_id} not found",
        )
        return patch_item(
            relationship, relationship_data.model_dump(exclude_unset=True),
        )

    async def delete_entity_relationship(
        self, user_id: UUID, relationship_id: int,
    ) -> bool:
        return remove_item(self._relationships, user_id, relationship_id)
