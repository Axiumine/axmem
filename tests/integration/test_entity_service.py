"""Integration tests for EntityService CRUD and memory linking with in-memory stubs.
"""
from uuid import uuid4

import pytest

from app.exceptions import NotFoundError
from app.models.entity_models import (
    EntityCreate,
    EntityType,
    EntityUpdate,
)


@pytest.mark.asyncio
async def test_create_entity(test_entity_service):
    user_id = uuid4()

    entity_data = EntityCreate(
        name="Acme Corp",
        entity_type=EntityType.ORGANIZATION,
        notes="A software company",
        tags=["tech", "b2b"],
    )

    entity = await test_entity_service.create_entity(user_id, entity_data)

    assert entity.id is not None
    assert entity.name == "Acme Corp"
    assert entity.entity_type == EntityType.ORGANIZATION
    assert entity.notes == "A software company"
    assert entity.tags == ["tech", "b2b"]


@pytest.mark.asyncio
async def test_create_entity_with_custom_type(test_entity_service):
    user_id = uuid4()

    entity_data = EntityCreate(
        name="IoT Sensor",
        entity_type=EntityType.OTHER,
        custom_type="Sensor",
        tags=["hardware"],
    )

    entity = await test_entity_service.create_entity(user_id, entity_data)

    assert entity.id is not None
    assert entity.entity_type == EntityType.OTHER
    assert entity.custom_type == "Sensor"


@pytest.mark.asyncio
async def test_create_entity_with_system_type(test_entity_service):
    """Test creating an entity with the System type and round-tripping via get_entity"""
    user_id = uuid4()

    entity_data = EntityCreate(
        name="auth-service",
        entity_type=EntityType.SYSTEM,
        notes="Handles authentication for the platform",
        tags=["backend"],
    )

    entity = await test_entity_service.create_entity(user_id, entity_data)

    assert entity.id is not None
    assert entity.entity_type == EntityType.SYSTEM
    assert entity.custom_type is None

    retrieved = await test_entity_service.get_entity(user_id, entity.id)

    assert retrieved.entity_type == EntityType.SYSTEM


@pytest.mark.asyncio
async def test_create_entity_with_lowercase_system_type_is_case_insensitive(test_entity_service):
    """Test that entity_type="system" (lowercase) resolves to EntityType.SYSTEM"""
    user_id = uuid4()

    entity_data = EntityCreate(
        name="billing-db",
        entity_type="system",
        tags=[],
    )

    entity = await test_entity_service.create_entity(user_id, entity_data)

    assert entity.entity_type == EntityType.SYSTEM

    retrieved = await test_entity_service.get_entity(user_id, entity.id)

    assert retrieved.entity_type == EntityType.SYSTEM


@pytest.mark.asyncio
async def test_get_entity(test_entity_service):
    user_id = uuid4()

    # Create
    entity_data = EntityCreate(
        name="Test Entity",
        entity_type=EntityType.INDIVIDUAL,
        tags=[],
    )
    created = await test_entity_service.create_entity(user_id, entity_data)

    # Get
    retrieved = await test_entity_service.get_entity(user_id, created.id)

    assert retrieved.id == created.id
    assert retrieved.name == "Test Entity"


@pytest.mark.asyncio
async def test_get_entity_not_found(test_entity_service):
    user_id = uuid4()

    with pytest.raises(NotFoundError):
        await test_entity_service.get_entity(user_id, 999)


@pytest.mark.asyncio
async def test_list_entities(test_entity_service):
    user_id = uuid4()

    # Create multiple entities
    for i in range(3):
        entity_data = EntityCreate(
            name=f"Entity {i}",
            entity_type=EntityType.ORGANIZATION,
            tags=["test"],
        )
        await test_entity_service.create_entity(user_id, entity_data)

    # List
    entities, total = await test_entity_service.list_entities(user_id)

    assert len(entities) == 3
    assert total == 3


@pytest.mark.asyncio
async def test_list_entities_filter_by_type(test_entity_service):
    user_id = uuid4()

    # Create different types
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Company", entity_type=EntityType.ORGANIZATION, tags=[]),
    )

    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Person", entity_type=EntityType.INDIVIDUAL, tags=[]),
    )

    # Filter by organization
    orgs, total = await test_entity_service.list_entities(user_id, entity_type=EntityType.ORGANIZATION)

    assert len(orgs) == 1
    assert total == 1
    assert orgs[0].entity_type == EntityType.ORGANIZATION


@pytest.mark.asyncio
async def test_list_entities_filter_by_tags(test_entity_service):
    user_id = uuid4()

    # Create with different tags
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Dev Team", entity_type=EntityType.TEAM, tags=["engineering"]),
    )

    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Sales Team", entity_type=EntityType.TEAM, tags=["sales"]),
    )

    # Filter by engineering tag
    eng_entities, total = await test_entity_service.list_entities(user_id, tags=["engineering"])

    assert len(eng_entities) == 1
    assert total == 1
    assert "engineering" in eng_entities[0].tags


@pytest.mark.asyncio
async def test_update_entity(test_entity_service):
    user_id = uuid4()

    # Create
    entity_data = EntityCreate(
        name="Original Name",
        entity_type=EntityType.ORGANIZATION,
        notes="Original notes",
        tags=["original"],
    )
    created = await test_entity_service.create_entity(user_id, entity_data)

    # Update
    update_data = EntityUpdate(name="Updated Name", notes="New notes")
    updated = await test_entity_service.update_entity(user_id, created.id, update_data)

    assert updated.name == "Updated Name"
    assert updated.notes == "New notes"
    assert updated.tags == ["original"]  # Unchanged


@pytest.mark.asyncio
async def test_delete_entity(test_entity_service):
    user_id = uuid4()

    # Create
    entity_data = EntityCreate(
        name="To Delete",
        entity_type=EntityType.ORGANIZATION,
        tags=[],
    )
    created = await test_entity_service.create_entity(user_id, entity_data)

    # Delete
    success = await test_entity_service.delete_entity(user_id, created.id)
    assert success is True

    # Verify deleted
    with pytest.raises(NotFoundError):
        await test_entity_service.get_entity(user_id, created.id)


@pytest.mark.asyncio
async def test_link_entity_to_memory(test_entity_service):
    user_id = uuid4()

    # Create entity
    entity_data = EntityCreate(
        name="Test Entity",
        entity_type=EntityType.ORGANIZATION,
        tags=[],
    )
    entity = await test_entity_service.create_entity(user_id, entity_data)

    # Link to memory (memory_id = 1)
    success = await test_entity_service.link_entity_to_memory(user_id, entity.id, 1)
    assert success is True


@pytest.mark.asyncio
async def test_unlink_entity_from_memory(test_entity_service):
    user_id = uuid4()

    # Create entity
    entity_data = EntityCreate(
        name="Test Entity",
        entity_type=EntityType.ORGANIZATION,
        tags=[],
    )
    entity = await test_entity_service.create_entity(user_id, entity_data)

    # Link
    await test_entity_service.link_entity_to_memory(user_id, entity.id, 1)

    # Unlink
    success = await test_entity_service.unlink_entity_from_memory(user_id, entity.id, 1)
    assert success is True
