"""Integration tests for EntityService entity-to-entity relationships with in-memory stubs.
"""
from uuid import uuid4

import pytest

from app.models.entity_models import (
    EntityCreate,
    EntityRelationshipCreate,
    EntityRelationshipUpdate,
    EntityType,
)


@pytest.mark.asyncio
async def test_create_entity_relationship(test_entity_service):
    user_id = uuid4()

    # Create two entities
    entity1 = await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Entity 1", entity_type=EntityType.ORGANIZATION, tags=[]),
    )
    entity2 = await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Entity 2", entity_type=EntityType.INDIVIDUAL, tags=[]),
    )

    # Create relationship
    relationship_data = EntityRelationshipCreate(
        source_entity_id=entity1.id,
        target_entity_id=entity2.id,
        relationship_type="employs",
        strength=0.9,
        confidence=0.8,
        metadata={"role": "developer"},
    )

    relationship = await test_entity_service.create_entity_relationship(user_id, relationship_data)

    assert relationship.id is not None
    assert relationship.source_entity_id == entity1.id
    assert relationship.target_entity_id == entity2.id
    assert relationship.relationship_type == "employs"
    assert relationship.strength == 0.9
    assert relationship.confidence == 0.8
    assert relationship.metadata == {"role": "developer"}


@pytest.mark.asyncio
async def test_get_entity_relationships(test_entity_service):
    user_id = uuid4()

    # Create entities
    entity1 = await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Entity 1", entity_type=EntityType.ORGANIZATION, tags=[]),
    )
    entity2 = await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Entity 2", entity_type=EntityType.INDIVIDUAL, tags=[]),
    )

    # Create relationship
    await test_entity_service.create_entity_relationship(
        user_id,
        EntityRelationshipCreate(
            source_entity_id=entity1.id,
            target_entity_id=entity2.id,
            relationship_type="employs",
        ),
    )

    # Get relationships
    relationships = await test_entity_service.get_entity_relationships(user_id, entity1.id)

    assert len(relationships) == 1
    assert relationships[0].source_entity_id == entity1.id


@pytest.mark.asyncio
async def test_get_entity_relationships_filter_by_direction(test_entity_service):
    user_id = uuid4()

    # Create entities
    entity1 = await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Entity 1", entity_type=EntityType.ORGANIZATION, tags=[]),
    )
    entity2 = await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Entity 2", entity_type=EntityType.INDIVIDUAL, tags=[]),
    )

    # Create relationship
    await test_entity_service.create_entity_relationship(
        user_id,
        EntityRelationshipCreate(
            source_entity_id=entity1.id,
            target_entity_id=entity2.id,
            relationship_type="employs",
        ),
    )

    # Get outgoing relationships
    outgoing = await test_entity_service.get_entity_relationships(
        user_id, entity1.id, direction="outgoing",
    )
    assert len(outgoing) == 1

    # Get incoming relationships
    incoming = await test_entity_service.get_entity_relationships(
        user_id, entity1.id, direction="incoming",
    )
    assert len(incoming) == 0


@pytest.mark.asyncio
async def test_update_entity_relationship(test_entity_service):
    user_id = uuid4()

    # Create entities and relationship
    entity1 = await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Entity 1", entity_type=EntityType.ORGANIZATION, tags=[]),
    )
    entity2 = await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Entity 2", entity_type=EntityType.INDIVIDUAL, tags=[]),
    )

    relationship = await test_entity_service.create_entity_relationship(
        user_id,
        EntityRelationshipCreate(
            source_entity_id=entity1.id,
            target_entity_id=entity2.id,
            relationship_type="employs",
            strength=0.5,
        ),
    )

    # Update
    update_data = EntityRelationshipUpdate(strength=0.9, metadata={"updated": True})
    updated = await test_entity_service.update_entity_relationship(
        user_id, relationship.id, update_data,
    )

    assert updated.strength == 0.9
    assert updated.metadata == {"updated": True}
    assert updated.relationship_type == "employs"  # Unchanged


@pytest.mark.asyncio
async def test_delete_entity_relationship(test_entity_service):
    user_id = uuid4()

    # Create entities and relationship
    entity1 = await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Entity 1", entity_type=EntityType.ORGANIZATION, tags=[]),
    )
    entity2 = await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Entity 2", entity_type=EntityType.INDIVIDUAL, tags=[]),
    )

    relationship = await test_entity_service.create_entity_relationship(
        user_id,
        EntityRelationshipCreate(
            source_entity_id=entity1.id,
            target_entity_id=entity2.id,
            relationship_type="employs",
        ),
    )

    # Delete
    success = await test_entity_service.delete_entity_relationship(user_id, relationship.id)
    assert success is True

    # Verify deleted
    relationships = await test_entity_service.get_entity_relationships(user_id, entity1.id)
    assert len(relationships) == 0
